using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.GraphicsInterface;
using Autodesk.AutoCAD.Runtime;
using System.Diagnostics;
using System.Globalization;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Application;

namespace Maritime.AutoCAD;

public sealed partial class PreviewCommand
{
    // Cache only this live Database, not its filename or fingerprint: Save As and cloned
    // drawings can share fingerprints. Entity edits conservatively invalidate a scan.
    private static readonly Dictionary<Database, DrawingScanState> DrawingScans = new();
    private static bool drawingSessionCleanupRegistered;
    private sealed class DrawingScanState
    {
        public required Database Database;
        public string CadId = "", Hash = "", Fingerprint = "";
        public string System = "Pressure", Profile = "Challenge";
        public double Scale = 1, Clearance = 50.8, Padding = 500, Grid = 250, Slope = .01;
        public bool Reverse, Stale;
        public int Selected = 1;
        public JsonObject? Result;
        public void Changed(object sender, ObjectEventArgs args) { if (args.DBObject is Entity or BlockTableRecord) Stale = true; }
        public void Erased(object sender, ObjectErasedEventArgs args) { if (args.DBObject is Entity or BlockTableRecord) Stale = true; }
        public void Detach()
        {
            Database.ObjectModified -= Changed;
            Database.ObjectAppended -= Changed;
            Database.ObjectUnappended -= Changed;
            Database.ObjectReappended -= Changed;
            Database.ObjectErased -= Erased;
        }
    }

    private static void InvalidateDrawingScan(Document document)
    {
        if (DrawingScans.TryGetValue(document.Database, out var state)) state.Stale = true;
    }

    private static void RequireCurrentScanSnapshot(Document document, string cadId, string hash)
    {
        if (DrawingScans.TryGetValue(document.Database, out var state) && state.CadId == cadId && state.Hash == hash && state.Stale)
            throw new InvalidOperationException("The drawing or its context changed after this scan. Run MARITIMESCAN again before optimizing. The saved report still describes the previous snapshot.");
    }

    private static DrawingScanState BeginDrawingScan(Document document)
    {
        if (DrawingScans.Remove(document.Database, out var old)) old.Detach();
        var state = new DrawingScanState { Database = document.Database, Fingerprint = document.Database.FingerprintGuid.ToString() };
        DrawingScans[document.Database] = state;
        state.Database.ObjectModified += state.Changed;
        state.Database.ObjectAppended += state.Changed;
        state.Database.ObjectUnappended += state.Changed;
        state.Database.ObjectReappended += state.Changed;
        state.Database.ObjectErased += state.Erased;
        EnsureDrawingSessionCleanup();
        return state;
    }

    private static void EnsureDrawingSessionCleanup()
    {
        if (drawingSessionCleanupRegistered) return;
        AcApplication.DocumentManager.DocumentToBeDestroyed += (_, args) =>
        {
            var database = args.Document.Database;
            if (DrawingScans.Remove(database, out var removed)) removed.Detach();
            DrawingContexts.Remove(database);
            PendingReports.Remove(database);
            LatestReports.Remove(database);
        };
        drawingSessionCleanupRegistered = true;
    }

    [CommandMethod("MARITIMESCAN", CommandFlags.Modal)]
    public static void ScanDrawing()
    {
        var document = AcApplication.DocumentManager.MdiActiveDocument;
        if (document is null) return;
        var editor = document.Editor;
        string? snapshot = null;
        try
        {
            if (!document.Database.TileMode) throw new InvalidOperationException("Switch to Model space before scanning.");
            using var client = CreateClient();
            CheckService(client, "drawing_scan");
            editor.WriteMessage("\nFull drawing scan inventories every supported inferred section and records a result for each. Unsupported geometry and missing design evidence remain visible in the report.");
            var units = AskUnits(editor);
            if (units is null) return;
            var system = AskKeyword(editor, "Pipe system for this scan", ["Pressure", "Gravity"], "Pressure");
            if (system is null) return;
            var profile = AskKeyword(editor, "Rule profile", ["Challenge", "Prototype"], "Challenge");
            if (profile is null) return;
            if (!AskNumber(editor, "Clearance in mm", profile == "Challenge" ? 50.8 : 50, true, out var clearance) ||
                !AskNumber(editor, "Local review padding in mm", 500, false, out var padding) ||
                !AskNumber(editor, "Optimization grid step in mm", 250, false, out var grid)) return;
            if (profile == "Challenge" && clearance < 50.8) throw new InvalidOperationException("The challenge profile requires at least 50.8 mm shock clearance.");
            var reverse = false;
            var slope = .01;
            if (system == "Gravity")
            {
                var direction = AskKeyword(editor, "Reverse inferred start and end for flow", ["No", "Yes"], "No");
                if (direction is null) return;
                reverse = direction == "Yes";
                if (!AskNumber(editor, "Minimum drain slope as fall divided by horizontal distance", .01, false, out slope)) return;
            }
            snapshot = Path.Combine(Path.GetTempPath(), $"maritime-scan-{Guid.NewGuid():N}.dxf");
            editor.WriteMessage("\nExporting the current drawing to a temporary local snapshot.");
            document.Database.DxfOut(snapshot, 16, DwgVersion.AC1024);
            if (new FileInfo(snapshot).Length > SnapshotLimitBytes) throw new InvalidOperationException("The snapshot exceeds the supported 48 MB upload limit. No full scan was performed.");
            var bytes = File.ReadAllBytes(snapshot);
            var state = BeginDrawingScan(document);
            state.Scale = UnitScale(units); state.System = system; state.Profile = profile;
            state.Clearance = clearance; state.Padding = padding; state.Grid = grid; state.Reverse = reverse; state.Slope = slope;
            state.Hash = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
            var request = new JsonObject
            {
                ["filename"] = "autocad-snapshot.dxf", ["content_base64"] = Convert.ToBase64String(bytes),
                ["source_units"] = units,
                ["document"] = new JsonObject { ["name"] = Path.GetFileName(document.Name), ["fingerprint"] = state.Fingerprint },
                ["system_type"] = system == "Gravity" ? "gravity" : "pressurized",
                ["rule_profile"] = profile == "Challenge" ? "fmg-v3" : "prototype-unverified",
                ["clearance"] = clearance, ["padding"] = padding, ["reverse_flow"] = reverse, ["min_slope"] = slope
            };
            AddDrawingContext(request, document.Database);
            var pending = PostJson(client, "/api/autocad/scan", request);
            state.CadId = Text(pending, "cad_id");
            VerifyBatchContext(document, pending, state.CadId, state.Hash, state.Fingerprint);
            var result = WaitForDrawingScan(document, pending, state);
            if (result is null) return;
            CaptureScanResult(document, result);
            RememberReport(document, result);
            WriteScanSummary(document, state);
            if (state.Result?["summary"]?["candidates"] is not JsonArray { Count: > 0 }) return;
            while (true)
            {
                var next = AskKeyword(editor, "Full scan ready", ["Find", "Optimize", "Report", "Finish"], "Find");
                if (next is null or "Finish") return;
                if (next == "Report") { OpenLatestReport(); continue; }
                if (next == "Find") { BrowseScannedSections(document, state); continue; }
                RunScanOptimization(document, client, state);
                return;
            }
        }
        catch (System.Exception exception) { editor.WriteMessage($"\nDrawing scan stopped: {exception.Message}"); }
        finally
        {
            if (snapshot is not null)
                try { File.Delete(snapshot); }
                catch (System.Exception exception) when (exception is IOException or UnauthorizedAccessException)
                { editor.WriteMessage($"\nTemporary snapshot could not be removed: {snapshot}"); }
        }
    }

    private static JsonObject? WaitForDrawingScan(Document document, JsonObject pending, DrawingScanState state)
    {
        if (Number(pending, "api_version") != 1) throw new InvalidOperationException("Unsupported drawing scan protocol.");
        var jobId = Text(pending, "job_id");
        var statusUrl = Text(pending, "status_url");
        if (!Regex.IsMatch(jobId, "^[a-fA-F0-9]{32}$") || statusUrl != $"/api/drawing-jobs/{jobId}")
            throw new InvalidOperationException("The service returned an invalid drawing scan status address.");
        PendingReports[state.Database] = (statusUrl, state.CadId, state.Hash);
        using var client = CreateClient();
        client.Timeout = TimeSpan.FromSeconds(15);
        var editor = document.Editor;
        editor.WriteMessage("\nScanning all inferred sections. Hold Esc to request cancellation; the partial report will identify unprocessed sections.");
        var watch = Stopwatch.StartNew();
        string? lastProgress = null;
        while (watch.Elapsed < TimeSpan.FromMinutes(30))
        {
            if ((GetAsyncKeyState(0x1B) & 0x8000) != 0)
            {
                try { PostJson(client, statusUrl + "/cancel", new JsonObject()); }
                catch (System.Exception exception) { editor.WriteMessage($"\nCould not confirm cancellation: {exception.Message}. The service may still be scanning."); return null; }
                editor.WriteMessage("\nCancellation requested. The current section may finish. Use MARITIMEREPORT after the service finishes to view partial results and unprocessed coverage. No proposal geometry was added.");
                return null;
            }
            var response = SendServiceJson(client, HttpMethod.Get, statusUrl);
            if (Number(response, "api_version") != 1) throw new InvalidOperationException("Unsupported scan status protocol.");
            var status = Text(response, "status");
            if (status == "complete") return response;
            if (status == "failed") throw new InvalidOperationException(response["error"]?.GetValue<string>() ?? "The scan failed. Check the Python window.");
            if (status is not ("pending" or "running")) throw new InvalidOperationException("Unknown scan job state.");
            if (response["progress"] is JsonObject progress)
            {
                var done = progress["completed"] ?? progress["processed_routes"] ?? JsonValue.Create(0);
                var total = progress["total"] ?? progress["total_supported_routes"];
                var detail = progress["message"] ?? progress["phase"];
                var message = total is null ? $"{detail}" : $"{done}/{total} sections processed: {detail}";
                if (message != lastProgress) { editor.WriteMessage($"\n{message}"); lastProgress = message; }
            }
            for (var step = 0; step < 4; step++)
            {
                Thread.Sleep(250);
                if ((GetAsyncKeyState(0x1B) & 0x8000) != 0) break;
            }
        }
        editor.WriteMessage("\nStopped waiting after 30 minutes. The service may still be scanning. MARITIMEREPORT can open its completed result later. No proposal geometry was added.");
        return null;
    }

    private static void CaptureScanResult(Document document, JsonObject result)
    {
        if (!DrawingScans.TryGetValue(document.Database, out var state)) return;
        VerifyBatchContext(document, result, state.CadId, state.Hash, state.Fingerprint, requireCurrentScan: false);
        if (Text(result, "units") != "mm" || result["summary"] is not JsonObject summary ||
            summary["candidates"] is not JsonArray candidates || summary["review"] is not JsonObject)
            throw new InvalidOperationException("The scan result is missing its inventory, coverage or millimetre unit declaration.");
        var ids = new HashSet<string>();
        for (var index = 0; index < candidates.Count; index++)
        {
            var item = candidates[index] ?? throw new InvalidOperationException("The scan contains an empty route.");
            if (!ids.Add(Text(item, "id")) || Number(item, "global_number") != index + 1)
                throw new InvalidOperationException("The scan returned duplicate identities or inconsistent route numbers.");
        }
        state.Result = (JsonObject)result.DeepClone();
        if (state.Stale) document.Editor.WriteMessage("\nThis report describes an earlier snapshot because the drawing or its context changed. Run MARITIMESCAN again before finding or optimizing sections.");
    }

    private static void WriteScanSummary(Document document, DrawingScanState state)
    {
        var result = state.Result ?? throw new InvalidOperationException("No scan result was stored.");
        var summary = result["summary"]!;
        var review = summary["review"]!;
        var editor = document.Editor;
        editor.WriteMessage($"\nFull drawing inventory: {review["total_supported_routes"]} supported inferred sections; {review["processed_routes"]} processed; {review["reviewed_routes"]} geometry-checked; {review["not_processed_routes"]} not processed.");
        editor.WriteMessage($"\nCoverage: {review["coverage"]}. A completed scan is not a full-compliance or optimality certificate.");
        if (review["counts"] is JsonObject counts)
            foreach (var entry in counts) editor.WriteMessage($"\n{ScanStatusLabel(entry.Key)}: {entry.Value}");
        WriteMessages(editor, review["limitations"], "Review limit", 4);
        var rows = (JsonArray)summary["candidates"]!;
        var priorities = rows.Where(row => row?["review_status"]?.GetValue<string>() is "attention" or "opportunity").Take(8).ToArray();
        if (priorities.Length == 0) priorities = rows.Take(8).ToArray();
        editor.WriteMessage("\nFirst findings (the report contains the full numbered inventory):");
        foreach (var row in priorities)
            editor.WriteMessage($"\n{row!["global_number"]}: {row["label"]} | {ScanStatusLabel(Text(row, "review_status"))} | source {SourceHandleText(row)}");
        editor.WriteMessage($"\nReport: {ServiceUrl}{Text(result["exports"]!, "report")}\nMARITIMEREPORT opens the report. MARITIMEFIND locates a numbered section in this drawing. MARITIMEOPTIMIZE searches up to 12 selected sections from this scan.");
    }

    private static string ScanStatusLabel(string value) => value switch
    {
        "not_processed" => "Not processed",
        _ => ReviewStatusLabel(value)
    };

    [CommandMethod("MARITIMEFIND", CommandFlags.Modal)]
    public static void FindScannedSection()
    {
        var document = AcApplication.DocumentManager.MdiActiveDocument;
        if (document is null) return;
        try
        {
            if (!DrawingScans.TryGetValue(document.Database, out var state) || state.Result is null)
                throw new InvalidOperationException("Run MARITIMESCAN in this drawing first. If a scan is finishing in the background, run MARITIMEREPORT to collect its result.");
            BrowseScannedSections(document, state);
        }
        catch (System.Exception exception) { document.Editor.WriteMessage($"\nFind stopped: {exception.Message}"); }
    }

    private static bool TryOptimizeLatestScan()
    {
        var document = AcApplication.DocumentManager.MdiActiveDocument;
        if (document is null) return true;
        if (!DrawingScans.TryGetValue(document.Database, out var state) || state.Result is null) return false;
        try
        {
            RequireCurrentScanSnapshot(document, state.CadId, state.Hash);
            var answer = AskKeyword(document.Editor, "Use the latest full scan inventory", ["Yes", "No"], "Yes");
            if (answer is null) return true;
            if (answer == "No") return false;
            using var client = CreateClient();
            CheckService(client, "coordinated_sections");
            RunScanOptimization(document, client, state);
        }
        catch (System.Exception exception) { document.Editor.WriteMessage($"\nOptimization stopped: {exception.Message}"); }
        return true;
    }

    private static void RunScanOptimization(Document document, HttpClient client, DrawingScanState state)
    {
        RequireCurrentScanSnapshot(document, state.CadId, state.Hash);
        var candidates = state.Result?["summary"]?["candidates"] as JsonArray
            ?? throw new InvalidOperationException("There is no completed scan inventory.");
        if (candidates.Count == 0) throw new InvalidOperationException("No supported pipe sections were identified.");
        document.Editor.WriteMessage($"\nChoose up to 12 route numbers from the full inventory of {candidates.Count}. The report retains every other section's screening outcome. This operation does not redesign all branches automatically.");
        RunCoordinatedSelection(document, client, candidates, state.CadId, state.Hash, state.Fingerprint,
            state.Scale, state.System, state.Profile, state.Clearance, state.Padding, state.Grid, state.Reverse, state.Slope);
    }

    private static void BrowseScannedSections(Document document, DrawingScanState state)
    {
        RequireCurrentScanSnapshot(document, state.CadId, state.Hash);
        if (!document.Database.TileMode) throw new InvalidOperationException("Switch to Model space before finding a section.");
        var rows = state.Result?["summary"]?["candidates"] as JsonArray ?? throw new InvalidOperationException("No completed inventory is available.");
        if (rows.Count == 0) throw new InvalidOperationException("No supported sections were identified.");
        var pick = document.Editor.GetInteger(new PromptIntegerOptions($"\nRoute number from the full report (1–{rows.Count})")
        { DefaultValue = Math.Clamp(state.Selected, 1, rows.Count), UseDefaultValue = true, LowerLimit = 1, UpperLimit = rows.Count });
        if (pick.Status != PromptStatus.OK) return;
        state.Selected = pick.Value;
        while (true)
        {
            RequireCurrentScanSnapshot(document, state.CadId, state.Hash);
            var row = rows[state.Selected - 1]!;
            var next = ShowScannedSection(document, row, state.Scale, state.Selected, rows.Count);
            if (next is null or "Finish") return;
            if (next == "Report") { OpenLatestReport(); continue; }
            if (next == "Next") state.Selected = state.Selected == rows.Count ? 1 : state.Selected + 1;
            else if (next == "Previous") state.Selected = state.Selected == 1 ? rows.Count : state.Selected - 1;
            else
            {
                var number = document.Editor.GetInteger(new PromptIntegerOptions("\nRoute number")
                { DefaultValue = state.Selected, UseDefaultValue = true, LowerLimit = 1, UpperLimit = rows.Count });
                if (number.Status != PromptStatus.OK) return;
                state.Selected = number.Value;
            }
        }
    }

    private static string? ShowScannedSection(Document document, JsonNode row, double scale, int number, int count)
    {
        var editor = document.Editor;
        editor.WriteMessage($"\nRoute {number}/{count}: {row["label"]}; source handles {SourceHandleText(row)}.\n{ScanStatusLabel(Text(row, "review_status"))}: {row["review_message"]}");
        editor.WriteMessage($"\nInferred outside diameter {row["diameter_mm"]} mm; length {row["length_mm"]} mm. Pipe identity and nominal size still require source review.");
        if (row["rule_findings"] is JsonArray rules)
            foreach (var finding in rules.Where(item => item?["status"]?.GetValue<string>() is "fail" or "needs_review")
                .OrderBy(item => item?["status"]?.GetValue<string>() == "fail" ? 0 : 1).Take(6))
            {
                var message = finding?["message"];
                if (message is null && finding?["rule_ref"] is JsonValue reference && DrawingScans.TryGetValue(document.Database, out var scan))
                    message = scan.Result?["summary"]?["rule_catalog"]?[reference.GetValue<string>()]?["message"];
                editor.WriteMessage($"\n{finding!["status"]}: {finding["id"]} — {message}");
            }
        if (row["findings"] is JsonArray findings)
            foreach (var finding in findings.Take(3))
                editor.WriteMessage($"\nGeometry flag: {finding?["message"]} (segment {finding?["segment"]}).");
        editor.WriteMessage("\nThe report lists every recorded finding and its evidence.");
        var highlighted = new List<ObjectId>();
        var points = new List<Point3d>();
        var manager = TransientManager.CurrentTransientManager;
        var viewports = new IntegerCollection();
        var markers = new List<Line>();
        try
        {
            var nested = false;
            var handles = new HashSet<string>();
            if (row["source_instances"] is JsonArray instances)
                foreach (var instance in instances)
                {
                    if (instance?["instance_path"] is JsonArray { Count: > 0 } path)
                    {
                        // A nested entity handle names its block definition, not a world-space
                        // instance. Highlight the owning model-space insert, then frame the
                        // precise scanned route bounds and mark its actual world endpoints.
                        var handle = Regex.Match(path[0]?.GetValue<string>() ?? "", "^[a-fA-F0-9]+").Value;
                        if (handle.Length > 0) handles.Add(handle);
                        nested = true;
                    }
                    else if (instance?["source_handle"] is JsonValue value) handles.Add(value.GetValue<string>());
                }
            else if (row["source_handles"] is JsonArray direct)
                foreach (var handle in direct) if (handle is not null) handles.Add(handle.GetValue<string>());
            using (var transaction = document.Database.TransactionManager.StartOpenCloseTransaction())
            {
                var blocks = (BlockTable)transaction.GetObject(document.Database.BlockTableId, OpenMode.ForRead);
                foreach (var handle in handles)
                {
                    if (!long.TryParse(handle, NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out var numeric)) continue;
                    try
                    {
                        var id = document.Database.GetObjectId(false, new Handle(numeric), 0);
                        if (id.IsNull || id.IsErased || transaction.GetObject(id, OpenMode.ForRead) is not Entity entity || entity.OwnerId != blocks[BlockTableRecord.ModelSpace]) continue;
                        entity.Highlight(); highlighted.Add(id);
                        try { points.Add(entity.GeometricExtents.MinPoint); points.Add(entity.GeometricExtents.MaxPoint); }
                        catch (Autodesk.AutoCAD.Runtime.Exception) { }
                    }
                    catch (Autodesk.AutoCAD.Runtime.Exception) { }
                }
            }
            if (nested) editor.WriteMessage("\nThis section is inside a block. The owning block is highlighted; endpoint markers and the view frame locate the selected section within it.");
            if (highlighted.Count == 0) editor.WriteMessage("\nSource objects could not be highlighted. Only the recorded endpoint positions and bounds are shown; review source identity before optimizing.");
            var routePoints = new List<Point3d>();
            if (row["bounds"] is JsonObject bounds)
            {
                AddPoint(bounds["min"], routePoints); AddPoint(bounds["max"], routePoints);
            }
            else if (row["bounds"] is JsonArray pair && pair.Count == 2)
            { AddPoint(pair[0], routePoints); AddPoint(pair[1], routePoints); }
            var start = ReadScanPoint(row["start"], scale);
            var end = ReadScanPoint(row["end"], scale);
            if (routePoints.Count == 0) { if (start is not null) routePoints.Add(start.Value); if (end is not null) routePoints.Add(end.Value); }
            FrameMeshReview(document, routePoints.Count > 0 ? routePoints : points);
            var size = Math.Max(1, Number(row, "diameter_mm") * .7) / scale;
            AddMarker(start, 1); AddMarker(end, 6);
            if (row["findings"] is JsonArray locatedFindings)
            {
                var seenSegments = new HashSet<string>();
                foreach (var finding in locatedFindings)
                {
                    if (seenSegments.Count >= 30) break;
                    var a = ReadScanPoint(finding?["segment_start"], scale);
                    var b = ReadScanPoint(finding?["segment_end"], scale);
                    if (a is null || b is null || !seenSegments.Add($"{a}:{b}")) continue;
                    var segment = new Line(a.Value, b.Value) { ColorIndex = 1 };
                    if (!manager.AddTransient(segment, TransientDrawingMode.Main, 128, viewports)) { segment.Dispose(); continue; }
                    markers.Add(segment);
                }
                if (seenSegments.Count > 0) editor.WriteMessage("\nRed segment overlays locate recorded geometry flags (up to 30); intended contacts may also be flagged. The report retains the findings.");
            }
            editor.Regen();
            editor.WriteMessage("\nRed cross = recorded start; magenta cross = recorded end. These markers do not create a new route.");
            return AskKeyword(editor, "Find section", ["Next", "Previous", "Number", "Report", "Finish"], "Finish");
            void AddPoint(JsonNode? value, List<Point3d> target) { var point = ReadScanPoint(value, scale); if (point is not null) target.Add(point.Value); }
            void AddMarker(Point3d? point, short color)
            {
                if (point is null) return;
                foreach (var axis in new[] { Vector3d.XAxis, Vector3d.YAxis, Vector3d.ZAxis })
                {
                    var line = new Line(point.Value - axis * size, point.Value + axis * size) { ColorIndex = color };
                    if (!manager.AddTransient(line, TransientDrawingMode.Main, 128, viewports)) { line.Dispose(); continue; }
                    markers.Add(line);
                }
            }
        }
        finally
        {
            foreach (var line in markers)
            { try { manager.EraseTransient(line, viewports); } catch (Autodesk.AutoCAD.Runtime.Exception) { } line.Dispose(); }
            using var transaction = document.Database.TransactionManager.StartOpenCloseTransaction();
            foreach (var id in highlighted)
                try { if (!id.IsErased && transaction.GetObject(id, OpenMode.ForRead) is Entity entity) entity.Unhighlight(); }
                catch (Autodesk.AutoCAD.Runtime.Exception) { }
            editor.Regen();
        }
    }

    private static Point3d? ReadScanPoint(JsonNode? node, double scale)
    {
        if (node is not JsonArray { Count: 3 } coordinates) return null;
        var values = coordinates.Select(value => value?.GetValue<double>() ?? double.NaN).ToArray();
        if (values.Any(value => !double.IsFinite(value) || Math.Abs(value) > 1e12)) throw new InvalidOperationException("The scan contains an invalid world coordinate.");
        return new Point3d(values[0] / scale, values[1] / scale, values[2] / scale);
    }
}
