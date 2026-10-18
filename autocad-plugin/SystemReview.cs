using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.Colors;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.GraphicsInterface;
using Autodesk.AutoCAD.Runtime;
using System.Diagnostics;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Application;

namespace Maritime.AutoCAD;

public sealed partial class PreviewCommand
{
    private const string RepairRelease = "real-model-batch-20261018";

    [CommandMethod("MARITIMEVERSION", CommandFlags.Modal)]
    public static void ShowRepairVersion()
    {
        var editor = AcApplication.DocumentManager.MdiActiveDocument?.Editor;
        if (editor is null) return;
        editor.WriteMessage($"\nMaritime plugin: {RepairRelease}");
        try
        {
            using var client = CreateClient();
            client.Timeout = TimeSpan.FromSeconds(5);
            var health = SendServiceJson(client, HttpMethod.Get, "/api/health");
            CheckService(client, "endpoint_contact_evidence");
            var release = health["release"]?.GetValue<string>() ?? "older release";
            editor.WriteMessage($"\nLocal service: {release}");
            editor.WriteMessage(release == RepairRelease
                ? "\nPlugin and service release match. Ready for the real-model batch tests."
                : "\nRelease mismatch. Update both pipe_router and autocad-plugin, restart Python and AutoCAD, then reload the DLL.");
        }
        catch (System.Exception error) { editor.WriteMessage($"\nService check failed: {error.Message}\nStart the updated local Python service and retry MARITIMEVERSION."); }
    }

    private const int MaximumCoordinatedSections = 12;
    private static readonly Dictionary<Database, string> LatestReports = new();
    private static readonly Dictionary<Database, (string Url, string CadId, string Hash)> PendingReports = new();
    private sealed record SectionReview(string CandidateId, JsonObject Metadata,
        List<MeshPart> Proposal, List<MeshPart> Reference, List<Point3d> Centerline, List<ReviewDimension> Dimensions);

    [DllImport("user32.dll")]
    private static extern short GetAsyncKeyState(int virtualKey);

    [CommandMethod("MARITIMEREPORT", CommandFlags.Modal)]
    public static void OpenLatestReport()
    {
        var document = AcApplication.DocumentManager.MdiActiveDocument;
        if (document is null) return;
        var fingerprint = document.Database.FingerprintGuid.ToString();
        try
        {
            if (PendingReports.TryGetValue(document.Database, out var pending))
            {
                using var client = CreateClient();
                client.Timeout = TimeSpan.FromSeconds(15);
                var result = SendServiceJson(client, HttpMethod.Get, pending.Url);
                var state = Text(result, "status");
                if (state is "pending" or "running")
                {
                    document.Editor.WriteMessage("\nThe latest review is still running. Try MARITIMEREPORT after the local service finishes.");
                    return;
                }
                if (state != "complete") throw new InvalidOperationException(result["error"]?.GetValue<string>() ?? "The latest review failed.");
                VerifyBatchContext(document, result, pending.CadId, pending.Hash, fingerprint, requireCurrentScan: false);
                if (pending.Url.StartsWith("/api/drawing-jobs/", StringComparison.Ordinal)) CaptureScanResult(document, result);
                RememberReport(document, result);
                PendingReports.Remove(document.Database);
            }
            if (!LatestReports.TryGetValue(document.Database, out var path))
                throw new InvalidOperationException("No report has been created for this drawing in this AutoCAD session. Run MARITIMESCAN, MARITIMEREVIEW, MARITIMEOPTIMIZE or MARITIMECAD first.");
            ValidateReportPath(path);
            Process.Start(new ProcessStartInfo(ServiceUrl + path) { UseShellExecute = true });
            document.Editor.WriteMessage($"\nOpened the latest report for this drawing: {ServiceUrl}{path}\nKeep the Python service running while viewing it.");
        }
        catch (System.Exception exception) { document.Editor.WriteMessage($"\nReport could not be opened: {exception.Message}"); }
    }

    private static void RememberReport(Document document, JsonObject response)
    {
        if (response["exports"]?["report"] is not JsonValue value || !value.TryGetValue<string>(out var path)) return;
        ValidateReportPath(path);
        EnsureDrawingSessionCleanup();
        LatestReports[document.Database] = path;
        PendingReports.Remove(document.Database);
    }

    private static void ValidateReportPath(string path)
    {
        // The local service may return data, but may not choose an arbitrary program or website to launch.
        if (!Regex.IsMatch(path, "^/downloads/[a-fA-F0-9]{32}/report\\.html$", RegexOptions.CultureInvariant))
            throw new InvalidOperationException("The service returned an unsupported local report path.");
    }

    private static string SourceHandleText(JsonNode candidate) => candidate["source_handles"] is JsonArray handles
        ? string.Join(", ", handles.Select(item => item?.GetValue<string>() ?? "?")) : "not supplied";

    private static List<int> ParseSectionNumbers(string value, int count, int maximum)
    {
        var tokens = value.Split(new[] { ',', ' ', ';' }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (tokens.Length == 0 || tokens.Length > maximum)
            throw new InvalidOperationException($"Enter between 1 and {maximum} route numbers separated by commas.");
        var numbers = new List<int>();
        foreach (var token in tokens)
        {
            if (!int.TryParse(token, out var number) || number < 1 || number > count)
                throw new InvalidOperationException($"Each number must be between 1 and {count}.");
            if (numbers.Contains(number)) throw new InvalidOperationException("Each route may be selected only once.");
            numbers.Add(number);
        }
        return numbers;
    }

    private static void RunCoordinatedSelection(Document document, HttpClient client, JsonArray candidates,
        string cadId, string snapshotHash, string fingerprint, double drawingScale, string system,
        string profile, double clearance, double padding, double grid, bool reverse, double slope)
    {
        var editor = document.Editor;
        RequireCurrentScanSnapshot(document, cadId, snapshotHash);
        CheckService(client, "endpoint_contact_evidence");
        List<int> selected;
        while (true)
        {
            var answer = editor.GetString(new PromptStringOptions("\nRoute numbers to coordinate, separated by commas (for example 16,17): ") { AllowSpaces = true });
            if (answer.Status != PromptStatus.OK) return;
            try { selected = ParseSectionNumbers(answer.StringResult, candidates.Count, MaximumCoordinatedSections); break; }
            catch (InvalidOperationException exception) { editor.WriteMessage($"\n{exception.Message}"); }
        }
        editor.WriteMessage($"\nCurrent settings: search padding {padding:G} mm; required clearance {clearance:G} mm; grid step {grid:G} mm.");
        editor.WriteMessage("\nPadding controls how far the route search can explore around each selected section. It is NOT clearance, a hull boundary or an approved routing space. Increasing padding can include more surrounding obstacles; none are dropped to make a route pass.");
        if (!AskNumber(editor, "Search padding for this optimization in mm (Enter keeps the value shown)", padding, false, out padding)) return;
        editor.WriteMessage($"\nUsing search padding {padding:G} mm for this run. Required clearance remains {clearance:G} mm. The earlier scan report retains its original screening settings.");
        var sections = new JsonArray();
        var expectedLayouts = new Dictionary<string, JsonObject>();
        var selectedCandidates = new Dictionary<string, JsonNode>();
        foreach (var number in selected)
        {
            var candidate = candidates[number - 1]?.DeepClone() as JsonObject ?? throw new InvalidOperationException("An empty candidate was selected.");
            candidate["selection_number"] = number;
            var candidateId = Text(candidate, "id");
            editor.WriteMessage($"\nSection {number}: {Text(candidate, "label")}; source handles {SourceHandleText(candidate)}.");
            var section = new JsonObject
            {
                ["candidate_id"] = candidateId,
                ["system_type"] = system == "Gravity" ? "gravity" : "pressurized",
                ["clearance"] = clearance, ["padding"] = padding, ["grid_step"] = grid,
                ["reverse_flow"] = reverse, ["min_slope"] = slope,
                ["rule_profile"] = profile == "Challenge" ? "fmg-v3" : "prototype-unverified"
            };
            // A selected-only import also numbers its local menu, so only send a
            // global scan reference when it actually came from the full inventory.
            if (candidate["global_number"] is JsonValue globalNumber)
                section["source_scan_number"] = globalNumber.DeepClone();
            AddDrawingContext(section, document.Database);
            var prepareRequest = (JsonObject)section.DeepClone();
            prepareRequest["cad_id"] = cadId;
            JsonObject layout;
            try
            {
                layout = RequiredLayout(PostJson(client, "/api/prepare-cad-layout", prepareRequest));
                VerifySnapshotContext(layout, fingerprint, snapshotHash);
            }
            catch (InvalidOperationException exception)
            {
                editor.WriteMessage($"\nThis section could not be prepared: {exception.Message}\nIt will remain in the batch report as a section requiring review.");
                sections.Add(section); selectedCandidates[candidateId] = candidate;
                continue;
            }
            if (!ShowSelectedSectionPreview(document, layout, candidate, drawingScale, number)) return;
            try
            {
                if (!ChooseSectionConnections(document, client, section, prepareRequest, layout, fingerprint, snapshotHash, drawingScale, out var prepared)) return;
                layout = prepared;
                WriteConnectionSelectionReview(editor, layout);
                if (layout["protected_endpoints"] is JsonArray { Count: > 0 })
                {
                    editor.WriteMessage($"\nReview the unchanged fixed connection portions for section {number}. Cyan marks the portions that must be preserved; magenta marks the rest of the original route.");
                    if (!ShowSelectedSectionPreview(document, layout, candidate, drawingScale, number)) return;
                }
            }
            catch (InvalidOperationException exception)
            {
                editor.WriteMessage($"\nThe selected connection assumptions could not be prepared: {exception.Message}\nThis section will remain in the batch report for review; the other selected sections can continue.");
                sections.Add(section); selectedCandidates[candidateId] = candidate;
                continue;
            }
            if (system == "Pressure")
            {
                if (!AskNumber(editor, $"Section {number} bend centerline radius in mm (review assumption)",
                    Math.Max(304.8, 1.5 * Number(layout["pipe"]!, "diameter")), false, out var radius)) return;
                section["bend_radius_mm"] = radius;
            }
            expectedLayouts[candidateId] = layout;
            selectedCandidates[candidateId] = candidate;
            sections.Add(section);
        }
        if (sections.Count == 0) return;
        editor.WriteMessage("\nREVIEW THE ACTIVE SETTINGS BEFORE RUNNING. Search padding does not reduce required clearance. Endpoint approvals apply only to the sections and contact evidence reviewed above.");
        foreach (var section in sections.OfType<JsonObject>())
        {
            var id = Text(section, "candidate_id");
            var number = selectedCandidates[id]["selection_number"];
            var radius = section["bend_radius_mm"] is JsonValue value ? value.ToString() + " mm" : "not set (no rounded pressure proposal prepared)";
            editor.WriteMessage($"\nSection {number}: padding {section["padding"]} mm | clearance {section["clearance"]} mm | grid {section["grid_step"]} mm | bend centerline radius {radius}.");
            if (section["endpoint_connections"] is JsonObject connections)
                editor.WriteMessage($"\n  Explicitly approved endpoint contacts: {connections}. Maximum unchanged connection length: {section["max_stub_length"]} mm.");
            else editor.WriteMessage("\n  No endpoint contacts approved.");
        }
        var confirmation = AskKeyword(editor, $"Coordinate {sections.Count} selected sections with surrounding source geometry", ["Run", "Cancel"], "Run");
        if (confirmation != "Run") return;
        var request = new JsonObject
        {
            ["cad_id"] = cadId, ["source_sha256"] = snapshotHash,
            ["document"] = new JsonObject { ["name"] = Path.GetFileName(document.Name), ["fingerprint"] = fingerprint },
            ["sections"] = sections
        };
        RequireCurrentScanSnapshot(document, cadId, snapshotHash);
        var pending = PostJson(client, "/api/autocad/optimize", request);
        var reply = WaitForSystemJob(document, pending, cadId, snapshotHash);
        if (reply is null) return;
        VerifyBatchContext(document, reply, cadId, snapshotHash, fingerprint);
        RememberReport(document, reply);
        PendingReports.Remove(document.Database);
        var summary = reply["summary"] as JsonObject ?? throw new InvalidOperationException("No coordinated result summary was returned.");
        if (summary["complete_system_verified"]?.GetValue<bool>() != false)
            throw new InvalidOperationException("The service did not identify the scope limits of this review.");
        editor.WriteMessage("\nCoordinated section review finished. The source drawing has not been replaced; only the selected sections were searched.");
        if (summary["counts"] is JsonObject counts)
            foreach (var count in counts) editor.WriteMessage($"\n{count.Key}: {count.Value}");
        if (summary["coverage"] is not null) editor.WriteMessage($"\nCoverage: {summary["coverage"]}");
        WriteMessages(editor, summary["limitations"], "Review limit", 5);
        var reviews = ValidateSectionReviews(summary, expectedLayouts, selectedCandidates, fingerprint, snapshotHash, reply);
        editor.WriteMessage($"\nReport: {ServiceUrl}{Text(reply["exports"]!, "report")}\nUse MARITIMEREPORT to reopen it after this command.");
        if (reviews.Count == 0)
        {
            editor.WriteMessage("\nNo accepted rounded section was returned. No proposal geometry will be saved. The report lists the blocked or unresolved sections.");
            editor.WriteMessage($"\nThis run used search padding {padding:G} mm and clearance {clearance:G} mm. Check the report's search-region evidence. A blocked fixed endpoint needs connection review; more padding does not move that endpoint. For a search-region limit, MARITIMEOPTIMIZE lets you choose new padding and review the resulting contacts again.");
            return;
        }
        ShowSystemReview(document, reviews, drawingScale, () => VerifyBatchContext(document, reply, cadId, snapshotHash, fingerprint));
    }

    private static bool ChooseSectionConnections(Document document, HttpClient client, JsonObject section,
        JsonObject prepareRequest, JsonObject layout, string fingerprint, string snapshotHash, double drawingScale, out JsonObject prepared)
    {
        prepared = layout;
        if (layout["cad_context"]?["endpoint_contacts"] is not JsonArray contacts || contacts.Count == 0) return true;
        var editor = document.Editor;
        editor.WriteMessage("\nEnd contacts may be intended connectors or collisions. Select only the contacts whose connections you have verified.");
        for (var index = 0; index < contacts.Count; index++)
        {
            var contact = contacts[index] ?? throw new InvalidOperationException("An empty contact was returned.");
            editor.WriteMessage($"\nContact {index + 1}: {Text(contact, "endpoint")}, source {Text(contact, "source_handle")} ({Text(contact, "obstacle_id")})");
            if (contact["contact_summary"] is JsonValue summary) editor.WriteMessage($"\n  {summary.GetValue<string>()}");
        }
        List<int> accepted;
        while (true)
        {
            var answer = editor.GetString(new PromptStringOptions("\nEnter INSPECT 1 to locate contact 1 (or INSPECT 2, etc.). Intended fixed contact numbers, comma separated; ALL only if every listed contact is intended; Enter for none: ") { AllowSpaces = true });
            if (answer.Status == PromptStatus.None || answer.Status == PromptStatus.OK && string.IsNullOrWhiteSpace(answer.StringResult)) return true;
            if (answer.Status != PromptStatus.OK) return false;
            var inspect = Regex.Match(answer.StringResult.Trim(), @"^INSPECT\s+(\d+)$", RegexOptions.IgnoreCase);
            if (inspect.Success)
            {
                if (!int.TryParse(inspect.Groups[1].Value, out var contactNumber) || contactNumber < 1 || contactNumber > contacts.Count)
                    editor.WriteMessage($"\nChoose a contact number from 1 to {contacts.Count}.");
                else
                {
                    while (true)
                    {
                        var navigation = InspectSectionContact(document, layout, contacts[contactNumber - 1]!, contactNumber, contacts.Count, drawingScale);
                        if (navigation is null) return false;
                        if (navigation == "Return") break;
                        contactNumber = navigation == "Next" ? Math.Min(contacts.Count, contactNumber + 1) : Math.Max(1, contactNumber - 1);
                    }
                }
                continue;
            }
            if (answer.StringResult.Trim().Equals("ALL", StringComparison.OrdinalIgnoreCase))
            {
                accepted = Enumerable.Range(1, contacts.Count).ToList(); break;
            }
            try { accepted = ParseSectionNumbers(answer.StringResult, contacts.Count, contacts.Count); break; }
            catch (InvalidOperationException exception) { editor.WriteMessage($"\n{exception.Message}"); }
        }
        if (!AskNumber(editor, "Maximum fixed connection length in mm (at most 1000)", 250, false, out var maximum)) return false;
        if (maximum > 1000) throw new InvalidOperationException("Fixed endpoint assumptions are limited to 1000 mm.");
        var connections = new Dictionary<string, List<string>>();
        foreach (var index in accepted)
        {
            var contact = contacts[index - 1]!;
            var endpoint = Text(contact, "endpoint");
            if (endpoint is not ("start" or "end")) throw new InvalidOperationException("Unknown endpoint contact.");
            if (!connections.TryGetValue(endpoint, out var values)) connections[endpoint] = values = [];
            var id = Text(contact, "obstacle_id");
            if (!values.Contains(id)) values.Add(id);
        }
        section["endpoint_connections"] = JsonSerializer.SerializeToNode(connections);
        section["max_stub_length"] = maximum;
        prepareRequest["endpoint_connections"] = section["endpoint_connections"]!.DeepClone();
        prepareRequest["max_stub_length"] = maximum;
        prepared = RequiredLayout(PostJson(client, "/api/prepare-cad-layout", prepareRequest));
        VerifySnapshotContext(prepared, fingerprint, snapshotHash);
        return true;
    }

    private static void WriteConnectionSelectionReview(Editor editor, JsonObject layout)
    {
        if (layout["connection_selection_review"] is not JsonObject review) return;
        editor.WriteMessage($"\nFixed connection check: {review["status"]}. {review["message"]}");
        if (review["endpoints"] is JsonArray endpoints)
            foreach (var endpoint in endpoints)
            {
                if (endpoint?["movable_anchor"] is JsonArray anchor)
                    editor.WriteMessage($"\n  {endpoint["endpoint"]} movable-route anchor (mm): {anchor.ToJsonString()}.");
                if (endpoint?["blockers"] is JsonArray blockers)
                {
                    foreach (var blocker in blockers.Take(6))
                        editor.WriteMessage($"\n  Remaining conflict: source {blocker?["source_handle"] ?? JsonValue.Create("unknown")}, obstacle {blocker?["obstacle_id"]}; affected portions {blocker?["scopes"]}.");
                    if (blockers.Count > 6) editor.WriteMessage($"\n  {blockers.Count - 6} additional conflicts remain in the report.");
                }
            }
        if (review["status"]?.GetValue<string>() == "blocked")
            editor.WriteMessage("\nThe prepared fixed connection still conflicts with other geometry or the required clearance. More search padding does not fix a collision on an unchanged connection. Running records the unresolved result; do not approve unrelated contacts to force acceptance.");
    }

    private static string? InspectSectionContact(Document document, JsonObject layout, JsonNode contact, int number, int total, double scale)
    {
        var editor = document.Editor;
        var obstacle = (layout["obstacles"] as JsonArray)?.FirstOrDefault(item => item?["id"]?.GetValue<string>() == Text(contact, "obstacle_id"));
        if (obstacle is null) throw new InvalidOperationException("The contact envelope is missing; refresh the scan before reviewing it.");
        var minimum = ReadScanPoint(obstacle["min"], scale) ?? throw new InvalidOperationException("Missing contact bounds.");
        var maximum = ReadScanPoint(obstacle["max"], scale) ?? throw new InvalidOperationException("Missing contact bounds.");
        var endpoint = ReadScanPoint(layout["pipe"]?[Text(contact, "endpoint")], scale) ?? throw new InvalidOperationException("Missing contact endpoint.");
        using var previousView = editor.GetCurrentView();
        var manager = TransientManager.CurrentTransientManager;
        var viewports = new IntegerCollection();
        var lines = new List<Line>();
        ObjectId highlighted = ObjectId.Null;
        try
        {
            var handle = Text(contact, "source_handle");
            var nested = contact["instance_path"] is JsonArray { Count: > 0 };
            if (nested) handle = Regex.Match(contact["instance_path"]![0]?.GetValue<string>() ?? "", "^[a-fA-F0-9]+").Value;
            if (long.TryParse(handle, System.Globalization.NumberStyles.AllowHexSpecifier, System.Globalization.CultureInfo.InvariantCulture, out var numeric))
            {
                using var transaction = document.Database.TransactionManager.StartOpenCloseTransaction();
                try
                {
                    var id = document.Database.GetObjectId(false, new Handle(numeric), 0);
                    var blocks = (BlockTable)transaction.GetObject(document.Database.BlockTableId, OpenMode.ForRead);
                    if (!id.IsNull && !id.IsErased && transaction.GetObject(id, OpenMode.ForRead) is Entity entity && entity.OwnerId == blocks[BlockTableRecord.ModelSpace])
                    { entity.Highlight(); highlighted = id; }
                }
                catch (Autodesk.AutoCAD.Runtime.Exception) { }
            }
            var corners = Enumerable.Range(0, 8).Select(i => new Point3d(
                (i & 1) == 0 ? minimum.X : maximum.X,
                (i & 2) == 0 ? minimum.Y : maximum.Y,
                (i & 4) == 0 ? minimum.Z : maximum.Z)).ToArray();
            for (var i = 0; i < 8; i++)
                foreach (var bit in new[] { 1, 2, 4 }) if ((i & bit) == 0) AddLine(corners[i], corners[i | bit], 2);
            var size = Math.Max(25, Number(layout["pipe"]!, "diameter")) / scale;
            foreach (var axis in new[] { Vector3d.XAxis, Vector3d.YAxis, Vector3d.ZAxis })
                AddLine(endpoint - axis * size, endpoint + axis * size, 1);
            // Distinguish the selected path from cyan source wireframe. Clip the
            // overlay to this contact region without altering source entities.
            var original = ReadPoints(layout["pipe"]?["original_route"], "selected section").Select(point => Scale(point, scale)).ToList();
            if (Text(contact, "endpoint") == "end") original.Reverse();
            var reach = Math.Max(minimum.DistanceTo(maximum) * 1.5, size * 5);
            var remaining = reach;
            var focus = corners.Concat(new[] { endpoint }).ToList();
            for (var i = 1; i < original.Count && remaining > 0; i++)
            {
                var a = original[i - 1]; var b = original[i];
                var distance = a.DistanceTo(b);
                if (distance <= 1e-9) continue;
                var finish = distance <= remaining ? b : a + (b - a) * (remaining / distance);
                AddLine(a, finish, 6); focus.Add(finish); remaining -= distance;
            }
            FrameMeshReview(document, focus);
            editor.Regen();
            editor.WriteMessage($"\nContact {number}: {Text(contact, "endpoint")}, source {Text(contact, "source_handle")}. Yellow box locates the component bounds; red cross marks the route endpoint. This is NOT a proposal or proof of a connection.\nEnvelope method: {contact["obstacle_method"]}. Contact can include clearance, not only physical overlap.");
            if (nested) editor.WriteMessage("\nThe owning block is highlighted; the yellow box locates the particular nested component.");
            if (highlighted.IsNull) editor.WriteMessage("\nSource highlighting unavailable. Only recorded bounds are shown; do not infer a verified connection from this box.");
            editor.WriteMessage("\nMAGENTA = selected pipe path near this endpoint. Yellow = contacted component bounds. Red = fixed endpoint. Source geometry is unchanged.");
            editor.WriteMessage("\nInspection markers draw over source geometry; their visibility does not establish clearance or a connection.");
            if (contact["contact_summary"] is JsonValue summary) editor.WriteMessage($"\nContact evidence: {summary.GetValue<string>()}");
            if (contact["stub_preview"] is JsonObject stub)
                editor.WriteMessage($"\nFixed-path preview: {stub["status"]}; required length {stub["required_length_mm"]} mm; current limit {stub["limit_mm"]} mm. This does not approve the contact.");
            return AskKeyword(editor, $"Inspect contact {number}/{total}; Enter returns without approving", ["Next", "Previous", "Return"], "Return");
        }
        finally
        {
            foreach (var line in lines)
            { try { manager.EraseTransient(line, viewports); } catch (Autodesk.AutoCAD.Runtime.Exception) { } line.Dispose(); }
            if (!highlighted.IsNull)
            {
                using var transaction = document.Database.TransactionManager.StartOpenCloseTransaction();
                try { if (!highlighted.IsErased && transaction.GetObject(highlighted, OpenMode.ForRead) is Entity entity) entity.Unhighlight(); }
                catch (Autodesk.AutoCAD.Runtime.Exception) { }
            }
            editor.SetCurrentView(previousView);
            editor.Regen();
        }
        void AddLine(Point3d start, Point3d end, short color)
        {
            var line = new Line(start, end) { ColorIndex = color, LineWeight = LineWeight.LineWeight050 };
            if (manager.AddTransient(line, TransientDrawingMode.DirectTopmost, 128, viewports)) lines.Add(line);
            else line.Dispose();
        }
    }

    private static JsonObject? WaitForSystemJob(Document document, JsonObject pending, string cadId, string snapshotHash)
    {
        if (Number(pending, "api_version") != 1) throw new InvalidOperationException("Unsupported coordinated-search protocol.");
        var jobId = Text(pending, "job_id");
        var statusUrl = Text(pending, "status_url");
        if (!Regex.IsMatch(jobId, "^[a-fA-F0-9]{32}$") || statusUrl != $"/api/system-jobs/{jobId}")
            throw new InvalidOperationException("The local service returned an invalid search status address.");
        EnsureDrawingSessionCleanup();
        PendingReports[document.Database] = (statusUrl, cadId, snapshotHash);
        using var client = CreateClient();
        client.Timeout = TimeSpan.FromSeconds(15);
        var editor = document.Editor;
        editor.WriteMessage("\nCoordinating the selected sections. Hold Esc to stop waiting without changing the drawing. The local job may finish in the background.");
        var stopwatch = Stopwatch.StartNew();
        string? lastProgress = null;
        while (stopwatch.Elapsed < TimeSpan.FromMinutes(10))
        {
            if ((GetAsyncKeyState(0x1B) & 0x8000) != 0)
            {
                editor.WriteMessage("\nStopped waiting. No coordinated proposal was saved. Use MARITIMEREPORT after the local service finishes to view its result. Wait for it to finish before starting another run.");
                return null;
            }
            var response = SendServiceJson(client, HttpMethod.Get, statusUrl);
            if (Number(response, "api_version") != 1) throw new InvalidOperationException("Unsupported search response protocol.");
            var status = Text(response, "status");
            if (status == "complete") return response;
            if (status == "failed") throw new InvalidOperationException(response["error"]?.GetValue<string>() ?? "The coordinated search failed. Check the Python service window.");
            if (status is not ("pending" or "running")) throw new InvalidOperationException("Unknown coordinated search state.");
            if (response["progress"] is JsonObject progress)
            {
                var message = $"{progress["completed"]}/{progress["total"]} sections processed: {progress["message"]}";
                if (message != lastProgress) { editor.WriteMessage($"\n{message}"); lastProgress = message; }
            }
            for (var step = 0; step < 4; step++)
            {
                Thread.Sleep(250);
                if ((GetAsyncKeyState(0x1B) & 0x8000) != 0) break;
            }
        }
        throw new InvalidOperationException("The coordinated search did not finish within ten minutes. No geometry was saved. Check the local service before restarting.");
    }

    private static void VerifyBatchContext(Document document, JsonObject reply, string cadId, string hash, string fingerprint, bool requireCurrentScan = true)
    {
        if (Text(reply, "cad_id") != cadId || Text(reply, "source_sha256") != hash ||
            reply["document"]?["fingerprint"]?.GetValue<string>() != fingerprint ||
            document.Database.FingerprintGuid.ToString() != fingerprint || AcApplication.DocumentManager.MdiActiveDocument != document)
            throw new InvalidOperationException("This coordinated result does not match the active drawing snapshot.");
        if (requireCurrentScan) RequireCurrentScanSnapshot(document, cadId, hash);
    }

    private static List<SectionReview> ValidateSectionReviews(JsonObject summary, Dictionary<string, JsonObject> expected,
        Dictionary<string, JsonNode> selected, string fingerprint, string snapshotHash, JsonObject response)
    {
        if (summary["routes"] is not JsonArray routes || routes.Count != selected.Count || routes.Count > MaximumCoordinatedSections)
            throw new InvalidOperationException("The coordinated result omitted a selected section or exceeded the batch limit.");
        var seen = new HashSet<string>();
        var reviews = new List<SectionReview>();
        foreach (var route in routes)
        {
            if (route is not JsonObject item) throw new InvalidOperationException("An empty coordinated result was returned.");
            var id = Text(item, "candidate_id");
            if (!selected.ContainsKey(id) || !seen.Add(id)) throw new InvalidOperationException("An unexpected or duplicate section was returned.");
            var status = Text(item, "status");
            AcApplication.DocumentManager.MdiActiveDocument.Editor.WriteMessage($"\n{item["label"] ?? JsonValue.Create(id)}: {status}. {item["message"]}");
            if (status is "blocked" or "needs_review" or "not_processed") continue;
            if (status is not ("proposed" or "unchanged")) throw new InvalidOperationException("Unknown coordinated section outcome.");
            if (item["coordination"]?["accepted"]?.GetValue<bool>() != true ||
                item["coordination"]?["original_geometry_revalidated"]?.GetValue<bool>() != true ||
                item["coordination"]?["pair_clearance_revalidated"]?.GetValue<bool>() != true)
                throw new InvalidOperationException("A section was accepted without final source-geometry and pair-clearance checks.");
            if (!expected.TryGetValue(id, out var originalLayout)) throw new InvalidOperationException("A section was accepted without a verified local preview.");
            if (item["layout"] is not JsonObject layout || item["result"] is not JsonObject result)
                throw new InvalidOperationException("An accepted section has no layout or result.");
            VerifySnapshotContext(layout, fingerprint, snapshotHash);
            if (layout["cad_context"]?["candidate_id"]?.GetValue<string>() != id)
                throw new InvalidOperationException("A coordinated section changed its source identity.");
            var expectedHandles = originalLayout["cad_context"]?["source_handles"] as JsonArray;
            var returnedHandles = layout["cad_context"]?["source_handles"] as JsonArray;
            if (expectedHandles is null || returnedHandles is null ||
                !expectedHandles.Select(value => value?.GetValue<string>()).Order().SequenceEqual(returnedHandles.Select(value => value?.GetValue<string>()).Order()))
                throw new InvalidOperationException("A coordinated section changed its source handles.");
            if (Text(result, "units") != "mm" || Text(layout, "units") != "mm") throw new InvalidOperationException("Coordinated results must use millimetres.");
            if (Math.Abs(Number(layout["pipe"]!, "diameter") - Number(originalLayout["pipe"]!, "diameter")) > 0.0001)
                throw new InvalidOperationException("The coordinated search changed a selected diameter.");
            var original = ReadPoints(originalLayout["pipe"]?["original_route"], "selected source section");
            var baseline = ReadPoints(result["original"]?["points"], "returned source section");
            if (baseline.Count != original.Count || baseline.Where((point, index) => point.DistanceTo(original[index]) > 0.0001).Any())
                throw new InvalidOperationException("A coordinated result changed its original reference.");
            if (result["candidate"]?["valid"]?.GetValue<bool>() != true) throw new InvalidOperationException("An accepted section failed its geometric checks.");
            var points = ReadPoints(result["candidate"]?["points"], "coordinated proposal");
            if (points[0].DistanceTo(original[0]) > 0.0001 || points[^1].DistanceTo(original[^1]) > 0.0001)
                throw new InvalidOperationException("A coordinated proposal moved a fixed endpoint.");
            ValidateFixedStubs(originalLayout, points);
            if (result["assembly"] is not JsonObject assembly || Text(assembly, "status") != "ready" || Text(assembly, "units") != "mm")
                throw new InvalidOperationException("An accepted section has no checked rounded geometry.");
            if (assembly["manufacturing_assessment"] is not JsonObject assessment ||
                assessment["status"]?.GetValue<string>() == "blocked" || Number(assessment, "blocking_failure_count") > 0)
                throw new InvalidOperationException("An accepted section contains a blocking proposed-piece finding.");
            var diameter = Number(assembly, "outer_diameter_mm");
            if (Math.Abs(diameter - Number(originalLayout["pipe"]!, "diameter")) > 0.0001)
                throw new InvalidOperationException("A rounded section changed its diameter.");
            var centerline = ReadPoints(assembly["centerline_points"], "rounded section");
            if (centerline[0].DistanceTo(original[0]) > 0.0001 || centerline[^1].DistanceTo(original[^1]) > 0.0001)
                throw new InvalidOperationException("A rounded section moved a fixed endpoint.");
            var proposal = ReadMeshParts(assembly["meshes"], "section proposal");
            var reference = ReadMeshParts(assembly["reference_meshes"], "section reference");
            if (proposal.Count == 0 || reference.Count == 0) throw new InvalidOperationException("An accepted section has no comparison meshes.");
            var metadata = new JsonObject
            {
                ["candidate_id"] = id, ["source_handles"] = selected[id]["source_handles"]?.DeepClone(),
                ["global_route_number"] = selected[id]["global_number"]?.DeepClone(),
                ["selection_number"] = selected[id]["selection_number"]?.DeepClone(),
                ["cad_id"] = response["cad_id"]?.DeepClone(),
                ["drawing_fingerprint"] = fingerprint, ["source_snapshot_sha256"] = snapshotHash,
                ["status"] = status, ["result_url"] = response["exports"]?["json"]?.DeepClone(),
                ["representation"] = "outer_envelope_mesh", ["catalog_verified"] = false,
                ["outer_diameter_mm"] = diameter, ["bend_radius_mm"] = assembly["bend_radius_mm"]?.DeepClone(),
                ["assembly_metrics"] = assembly["metrics"]?.DeepClone(),
                ["manufacturing_counts"] = assembly["manufacturing_assessment"]?["counts"]?.DeepClone(),
                ["complete_system_verified"] = false, ["synthetic_demo"] = false
            };
            reviews.Add(new SectionReview(id, metadata, proposal, reference, centerline, ReadAssemblyDimensions(assembly)));
        }
        return reviews;
    }

    private static void ShowSystemReview(Document document, IReadOnlyList<SectionReview> reviews, double drawingScale, Action verifyContext)
    {
        var editor = document.Editor;
        var manager = TransientManager.CurrentTransientManager;
        var viewports = new IntegerCollection();
        var transient = new List<SubDMesh>();
        var allParts = reviews.SelectMany(section => section.Proposal.Concat(section.Reference)).ToList();
        if (allParts.Sum(part => (long)part.Vertices.Length) > 1000000)
            throw new InvalidOperationException("This combined preview is too large. Review fewer sections together.");
        using var previousView = editor.GetCurrentView();
        var saved = false;
        try
        {
            FrameMeshReview(document, allParts.SelectMany(part => part.Vertices).Select(point => Scale(point, drawingScale)).ToArray());
            editor.WriteMessage($"\nCombined 3D review for {reviews.Count} accepted sections: cyan original reference, green checked proposal. Source CAD remains visible. Unchanged outcomes are identified in the report; green does not mean every section improved.");
            var mode = "Both";
            while (true)
            {
                ClearTransientMeshes(manager, viewports, transient);
                foreach (var section in reviews)
                {
                    if (mode != "Proposal") Add(section.Reference, 4);
                    if (mode != "Original") Add(section.Proposal, 3);
                }
                editor.Regen();
                var selected = AskKeyword(editor, "Compare selected sections; Save keeps this combined review", ["Original", "Proposal", "Both", "Save", "Discard"], "Save");
                if (selected is null || selected == "Discard") return;
                if (selected == "Save")
                {
                    verifyContext();
                    SaveSystemReview(document, reviews, drawingScale);
                    saved = true;
                    return;
                }
                mode = selected;
            }
        }
        finally
        {
            ClearTransientMeshes(manager, viewports, transient);
            if (!saved) editor.SetCurrentView(previousView);
            editor.Regen();
        }
        void Add(IEnumerable<MeshPart> parts, short color)
        {
            foreach (var part in parts)
            {
                var mesh = BuildMesh(part, drawingScale, color);
                if (!manager.AddTransient(mesh, TransientDrawingMode.Main, 128, viewports))
                {
                    mesh.Dispose(); throw new InvalidOperationException("AutoCAD could not display the combined comparison.");
                }
                transient.Add(mesh);
            }
        }
    }

    private static void SaveSystemReview(Document document, IReadOnlyList<SectionReview> sections, double drawingScale)
    {
        // One transaction and the modal command keep the entire accepted set atomic and undoable.
        using var transaction = document.Database.TransactionManager.StartTransaction();
        var blocks = (BlockTable)transaction.GetObject(document.Database.BlockTableId, OpenMode.ForRead);
        var model = (BlockTableRecord)transaction.GetObject(blocks[BlockTableRecord.ModelSpace], OpenMode.ForWrite);
        var proposalLayer = GetReviewLayer(document.Database, transaction, ProposalMeshLayer, 3);
        var referenceLayer = GetReviewLayer(document.Database, transaction, ReferenceMeshLayer, 4);
        var centerlineLayer = GetReviewLayer(document.Database, transaction, SuggestionLayer, 3);
        foreach (var section in sections)
        {
            Add(section.Proposal, proposalLayer, 3, "proposal", section.Metadata);
            Add(section.Reference, referenceLayer, 4, "original_reference", section.Metadata);
            using var line = new Polyline3d(Poly3dType.SimplePoly,
                new Point3dCollection(section.Centerline.Select(point => Scale(point, drawingScale)).ToArray()), false);
            line.SetDatabaseDefaults(document.Database); line.LayerId = centerlineLayer;
            line.Color = Color.FromColorIndex(ColorMethod.ByAci, 3);
            model.AppendEntity(line); transaction.AddNewlyCreatedDBObject(line, true);
            var metadata = (JsonObject)section.Metadata.DeepClone(); metadata["role"] = "proposal_centerline";
            WriteReviewMetadata(line, transaction, metadata);
            SaveReviewDimensions(document, transaction, model, section.Dimensions, drawingScale, section.Metadata);
        }
        transaction.Commit();
        document.Editor.WriteMessage($"\nSaved one combined comparison for {sections.Count} accepted sections, with separate source identity and centerlines. Source pipe objects were not replaced. One UNDO removes this comparison. Use MARITIMEVIEW to switch views and MARITIMEREPORT for outcomes. Save this drawing under a new name to keep the review.");
        document.Editor.WriteMessage("\nMeasured piece callouts were saved on MARITIME_REVIEW_DIMENSIONS. Use MARITIMEDIMENSIONS On/Off to show or hide them. They describe review geometry, not fabrication approval.");
        void Add(IEnumerable<MeshPart> parts, ObjectId layer, short color, string role, JsonObject metadata)
        {
            foreach (var part in parts)
            {
                using var mesh = BuildMesh(part, drawingScale, color);
                mesh.SetDatabaseDefaults(document.Database); mesh.LayerId = layer;
                mesh.Color = Color.FromColorIndex(ColorMethod.ByAci, color);
                model.AppendEntity(mesh); transaction.AddNewlyCreatedDBObject(mesh, true);
                var record = (JsonObject)metadata.DeepClone();
                record["part_id"] = part.Id; record["part_kind"] = part.Kind; record["role"] = role;
                WriteReviewMetadata(mesh, transaction, record);
            }
        }
    }
}
