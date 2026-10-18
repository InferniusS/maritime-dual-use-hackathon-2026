using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.Colors;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.GraphicsInterface;
using Autodesk.AutoCAD.Runtime;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Application;

namespace Maritime.AutoCAD;

public sealed partial class PreviewCommand
{
    private const string ServiceUrl = "http://127.0.0.1:8767";
    private const string SuggestionLayer = "MARITIME_SUGGESTION";
    private const int SnapshotLimitBytes = 48 * 1024 * 1024;

    private static readonly Dictionary<string, JsonObject> DrawingContexts = new();

    [CommandMethod("MARITIMECONTEXT", CommandFlags.Modal)]
    public static void LoadDrawingContext()
    {
        var document = AcApplication.DocumentManager.MdiActiveDocument;
        if (document is null) return;
        var editor = document.Editor;
        var fingerprint = document.Database.FingerprintGuid.ToString();
        try
        {
            var action = AskKeyword(editor, "Drawing context", ["Load", "Clear", "Status"], "Load");
            if (action == "Clear") { DrawingContexts.Remove(fingerprint); editor.WriteMessage("\nDrawing context cleared."); return; }
            if (action == "Status") { editor.WriteMessage(DrawingContexts.ContainsKey(fingerprint) ? "\nA context file is loaded for this drawing session." : "\nNo context file is loaded. Missing context will be reported for review."); return; }
            if (action != "Load") return;
            var selected = editor.GetFileNameForOpen(new PromptOpenFileOptions("\nChoose the reviewed model context JSON file") { Filter = "JSON files (*.json)|*.json" });
            if (selected.Status != PromptStatus.OK) return;
            if (new FileInfo(selected.StringResult).Length > 16 * 1024 * 1024)
                throw new InvalidOperationException("Context files must be at most 16 MB. Use a smaller local scene.");
            var json = JsonNode.Parse(File.ReadAllText(selected.StringResult)) as JsonObject
                ?? throw new InvalidOperationException("The context file must be a JSON object.");
            DrawingContexts[fingerprint] = json;
            editor.WriteMessage("\nContext loaded for this drawing session. Geometry and evidence will be validated when MARITIMECAD prepares the route. Confirm drawing alignment before running.");
        }
        catch (System.Exception exception) { editor.WriteMessage($"\nContext was not loaded: {exception.Message}"); }
    }

    [CommandMethod("MARITIMECAD", CommandFlags.Modal)]
    public static void OptimizeDrawingPipe()
    {
        var document = AcApplication.DocumentManager.MdiActiveDocument;
        if (document is null) return;
        var editor = document.Editor;
        string? snapshot = null;
        try
        {
            using var client = CreateClient();
            CheckService(client);
            var pick = new PromptEntityOptions("\nSelect a pipe solid or its block in model space: ");
            pick.SetRejectMessage("\nSelect a 3D solid or block reference.");
            pick.AddAllowedClass(typeof(Solid3d), false);
            pick.AddAllowedClass(typeof(BlockReference), false);
            var selected = editor.GetEntity(pick);
            if (selected.Status != PromptStatus.OK) return;

            string handle;
            using (var transaction = document.Database.TransactionManager.StartTransaction())
            {
                var entity = (Entity)transaction.GetObject(selected.ObjectId, OpenMode.ForRead);
                var blockTable = (BlockTable)transaction.GetObject(document.Database.BlockTableId, OpenMode.ForRead);
                if (entity.OwnerId != blockTable[BlockTableRecord.ModelSpace])
                    throw new InvalidOperationException("Select geometry directly in model space. Paper space is not supported.");
                if (entity is BlockReference block)
                {
                    var definition = (BlockTableRecord)transaction.GetObject(block.BlockTableRecord, OpenMode.ForRead);
                    if (definition.IsFromExternalReference)
                        throw new InvalidOperationException("Open the referenced drawing directly. External reference geometry is not included in this snapshot.");
                }
                handle = entity.Handle.ToString();
            }
            var units = AskUnits(editor);
            if (units is null) return;
            var scale = UnitScale(units);
            var system = AskKeyword(editor, "Pipe system", ["Pressure", "Gravity"], "Pressure");
            if (system is null) return;
            var profile = AskKeyword(editor, "Rule profile", ["Challenge", "Prototype"], "Challenge");
            if (profile is null) return;
            if (!AskNumber(editor, "Clearance in mm", profile == "Challenge" ? 50.8 : 50, true, out var clearance) ||
                !AskNumber(editor, "Local search padding in mm", 500, false, out var padding) ||
                !AskNumber(editor, "Search grid step in mm", 250, false, out var grid)) return;
            if (profile == "Challenge" && clearance < 50.8)
                throw new InvalidOperationException("The challenge profile requires at least 50.8 mm shock clearance.");
            var reverse = false;
            var slope = 0.01;
            if (system == "Gravity")
            {
                var direction = AskKeyword(editor, "Reverse inferred start and end for flow", ["No", "Yes"], "No");
                if (direction is null) return;
                reverse = direction == "Yes";
                if (!AskNumber(editor, "Minimum drain slope as fall divided by horizontal distance", 0.01, false, out slope)) return;
            }

            editor.WriteMessage("\nExporting a temporary AutoCAD 2010 DXF snapshot. Source objects remain unchanged.");
            snapshot = Path.Combine(Path.GetTempPath(), $"maritime-{Guid.NewGuid():N}.dxf");
            document.Database.DxfOut(snapshot, 16, DwgVersion.AC1024);
            if (new FileInfo(snapshot).Length > SnapshotLimitBytes)
                throw new InvalidOperationException("The DXF snapshot exceeds 48 MB. Open a smaller drawing that still includes the surrounding obstacles.");
            var metadata = new { name = Path.GetFileName(document.Name), fingerprint = document.Database.FingerprintGuid.ToString() };
            editor.WriteMessage("\nReading pipe geometry with the local optimizer. Large drawings may take a few minutes.");
            var inspected = PostJson(client, "/api/autocad/inspect", new
            {
                filename = "autocad-snapshot.dxf",
                content_base64 = Convert.ToBase64String(File.ReadAllBytes(snapshot)),
                source_units = units,
                selected_handles = new[] { handle },
                document = metadata
            });
            if (Number(inspected, "api_version") != 1 || Text(inspected, "units") != "mm")
                throw new InvalidOperationException("The snapshot response uses an unsupported protocol or unit.");
            var cadId = Text(inspected, "cad_id");
            var snapshotHash = Text(inspected, "source_sha256");
            var choices = inspected["candidates"] as JsonArray ?? throw new InvalidOperationException("No candidate list was returned.");
            if (choices.Count == 0) throw new InvalidOperationException("No supported pipe run was found for this selection.");
            editor.WriteMessage("\nThese routes are inferred from geometry. Pipe identity and branch connections still need review.");
            WriteMessages(editor, inspected["warnings"], "Drawing note", 8);
            for (var index = 0; index < choices.Count; index++)
            {
                var item = choices[index] ?? throw new InvalidOperationException("An empty candidate was returned.");
                editor.WriteMessage($"\n{index + 1}: {Text(item, "label")} | {Number(item, "member_count"):0} component(s) | " +
                    $"{Number(item, "length_mm"):0.###} mm long | inferred diameter {Number(item, "diameter_mm"):0.###} mm");
            }
            var choice = editor.GetInteger(new PromptIntegerOptions("\nChoose the route number")
            {
                DefaultValue = 1, UseDefaultValue = true, LowerLimit = 1, UpperLimit = choices.Count
            });
            if (choice.Status != PromptStatus.OK) return;
            var candidateId = Text(choices[choice.Value - 1]!, "id");
            var request = new JsonObject
            {
                ["cad_id"] = cadId, ["candidate_id"] = candidateId,
                ["system_type"] = system == "Gravity" ? "gravity" : "pressurized",
                ["clearance"] = clearance, ["padding"] = padding, ["reverse_flow"] = reverse,
                ["rule_profile"] = profile == "Challenge" ? "fmg-v3" : "prototype-unverified"
            };
            if (DrawingContexts.TryGetValue(metadata.fingerprint, out var contextFile))
            {
                request["model_context"] = (contextFile["model_context"] ?? contextFile).DeepClone();
                if (contextFile["engineering"] is JsonObject engineering) request["engineering"] = engineering.DeepClone();
            }
            var prepared = PostJson(client, "/api/prepare-cad-layout", request);
            var layout = RequiredLayout(prepared);
            VerifySnapshotContext(layout, metadata.fingerprint, snapshotHash);
            var original = ReadPoints(layout["pipe"]?["original_route"], "selected baseline");
            editor.WriteMessage($"\nSelected route has {original.Count} points. " +
                $"The local check includes {(layout["obstacles"] as JsonArray)?.Count ?? 0} nearby components.");
            if (layout["cad_context"]?["counts"] is JsonObject coverage)
                editor.WriteMessage($"\nPipe-shaped envelopes: {coverage["pipe_shaped_obstacles"]}. " +
                    $"Fallback boxes: {coverage["fallback_box_obstacles"]}.");
            if (!ShowComparedPreview(document, original.Select(p => Scale(p, scale)).ToList(), null,
                "Cyan is the inferred route to be checked. Verify its start, end and included components.", false, null)) return;

            var contacts = layout["cad_context"]?["endpoint_contacts"] as JsonArray;
            if (contacts is { Count: > 0 })
            {
                editor.WriteMessage("\nSome component envelopes touch the route ends. They may be connectors or actual collisions.");
                var connections = new Dictionary<string, List<string>>();
                foreach (var contact in contacts)
                {
                    if (contact is null) continue;
                    var endpoint = Text(contact, "endpoint");
                    if (endpoint is not ("start" or "end")) throw new InvalidOperationException("Unknown endpoint contact.");
                    var obstacleId = Text(contact, "obstacle_id");
                    editor.WriteMessage($"\n{endpoint}: source {Text(contact, "source_handle")} ({obstacleId})");
                    if (!connections.TryGetValue(endpoint, out var ids)) connections[endpoint] = ids = [];
                    if (!ids.Contains(obstacleId)) ids.Add(obstacleId);
                }
                editor.WriteMessage("\nAccepting these as connections preserves bounded original end sections. It does not approve engineering compliance.");
                var accept = AskKeyword(editor, "Treat all listed end contacts as intended fixed connections", ["No", "Yes"], "No");
                if (accept is null) return;
                if (accept == "Yes")
                {
                    if (!AskNumber(editor, "Maximum fixed connection length in mm (at most 1000)", 250, false, out var maximum)) return;
                    if (maximum > 1000) throw new InvalidOperationException("Fixed endpoint assumptions are limited to 1000 mm.");
                    request["endpoint_connections"] = JsonSerializer.SerializeToNode(connections);
                    request["max_stub_length"] = maximum;
                    layout = RequiredLayout(PostJson(client, "/api/prepare-cad-layout", request));
                    VerifySnapshotContext(layout, metadata.fingerprint, snapshotHash);
                }
            }
            var rules = layout["rules"] as JsonObject ?? throw new InvalidOperationException("Prepared layout has no rules.");
            rules["grid_step"] = grid;
            if (system == "Gravity") rules["min_slope"] = slope;
            if (system == "Pressure")
            {
                var diameter = Number(layout["pipe"]!, "diameter");
                if (!AskNumber(editor, "Proposed bend centerline radius in mm (review assumption)", Math.Max(304.8, 1.5 * diameter), false, out var radius)) return;
                layout["proposal"] = new JsonObject { ["mode"] = "rounded_pipe", ["bend_radius_mm"] = radius };
            }
            RunAndReview(document, client, layout, scale, metadata.fingerprint, snapshotHash);
        }
        catch (System.Exception exception)
        {
            editor.WriteMessage($"\nMaritime CAD check stopped: {exception.Message}");
        }
        finally
        {
            if (snapshot is not null)
            {
                try { File.Delete(snapshot); }
                catch (IOException) { editor.WriteMessage($"\nTemporary snapshot could not be removed: {snapshot}"); }
                catch (UnauthorizedAccessException) { editor.WriteMessage($"\nTemporary snapshot could not be removed: {snapshot}"); }
            }
        }
    }

    private static HttpClient CreateClient() => new(new SocketsHttpHandler
    {
        // Do not reuse a socket while the single-request Python handler closes it.
        PooledConnectionLifetime = TimeSpan.Zero
    }) { Timeout = TimeSpan.FromMinutes(5), BaseAddress = new Uri(ServiceUrl) };

    private static void CheckService(HttpClient client)
    {
        var json = SendServiceJson(client, HttpMethod.Get, "/api/health");
        if (json["capabilities"] is not JsonArray capabilities || !capabilities.Any(value => value?.GetValue<string>() == "challenge_rules_v3") ||
            !capabilities.Any(value => value?.GetValue<string>() == "rounded_pipe_proposal"))
            throw new InvalidOperationException("Update and restart the Python service. This plugin needs challenge_rules_v3 and rounded_pipe_proposal support.");
        if (Text(json, "service") != "maritime-pipe-router" || Number(json, "api_version") != 1 || Text(json, "units") != "mm")
            throw new InvalidOperationException("The local service is not a compatible Maritime optimizer. Update and restart the Python service.");
    }

    private static JsonObject PostJson(HttpClient client, string path, object payload) =>
        SendServiceJson(client, HttpMethod.Post, path, payload);

    private static JsonObject SendServiceJson(HttpClient client, HttpMethod method, string path, object? payload = null)
    {
        using var request = new HttpRequestMessage(method, path);
        // The local Python service handles one request per connection.
        request.Headers.ConnectionClose = true;
        if (payload is not null)
            request.Content = new StringContent(JsonSerializer.Serialize(payload), Encoding.UTF8, "application/json");
        try
        {
            using var response = client.SendAsync(request).GetAwaiter().GetResult();
            return ReadResponse(response);
        }
        catch (HttpRequestException exception)
        {
            throw new InvalidOperationException(
                $"Local request {method} {path} did not complete. No suggestion was saved. " +
                "Check the Python window for details. The service may still be running.\n" +
                $"HTTP error category: {exception.HttpRequestError}\n{DescribeRequestFailure(exception)}", exception);
        }
        catch (TaskCanceledException exception)
        {
            throw new InvalidOperationException(
                $"Local request {method} {path} timed out or was canceled. No suggestion was saved. " +
                "The service may still be finishing the calculation. Check the Python window before trying again.\n" +
                DescribeRequestFailure(exception), exception);
        }
    }

    private static string DescribeRequestFailure(System.Exception exception)
    {
        var details = new List<string>();
        for (System.Exception? current = exception; current is not null; current = current.InnerException)
            details.Add($"{current.GetType().Name}: {current.Message}");
        return string.Join("\nCaused by: ", details);
    }

    private static JsonObject ReadResponse(HttpResponseMessage response)
    {
        var body = response.Content.ReadAsStringAsync().GetAwaiter().GetResult();
        JsonObject json;
        try { json = JsonNode.Parse(body) as JsonObject ?? throw new JsonException(); }
        catch (JsonException) { throw new InvalidOperationException($"Local service returned unreadable data (HTTP {(int)response.StatusCode})."); }
        if (!response.IsSuccessStatusCode)
            throw new InvalidOperationException($"Local service: {json["error"]?.GetValue<string>() ?? response.ReasonPhrase}");
        return json;
    }

    private static JsonObject RequiredLayout(JsonObject response)
    {
        var layout = response["layout"] as JsonObject ?? throw new InvalidOperationException("No prepared layout was returned.");
        if (Text(layout, "units") != "mm") throw new InvalidOperationException("The prepared layout must use millimetres.");
        return (JsonObject)layout.DeepClone();
    }

    private static void VerifySnapshotContext(JsonObject layout, string fingerprint, string snapshotHash)
    {
        if (layout["cad_context"]?["document"]?["fingerprint"]?.GetValue<string>() != fingerprint ||
            layout["cad_context"]?["source_snapshot_sha256"]?.GetValue<string>() != snapshotHash)
            throw new InvalidOperationException("The returned layout does not match this drawing snapshot.");
    }

    private static void RunAndReview(Document document, HttpClient client, JsonObject layout,
        double drawingScale, string fingerprint, string? snapshotHash = null, bool demo = false)
    {
        var editor = document.Editor;
        editor.WriteMessage("\nChecking the original route and searching for a replacement locally.");
        var reply = PostJson(client, "/api/run", new { layout });
        var result = reply["result"] as JsonObject ?? throw new InvalidOperationException("The optimizer returned no result.");
        if (Text(result, "units") != "mm") throw new InvalidOperationException("The optimizer result must use millimetres.");
        var status = Text(result, "status");
        if (status is not ("improved" or "unchanged" or "no_route")) throw new InvalidOperationException("The optimizer returned an unknown status.");
        var normalized = RequiredLayout(reply);
        if (snapshotHash is not null) VerifySnapshotContext(normalized, fingerprint, snapshotHash);
        if (document.Database.FingerprintGuid.ToString() != fingerprint)
            throw new InvalidOperationException("The active drawing no longer matches the request.");
        var originalRecord = result["original"] as JsonObject ?? throw new InvalidOperationException("No baseline result was returned.");
        var baseline = ReadPoints(originalRecord["points"], "baseline result");
        var expectedScale = UnitScale(Text(layout, "units"));
        var expected = ReadPoints(layout["pipe"]?["original_route"], "request baseline")
            .Select(point => new Point3d(point.X * expectedScale, point.Y * expectedScale, point.Z * expectedScale)).ToList();
        if (baseline.Count != expected.Count || baseline.Where((point, index) => point.DistanceTo(expected[index]) > 0.0001).Any())
            throw new InvalidOperationException("The returned baseline does not match the request.");
        editor.WriteMessage($"\nOriginal length: {Number(originalRecord, "length"):0.###} mm. " +
            $"Original passes geometric checks: {originalRecord["valid"]?.GetValue<bool>() == true}. Review rule findings separately.");
        if (result["challenge_review"] is JsonObject review)
        {
            foreach (var key in new[] { "original", "candidate" })
            {
                if (review[key] is not JsonObject assessment) continue;
                var counts = assessment["counts"];
                editor.WriteMessage($"\nV3 {key}: {counts?["pass"]} pass, {counts?["fail"]} fail, {counts?["needs_review"]} need review. Full compliance is not verified.");
                if (assessment["findings"] is JsonArray findings)
                    foreach (var finding in findings.Where(f => f?["status"]?.GetValue<string>() == "fail").Take(8))
                        editor.WriteMessage($"\n{finding?["id"]}: {finding?["message"]}");
            }
        }
        WriteMessages(editor, result["limitations"], "Limit", 6);
        if (reply["exports"] is JsonObject exports)
        {
            foreach (var export in exports)
            {
                var path = export.Value?.GetValue<string>();
                if (path is not null && path.StartsWith("/downloads/", StringComparison.Ordinal))
                    editor.WriteMessage($"\n{export.Key} report: {ServiceUrl}{path}");
            }
        }
        if (status == "no_route")
        {
            editor.WriteMessage("\nNo checked replacement was found by this search. Other routes may exist.");
            WriteIssues(editor, originalRecord["issues"]);
            if (result["search"]?["reason"] is JsonValue reason) editor.WriteMessage($"\n{reason.GetValue<string>()}");
            if (result["assembly"] is JsonObject rejectedAssembly)
            {
                editor.WriteMessage("\nNo accepted rounded physical proposal is available.");
                WriteIssues(editor, rejectedAssembly["findings"]);
            }
            if (demo && ShowRejectedDemo(document, normalized, result)) return;
            ShowComparedPreview(document, baseline.Select(point => Scale(point, drawingScale)).ToList(), null,
                "Cyan is the original route. No replacement has been accepted.", false, null);
            return;
        }
        var candidate = result["candidate"] as JsonObject ?? throw new InvalidOperationException("The optimizer returned no candidate for its success status.");
        if (candidate["valid"]?.GetValue<bool>() != true)
            throw new InvalidOperationException("The optimizer candidate did not pass its checks.");
        var points = ReadPoints(candidate["points"], "suggested route");
        if (points[0].DistanceTo(expected[0]) > 0.0001 || points[^1].DistanceTo(expected[^1]) > 0.0001)
            throw new InvalidOperationException("The suggested route moved a fixed endpoint.");
        ValidateFixedStubs(normalized, points);
        var originalLength = Number(originalRecord, "length");
        var suggestedLength = Number(candidate, "length");
        if (status == "unchanged") editor.WriteMessage("\nThe original route was retained. This search found no better checked route.");
        else editor.WriteMessage($"\nSuggested design-line length: {suggestedLength:0.###} mm. Design-line length change: {suggestedLength - originalLength:+0.###;-0.###;0} mm. Rounded pipe length is reported separately when available.");
        editor.WriteMessage("\nThis is a checked centerline suggestion under the selected geometry rules. Review the V3 findings and missing evidence in the report. It is not a fitted pipe model or a compliance certificate.");
        var record = new JsonObject
        {
            ["drawing_fingerprint"] = fingerprint,
            ["source_snapshot_sha256"] = snapshotHash,
            ["status"] = status,
            ["result_url"] = reply["exports"]?["json"]?.DeepClone()
        };
        if (result["assembly"] is JsonObject assembly)
        {
            if (assembly["status"]?.GetValue<string>() == "ready")
            {
                var display = demo ? "3D" : AskKeyword(editor, "Review output", ["3D", "Centerline"], "3D");
                if (display is null) return;
                if (display == "3D" && ShowAssemblyReview(document, normalized, result, points, drawingScale, record, demo)) return;
            }
            else
            {
                editor.WriteMessage("\nNo accepted rounded physical proposal is available. A centerline alone is not a fitted pipe assembly.");
                WriteIssues(editor, assembly["findings"]);
            }
        }
        if (demo) editor.WriteMessage("\nNo 3D comparison was saved. See the report for the blocked physical checks.");
        ShowComparedPreview(document, baseline.Select(point => Scale(point, drawingScale)).ToList(),
            points.Select(point => Scale(point, drawingScale)).ToList(),
            "Cyan is the original centerline. Green is the checked suggestion.", status == "improved", record);
    }

    private static void ValidateFixedStubs(JsonObject layout, IReadOnlyList<Point3d> points)
    {
        if (layout["protected_endpoints"] is not JsonArray stubs) return;
        foreach (var stub in stubs)
        {
            if (stub is null) throw new InvalidOperationException("An empty fixed endpoint record was returned.");
            var fixedPoints = ReadPoints(stub["points"], "fixed endpoint");
            var endpoint = Text(stub, "endpoint");
            if (endpoint is not ("start" or "end")) throw new InvalidOperationException("Unknown fixed endpoint.");
            if (points.Count < fixedPoints.Count) throw new InvalidOperationException("A fixed endpoint section was removed.");
            var offset = endpoint == "start" ? 0 : points.Count - fixedPoints.Count;
            if (fixedPoints.Where((point, index) => point.DistanceTo(points[offset + index]) > 0.0001).Any())
                throw new InvalidOperationException("The suggested route changed a fixed endpoint section.");
        }
    }

    private static bool ShowComparedPreview(Document document, IReadOnlyList<Point3d> baseline,
        IReadOnlyList<Point3d>? suggestion, string message, bool allowSave, JsonObject? metadata)
    {
        var editor = document.Editor;
        var manager = TransientManager.CurrentTransientManager;
        var viewports = new IntegerCollection();
        var previews = new List<Line>();
        try
        {
            AddPreview(baseline, 4);
            if (suggestion is not null) AddPreview(suggestion, 3);
            editor.Regen();
            editor.WriteMessage($"\n{message}");
            if (allowSave && suggestion is not null)
            {
                var save = AskKeyword(editor, "Save a separate review centerline or discard preview", ["Discard", "Save"], "Discard");
                if (save is null) return false;
                if (save == "Save") SaveSuggestion(document, suggestion, metadata);
            }
            else
            {
                var confirmation = editor.GetString(new PromptStringOptions("\nPress Enter to clear the preview and continue: "));
                if (confirmation.Status is not (PromptStatus.OK or PromptStatus.None)) return false;
            }
            return true;
        }
        finally
        {
            var cleanupFailed = false;
            foreach (var line in previews)
            {
                try { manager.EraseTransient(line, viewports); }
                catch (System.Exception) { cleanupFailed = true; }
                try { line.Dispose(); }
                catch (System.Exception) { cleanupFailed = true; }
            }
            try { editor.Regen(); }
            catch (System.Exception) { cleanupFailed = true; }
            if (cleanupFailed)
            {
                // Cleanup must attempt every drawable and must not hide the original failure.
                try { editor.WriteMessage("\nAutoCAD could not finish clearing every preview item. Run REGEN to refresh the display."); }
                catch (System.Exception) { }
            }
        }
        void AddPreview(IReadOnlyList<Point3d> points, short color)
        {
            for (var index = 0; index + 1 < points.Count; index++)
            {
                if (points[index].DistanceTo(points[index + 1]) <= 1e-12) continue;
                var line = new Line(points[index], points[index + 1]) { Color = Color.FromColorIndex(ColorMethod.ByAci, color) };
                if (!manager.AddTransient(line, TransientDrawingMode.Main, 128, viewports))
                {
                    line.Dispose();
                    throw new InvalidOperationException("AutoCAD could not display the route preview.");
                }
                previews.Add(line);
            }
        }
    }

    private static void SaveSuggestion(Document document, IReadOnlyList<Point3d> points, JsonObject? metadata)
    {
        // The modal command and single committed transaction form one undoable drawing operation.
        using var transaction = document.Database.TransactionManager.StartTransaction();
        var layers = (LayerTable)transaction.GetObject(document.Database.LayerTableId, OpenMode.ForRead);
        ObjectId layerId;
        if (layers.Has(SuggestionLayer))
        {
            layerId = layers[SuggestionLayer];
            var existing = (LayerTableRecord)transaction.GetObject(layerId, OpenMode.ForRead);
            if (existing.IsLocked || existing.IsFrozen || existing.IsOff)
                throw new InvalidOperationException($"Layer {SuggestionLayer} is locked or hidden. Make it editable and visible before saving a suggestion.");
        }
        else
        {
            layers.UpgradeOpen();
            using var layer = new LayerTableRecord { Name = SuggestionLayer, Color = Color.FromColorIndex(ColorMethod.ByAci, 3) };
            layerId = layers.Add(layer);
            transaction.AddNewlyCreatedDBObject(layer, true);
        }
        var blocks = (BlockTable)transaction.GetObject(document.Database.BlockTableId, OpenMode.ForRead);
        var model = (BlockTableRecord)transaction.GetObject(blocks[BlockTableRecord.ModelSpace], OpenMode.ForWrite);
        using var route = new Polyline3d(Poly3dType.SimplePoly, new Point3dCollection(points.ToArray()), false);
        route.SetDatabaseDefaults(document.Database);
        route.LayerId = layerId;
        route.Color = Color.FromColorIndex(ColorMethod.ByAci, 3);
        model.AppendEntity(route);
        transaction.AddNewlyCreatedDBObject(route, true);
        if (metadata is not null)
        {
            route.CreateExtensionDictionary();
            var dictionary = (DBDictionary)transaction.GetObject(route.ExtensionDictionary, OpenMode.ForWrite);
            using var record = new Xrecord();
            var json = metadata.ToJsonString();
            var values = Enumerable.Range(0, (json.Length + 239) / 240)
                .Select(index => new TypedValue((int)DxfCode.Text, json.Substring(index * 240, Math.Min(240, json.Length - index * 240)))).ToArray();
            record.Data = new ResultBuffer(values);
            dictionary.SetAt("MARITIME_SOURCE", record);
            transaction.AddNewlyCreatedDBObject(record, true);
        }
        var handle = route.Handle.ToString();
        transaction.Commit();
        document.Editor.WriteMessage($"\nSaved review centerline {handle} on {SuggestionLayer}. Source objects were not replaced. Use UNDO to remove this addition.");
    }

    private static List<Point3d> ReadPoints(JsonNode? node, string label)
    {
        if (node is not JsonArray array || array.Count < 2 || array.Count > 10000)
            throw new InvalidOperationException($"The {label} must contain 2 to 10000 points.");
        var result = new List<Point3d>();
        foreach (var raw in array)
        {
            if (raw is not JsonArray point || point.Count != 3) throw new InvalidOperationException($"The {label} contains an invalid coordinate.");
            var values = point.Select(value => value?.GetValue<double>() ?? double.NaN).ToArray();
            if (values.Any(value => !double.IsFinite(value) || Math.Abs(value) > 1e12))
                throw new InvalidOperationException($"The {label} contains a non-finite or out-of-range coordinate.");
            result.Add(new Point3d(values[0], values[1], values[2]));
        }
        return result;
    }

    private static string Text(JsonNode node, string name) => node[name]?.GetValue<string>() is { Length: > 0 } value
        ? value : throw new InvalidOperationException($"Local response is missing {name}.");

    private static double Number(JsonNode node, string name)
    {
        var value = node[name]?.GetValue<double>() ?? double.NaN;
        return double.IsFinite(value) ? value : throw new InvalidOperationException($"Local response has an invalid {name}.");
    }

    private static double UnitScale(string units) => units switch
    {
        "mm" => 1, "m" => 1000, "in" => 25.4,
        _ => throw new InvalidOperationException("Units must be mm, m or in.")
    };

    private static Point3d Scale(Point3d point, double scale) => new(point.X / scale, point.Y / scale, point.Z / scale);

    private static string? AskUnits(Editor editor)
    {
        var answer = AskKeyword(editor, "Interpret drawing coordinates as", ["Millimetres", "Metres", "Inches"], "Millimetres");
        return answer switch { "Millimetres" => "mm", "Metres" => "m", "Inches" => "in", _ => null };
    }

    private static string? AskKeyword(Editor editor, string prompt, string[] choices, string defaultValue)
    {
        var options = new PromptKeywordOptions($"\n{prompt}") { AllowNone = true };
        foreach (var choice in choices) options.Keywords.Add(choice);
        options.Keywords.Default = defaultValue;
        var result = editor.GetKeywords(options);
        return result.Status == PromptStatus.None ? defaultValue : result.Status == PromptStatus.OK ? result.StringResult : null;
    }

    private static bool AskNumber(Editor editor, string prompt, double defaultValue, bool allowZero, out double value)
    {
        var result = editor.GetDouble(new PromptDoubleOptions($"\n{prompt}")
        {
            DefaultValue = defaultValue, UseDefaultValue = true, AllowNegative = false, AllowZero = allowZero
        });
        value = result.Value;
        return result.Status == PromptStatus.OK && double.IsFinite(value);
    }

    private static void WriteMessages(Editor editor, JsonNode? node, string prefix, int limit)
    {
        if (node is not JsonArray messages) return;
        foreach (var message in messages.Take(limit))
            if (message is JsonValue value && value.TryGetValue<string>(out var text)) editor.WriteMessage($"\n{prefix}: {text}");
        if (messages.Count > limit) editor.WriteMessage($"\n{messages.Count - limit} additional notes are in the saved result report.");
    }

    private static void WriteIssues(Editor editor, JsonNode? node)
    {
        if (node is not JsonArray issues) return;
        foreach (var issue in issues.Take(8))
            if (issue?["message"] is JsonValue message) editor.WriteMessage($"\nCheck: {message.GetValue<string>()}");
        if (issues.Count > 8) editor.WriteMessage($"\n{issues.Count - 8} additional findings are in the saved result report.");
    }
}
