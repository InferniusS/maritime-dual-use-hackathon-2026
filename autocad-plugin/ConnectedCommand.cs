using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.Colors;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.GraphicsInterface;
using Autodesk.AutoCAD.Runtime;
using System.Globalization;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Application;

namespace Maritime.AutoCAD;

/// <summary>
/// Additive network handoff/review. Original source entities are opened ForRead only.
/// These commands do not manufacture or replace pipe solids.
/// </summary>
public sealed partial class PreviewCommand
{
    private const string NetworkOriginalLayer = "PIPEFORGE4_ORIGINAL";
    private const string NetworkProposalLayer = "PIPEFORGE4_PROPOSED";
    private const int NetworkJsonLimitBytes = 32 * 1024 * 1024;
    private static readonly ConditionalWeakTable<Database, NetworkRevision> NetworkRevisions = new();

    // A new token after process/reopen rejects old bundles, even if a filename is reused.
    // Any database object edit after enrollment invalidates previously handed-off geometry.
    private sealed class NetworkRevision
    {
        private readonly string session = Guid.NewGuid().ToString("N");
        private long generation;
        public NetworkSourceBinding? SourceBinding { get; set; }
        public NetworkRevision(Database database)
        {
            database.ObjectAppended += (_, _) => System.Threading.Interlocked.Increment(ref generation);
            database.ObjectModified += (_, _) => System.Threading.Interlocked.Increment(ref generation);
            database.ObjectErased += (_, _) => System.Threading.Interlocked.Increment(ref generation);
        }
        public string Token => $"pipeforge-session:{session}:{System.Threading.Interlocked.Read(ref generation)}";
    }

    [CommandMethod("PIPEFORGENETWORK", CommandFlags.Modal)]
    public static void InspectConnectedNetwork()
    {
        var document = AcApplication.DocumentManager.MdiActiveDocument;
        if (document is null) return;
        var editor = document.Editor;
        string? snapshot = null;
        try
        {
            var permission = AskKeyword(editor,
                "Confirm authorization to process this drawing locally; use synthetic geometry if unclear",
                ["No", "Yes"], "No");
            if (permission != "Yes") return;
            var selection = editor.GetSelection(new PromptSelectionOptions
            {
                MessageForAdding = "\nSelect all source pipe solids/blocks for the connected-network handoff: ",
                AllowDuplicates = false
            }, new SelectionFilter([new TypedValue((int)DxfCode.Start, "3DSOLID,INSERT")]));
            if (selection.Status != PromptStatus.OK) return;
            if (selection.Value.Count is < 1 or > 256)
                throw new InvalidOperationException("Select between 1 and 256 source entities.");
            var handles = new List<string>();
            using (var transaction = document.Database.TransactionManager.StartTransaction())
            {
                var blocks = (BlockTable)transaction.GetObject(document.Database.BlockTableId, OpenMode.ForRead);
                foreach (var id in selection.Value.GetObjectIds())
                {
                    var entity = (Entity)transaction.GetObject(id, OpenMode.ForRead);
                    if (entity.OwnerId != blocks[BlockTableRecord.ModelSpace])
                        throw new InvalidOperationException("Select source geometry directly in model space.");
                    if (entity is BlockReference block)
                    {
                        var definition = (BlockTableRecord)transaction.GetObject(block.BlockTableRecord, OpenMode.ForRead);
                        if (definition.IsFromExternalReference)
                            throw new InvalidOperationException("Open externally referenced drawings directly before inspection.");
                    }
                    handles.Add(entity.Handle.ToString());
                }
            }
            var units = AskUnits(editor);
            if (units is null) return;
            if (!AskNumber(editor, "Endpoint review tolerance in mm (maximum 25; never establishes connectivity)", 1, false, out var tolerance)
                || tolerance > 25) throw new InvalidOperationException("Endpoint review tolerance must be greater than zero and at most 25 mm.");
            editor.WriteMessage("\nDeclare the permitted routing volume explicitly in global WCS millimetres. Extracted drawing extents are not permission to route.");
            var minimum = AskNetworkCoordinate(editor, "Minimum permitted bound");
            if (minimum is null) return;
            var maximum = AskNetworkCoordinate(editor, "Maximum permitted bound");
            if (maximum is null) return;
            if (minimum.Where((value, index) => value >= maximum[index]).Any())
                throw new InvalidOperationException("Each maximum bound must exceed its minimum bound.");
            var bounds = new { min = minimum, max = maximum };
            var coordinateSystem = new { frame = "WCS" };
            var output = editor.GetFileNameForSave(new PromptSaveFileOptions("\nSave connected-network handoff JSON: ")
            {
                Filter = "PipeForge network (*.json)|*.json"
            });
            if (output.Status != PromptStatus.OK) return;
            var tracker = NetworkRevisions.GetValue(document.Database, database => new NetworkRevision(database));
            var revision = tracker.Token;
            var drawing = new JsonObject
            {
                ["id"] = document.Database.FingerprintGuid.ToString(), ["revision"] = revision,
                ["name"] = Path.GetFileName(document.Name), ["source_units"] = units
            };
            using var client = CreateClient();
            CheckService(client);
            editor.WriteMessage("\nReading the selected sources and surrounding drawing geometry locally. No source object will be edited.");
            snapshot = Path.Combine(Path.GetTempPath(), $"pipeforge-network-{Guid.NewGuid():N}.dxf");
            document.Database.DxfOut(snapshot, 16, DwgVersion.AC1024);
            if (new FileInfo(snapshot).Length > SnapshotLimitBytes)
                throw new InvalidOperationException("The snapshot exceeds 48 MB. Use a smaller authorized test drawing with surrounding obstacles.");
            drawing["revision"] = tracker.Token;
            var inspection = PostJson(client, "/api/autocad/inspect", new
            {
                filename = "connected-snapshot.dxf", content_base64 = Convert.ToBase64String(File.ReadAllBytes(snapshot)),
                source_units = units, selected_handles = handles,
                document = new { name = Path.GetFileName(document.Name), fingerprint = document.Database.FingerprintGuid.ToString() }
            });
            var response = PostJson(client, "/api/network/inspect", new
            {
                cad_id = Text(inspection, "cad_id"), tolerance_mm = tolerance, drawing, selected_handles = handles, bounds,
                coordinate_system = coordinateSystem
            });
            var network = response["network"] as JsonObject ?? throw new InvalidOperationException("No connected network was returned.");
            VerifyNetworkSource(document, network, requireGeometryBinding: false);
            tracker.SourceBinding = new NetworkSourceBinding(network);
            File.WriteAllText(output.StringResult, network.ToJsonString(new JsonSerializerOptions { WriteIndented = true }), Encoding.UTF8);
            var unresolved = (network["unresolved_connections"] as JsonArray)?.Count ?? 0;
            editor.WriteMessage($"\nSaved {output.StringResult}. Network has {(network["spans"] as JsonArray)?.Count ?? 0} spans and {unresolved} unresolved connection(s).");
            editor.WriteMessage("\nOpen the companion Connected network workspace, load this JSON, confirm topology and system/port metadata, then optimize and review.");
            editor.WriteMessage("\nProximity is a proposal, not proof of connectivity. Coverage and standards remain UNVERIFIED until their evidence is supplied.");
            WriteNetworkCoordinates(editor, network);
        }
        catch (System.Exception exception) { editor.WriteMessage($"\nPipeForge network handoff stopped: {exception.Message}"); }
        finally
        {
            if (snapshot is not null)
                try { File.Delete(snapshot); }
                catch (System.Exception exception) when (exception is IOException or UnauthorizedAccessException)
                { editor.WriteMessage($"\nRemove the temporary authorized snapshot manually: {snapshot}"); }
        }
    }

    [CommandMethod("PIPEFORGECONNECTIONS", CommandFlags.Modal)]
    public static void ConfirmNetworkConnections()
    {
        var document = AcApplication.DocumentManager.MdiActiveDocument;
        if (document is null) return;
        var editor = document.Editor;
        try
        {
            var source = OpenNetworkJson(editor, "Open connected-network handoff JSON");
            if (source is null) return;
            var network = source.Value.Json;
            VerifyNetworkSource(document, network);
            if (network["unresolved_connections"] is not JsonArray unresolved || unresolved.Count == 0)
            {
                editor.WriteMessage("\nThis network has no unresolved geometric connection proposals.");
                return;
            }
            WriteNetworkCoordinates(editor, network);
            editor.WriteMessage("\nConfirm only reviewed pipe/system/port connections. Proximity does not establish semantic connectivity. The first endpoint's exact position is the anchor; tolerance is never enlarged.");
            var decisions = new JsonArray();
            foreach (var item in unresolved)
            {
                if (item is null) throw new InvalidOperationException("An unresolved connection is empty.");
                editor.WriteMessage($"\nConnection {Text(item, "id")}: endpoints={item["endpoint_ids"]}; reason={item["reason"]}.");
                var action = AskKeyword(editor, "Review this connection", ["Skip", "Connect", "Separate"], "Skip");
                if (action is null) return;
                if (action == "Skip") continue;
                decisions.Add(new JsonObject
                {
                    ["connection_id"] = Text(item, "id"), ["action"] = action == "Connect" ? "connect" : "separate"
                });
            }
            if (decisions.Count == 0) return;
            using var client = CreateClient();
            CheckService(client);
            var response = PostJson(client, "/api/network/confirm", new { network, decisions });
            var confirmed = response["network"] as JsonObject ?? throw new InvalidOperationException("No confirmed network was returned.");
            VerifyNetworkSource(document, confirmed);
            var target = editor.GetFileNameForSave(new PromptSaveFileOptions("\nSave reviewed connected-network JSON: ")
            { Filter = "PipeForge network (*.json)|*.json" });
            if (target.Status != PromptStatus.OK) return;
            File.WriteAllText(target.StringResult, confirmed.ToJsonString(new JsonSerializerOptions { WriteIndented = true }), Encoding.UTF8);
            editor.WriteMessage($"\nSaved {target.StringResult}; {(confirmed["unresolved_connections"] as JsonArray)?.Count ?? 0} connection(s) remain unresolved. Source geometry was preserved.");
            editor.WriteMessage("\nConfirming topology does not verify fitting compatibility or unknown system metadata. Correct system/port metadata in the companion before optimization.");
        }
        catch (System.Exception exception) { editor.WriteMessage($"\nPipeForge connection review stopped: {exception.Message}"); }
    }

    private static double[]? AskNetworkCoordinate(Editor editor, string label)
    {
        var values = new double[3];
        var axes = new[] { "X", "Y", "Z" };
        for (var index = 0; index < values.Length; index++)
        {
            var answer = editor.GetDouble(new PromptDoubleOptions($"\n{label} {axes[index]} in global WCS mm: ")
            { AllowNegative = true, AllowZero = true, AllowNone = false });
            if (answer.Status != PromptStatus.OK) return null;
            if (!double.IsFinite(answer.Value) || Math.Abs(answer.Value) > 1e9)
                throw new InvalidOperationException("Global coordinate must be finite and within 1e9 mm.");
            values[index] = answer.Value;
        }
        return values;
    }

    [CommandMethod("PIPEFORGEREVIEW", CommandFlags.Modal)]
    public static void ReviewConnectedNetwork()
    {
        var document = AcApplication.DocumentManager.MdiActiveDocument;
        if (document is null) return;
        var editor = document.Editor;
        try
        {
            var selected = OpenNetworkJson(editor, "Open exported network-result.json");
            if (selected is null) return;
            var result = selected.Value.Json;
            var original = result["original_network"] as JsonObject ?? throw new InvalidOperationException("The result has no original network.");
            var proposal = result["proposed_network"] as JsonObject;
            VerifyNetworkSource(document, original);
            if (Text(result, "source_revision") != Text(original["drawing"]!, "revision"))
                throw new InvalidOperationException("The proposal revision does not match its original network.");
            using var client = CreateClient();
            CheckService(client);
            var checkedResult = PostJson(client, "/api/network/validate", new { network = proposal ?? original, reference = original });
            if (Text(checkedResult, "source_digest") != Text(result, "source_digest"))
                throw new InvalidOperationException("The proposal source digest does not match its independent reference check.");
            VerifyNetworkSource(document, original);
            var validation = checkedResult["validation"] as JsonObject ?? throw new InvalidOperationException("No independent validation was returned.");
            editor.WriteMessage($"\nNetwork status: {Text(result, "status")}. Independent hard failures: {Number(validation, "hard_failures"):0}; unverified checks: {Number(validation, "unverified"):0}.");
            editor.WriteMessage("\nCyan is original. Green is proposed. Failed spans are red; source objects use AutoCAD highlight. UNVERIFIED checks remain in the report.");
            WriteNetworkCoordinates(editor, proposal ?? original);
            WriteNetworkChecks(editor, validation);
            var preview = AskKeyword(editor, "Display the network review overlay", ["No", "Yes"], "No");
            if (preview != "Yes") return;
            VerifyNetworkSource(document, original);
            ShowNetworkOverlay(document, original, proposal, validation, result);
            var folder = Path.GetDirectoryName(Path.GetFullPath(selected.Value.Path))!;
            foreach (var name in new[] { "instructions.md", "instructions.json", "components.xlsx", "components.csv", "network-preview.dxf" })
            {
                var path = Path.Combine(folder, name);
                if (File.Exists(path)) editor.WriteMessage($"\nReviewed bundle file: {path}");
            }
            editor.WriteMessage("\nUse the companion explicit approval/export action for fresh instructions and Excel. A centerline overlay does not authorize replacing source solids.");
        }
        catch (System.Exception exception) { editor.WriteMessage($"\nPipeForge network review stopped: {exception.Message}"); }
    }

    private static void VerifyNetworkSource(Document document, JsonObject network, bool requireGeometryBinding = true)
    {
        if (AcApplication.DocumentManager.MdiActiveDocument != document)
            throw new InvalidOperationException("The active drawing changed. Reactivate and inspect the source drawing before continuing.");
        if (Text(network, "schema_version") != "pipeforge.network/4.0" || Text(network, "units") != "mm")
            throw new InvalidOperationException("Expected a PipeForge 4.0 network in global WCS millimetres.");
        if (Text(network["coordinate_system"]!, "frame") != "WCS")
            throw new InvalidOperationException("The network coordinate frame must be WCS.");
        var drawing = network["drawing"] as JsonObject ?? throw new InvalidOperationException("No source drawing identity.");
        if (Text(drawing, "id") != document.Database.FingerprintGuid.ToString())
            throw new InvalidOperationException("This bundle belongs to a different drawing.");
        var tracker = NetworkRevisions.GetValue(document.Database, database => new NetworkRevision(database));
        if (Text(drawing, "revision") != tracker.Token)
            throw new InvalidOperationException("The source drawing changed or reopened. Inspect it again; stale proposals cannot be reviewed or saved.");
        if (requireGeometryBinding)
        {
            if (tracker.SourceBinding is null)
                throw new InvalidOperationException("No source geometry was inspected in this drawing session. Run PIPEFORGENETWORK again.");
            tracker.SourceBinding.Verify(network);
        }
        using var transaction = document.Database.TransactionManager.StartTransaction();
        foreach (var collection in new[] { "nodes", "spans" })
        {
            if (network[collection] is not JsonArray components) throw new InvalidOperationException($"Network has no {collection}.");
            foreach (var component in components)
            {
                if (component is null) throw new InvalidOperationException("Empty network component.");
                if (component["cad_handle"] is JsonValue handle) ResolveSource(handle.GetValue<string>());
                if (component["instance_path"] is JsonArray path)
                    foreach (var item in path) ResolveSource(item?.GetValue<string>() ?? "");
                if (component["source_instances"] is JsonArray instances)
                    foreach (var instance in instances)
                    {
                        if (instance?["source_handle"] is JsonValue source) ResolveSource(source.GetValue<string>());
                        if (instance?["instance_path"] is JsonArray placement)
                            foreach (var item in placement) ResolveSource(item?.GetValue<string>() ?? "");
                    }
            }
        }
        void ResolveSource(string raw)
        {
            var handle = raw.Split('[')[0]; // Array-placement suffix is identity metadata, not a database handle.
            if (!long.TryParse(handle, NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out var number) || number == 0)
                throw new InvalidOperationException("Invalid source handle in the network.");
            var id = document.Database.GetObjectId(false, new Handle(number), 0);
            if (id.IsNull || id.IsErased || transaction.GetObject(id, OpenMode.ForRead) is not Entity)
                throw new InvalidOperationException($"Source handle {raw} no longer identifies a live entity.");
        }
    }

    private static (JsonObject Json, string Path)? OpenNetworkJson(Editor editor, string message)
    {
        var prompt = editor.GetFileNameForOpen(new PromptOpenFileOptions($"\n{message}: ") { Filter = "JSON (*.json)|*.json" });
        if (prompt.Status != PromptStatus.OK) return null;
        var file = new FileInfo(prompt.StringResult);
        if (file.Length is < 1 or > NetworkJsonLimitBytes)
            throw new InvalidOperationException("Network JSON must be between 1 byte and 32 MB.");
        var json = JsonNode.Parse(File.ReadAllText(file.FullName, Encoding.UTF8)) as JsonObject
            ?? throw new InvalidOperationException("Network file must contain a JSON object.");
        return (json, file.FullName);
    }

    private static void WriteNetworkCoordinates(Editor editor, JsonObject network)
    {
        editor.WriteMessage("\nComponent properties and exact global coordinates (mm; round-trip numeric text):");
        foreach (var collection in new[] { "nodes", "spans" })
            if (network[collection] is JsonArray components)
                foreach (var component in components)
                {
                    if (component is null) continue;
                    editor.WriteMessage($"\n{component["id"]}: system={component["system_id"]}; CAD={component["cad_handle"] ?? component["source_instances"]}; nominal={component["nominal_diameter_mm"]} mm; type={component["type"] ?? JsonValue.Create("span")}; fixed={component["fixed"]}; outer diameter={component["outer_diameter_mm"]} mm; dimensions={component["dimensions"]}; fitting catalog={component["fitting_catalog"]}; constraints={component["constraints"]}.");
                    var points = component["points"] as JsonArray ?? new JsonArray(component["position"]?.DeepClone());
                    for (var index = 0; index < points.Count; index++)
                        if (points[index] is JsonArray point && point.Count == 3)
                            editor.WriteMessage($"\n  P{index}: X={point[0]!.GetValue<double>().ToString("R", CultureInfo.InvariantCulture)}, Y={point[1]!.GetValue<double>().ToString("R", CultureInfo.InvariantCulture)}, Z={point[2]!.GetValue<double>().ToString("R", CultureInfo.InvariantCulture)}");
                }
    }

    private static void WriteNetworkChecks(Editor editor, JsonObject validation)
    {
        if (validation["checks"] is not JsonArray checks) return;
        foreach (var check in checks)
            if (check is not null && check["status"]?.GetValue<string>() is "FAIL" or "UNVERIFIED")
                editor.WriteMessage($"\n{check["status"]} {check["id"]} ({check["severity"]}): {check["message"]}; components={check["component_ids"]}; evidence={check["evidence"]}");
    }

    private static void ShowNetworkOverlay(Document document, JsonObject original, JsonObject? proposal,
        JsonObject validation, JsonObject result, bool allowKeep = true)
    {
        var units = original["drawing"]?["source_units"]?.GetValue<string>()
            ?? throw new InvalidOperationException("Native review requires the original drawing's source_units.");
        var scale = UnitScale(units);
        var manager = TransientManager.CurrentTransientManager;
        var viewports = new IntegerCollection();
        var transients = new List<Line>();
        var highlighted = new List<Entity>();
        var keep = false;
        var failedIds = new HashSet<string>(StringComparer.Ordinal);
        using (var highlightTransaction = document.Database.TransactionManager.StartTransaction())
        try
        {
            if (validation["checks"] is JsonArray checks)
                foreach (var check in checks)
                    if (check?["status"]?.GetValue<string>() == "FAIL" && check["component_ids"] is JsonArray ids)
                        foreach (var id in ids) if (id is JsonValue value) failedIds.Add(value.GetValue<string>());
            Draw(original, 4);
            if (proposal is not null) Draw(proposal, 3);
            foreach (var component in ((JsonArray)original["nodes"]!).Concat((JsonArray)original["spans"]!))
            {
                if (component is null || !failedIds.Contains(Text(component, "id"))) continue;
                // Nested geometry lives in a block definition; highlight its
                // visible outer placement instead of an invisible definition entity.
                var handle = component["instance_path"] is JsonArray placement && placement.Count > 0
                    ? placement[0]?.GetValue<string>()?.Split('[')[0]
                    : component["cad_handle"]?.GetValue<string>();
                if (handle is null) continue;
                if (!long.TryParse(handle, NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out var number)) continue;
                var id = document.Database.GetObjectId(false, new Handle(number), 0);
                if (highlightTransaction.GetObject(id, OpenMode.ForRead) is Entity entity)
                {
                    entity.Highlight();
                    highlighted.Add(entity);
                }
            }
            document.Editor.Regen();
            var save = allowKeep
                ? AskKeyword(document.Editor, "Keep separate review centerlines, or clear temporary preview", ["Clear", "Keep"], "Clear")
                : AskKeyword(document.Editor, "Clear this temporary preview after inspection", ["Clear"], "Clear");
            keep = save == "Keep";
        }
        finally
        {
            var cleanupFailed = false;
            foreach (var entity in highlighted)
                try { entity.Unhighlight(); } catch (System.Exception) { cleanupFailed = true; }
            foreach (var line in transients)
            {
                try { manager.EraseTransient(line, viewports); } catch (System.Exception) { cleanupFailed = true; }
                try { line.Dispose(); } catch (System.Exception) { cleanupFailed = true; }
            }
            try { document.Editor.Regen(); } catch (System.Exception) { cleanupFailed = true; }
            if (cleanupFailed)
                try { document.Editor.WriteMessage("\nSome temporary graphics could not be cleared. Run REGEN to refresh the display."); }
                catch (System.Exception) { }
        }
        if (keep)
        {
            VerifyNetworkSource(document, original);
            SaveNetworkReview(document, original, proposal, result, scale);
        }
        void Draw(JsonObject network, short color)
        {
            foreach (var span in (JsonArray)network["spans"]!)
            {
                if (span is null) continue;
                var points = ReadPoints(span["points"], "network span");
                for (var index = 0; index + 1 < points.Count; index++)
                {
                    var line = new Line(Scale(points[index], scale), Scale(points[index + 1], scale))
                    { Color = Color.FromColorIndex(ColorMethod.ByAci, failedIds.Contains(Text(span, "id")) ? (short)1 : color) };
                    if (!manager.AddTransient(line, TransientDrawingMode.Main, 128, viewports))
                    { line.Dispose(); throw new InvalidOperationException("Could not display a network span."); }
                    transients.Add(line);
                }
            }
        }
    }

    private static void SaveNetworkReview(Document document, JsonObject original, JsonObject? proposal,
        JsonObject result, double scale)
    {
        // Do not rely on an earlier preview check: an intervening edit invalidates approval.
        VerifyNetworkSource(document, original);
        using var transaction = document.Database.TransactionManager.StartTransaction();
        var layers = (LayerTable)transaction.GetObject(document.Database.LayerTableId, OpenMode.ForRead);
        var blocks = (BlockTable)transaction.GetObject(document.Database.BlockTableId, OpenMode.ForRead);
        var model = (BlockTableRecord)transaction.GetObject(blocks[BlockTableRecord.ModelSpace], OpenMode.ForWrite);
        AddNetwork(original, NetworkOriginalLayer, 4);
        if (proposal is not null) AddNetwork(proposal, NetworkProposalLayer, 3);
        transaction.Commit();
        document.Editor.WriteMessage($"\nSaved review centerlines on {NetworkOriginalLayer} and {NetworkProposalLayer}. Source solids were preserved. Use UNDO to remove this addition. Reinspect before another proposal; the drawing revision changed.");
        void AddNetwork(JsonObject network, string layerName, short color)
        {
            ObjectId layerId;
            if (layers.Has(layerName))
            {
                layerId = layers[layerName];
                var existing = (LayerTableRecord)transaction.GetObject(layerId, OpenMode.ForRead);
                if (existing.IsLocked || existing.IsFrozen || existing.IsOff)
                    throw new InvalidOperationException($"Review layer {layerName} must be editable and visible.");
            }
            else
            {
                if (!layers.IsWriteEnabled) layers.UpgradeOpen();
                using var layer = new LayerTableRecord { Name = layerName, Color = Color.FromColorIndex(ColorMethod.ByAci, color) };
                layerId = layers.Add(layer);
                transaction.AddNewlyCreatedDBObject(layer, true);
            }
            foreach (var span in (JsonArray)network["spans"]!)
            {
                if (span is null) continue;
                var points = ReadPoints(span["points"], "review span").Select(point => Scale(point, scale)).ToArray();
                using var route = new Polyline3d(Poly3dType.SimplePoly, new Point3dCollection(points), false);
                route.SetDatabaseDefaults(document.Database);
                route.LayerId = layerId;
                route.Color = Color.FromColorIndex(ColorMethod.ByAci, color);
                model.AppendEntity(route);
                transaction.AddNewlyCreatedDBObject(route, true);
                route.CreateExtensionDictionary();
                var dictionary = (DBDictionary)transaction.GetObject(route.ExtensionDictionary, OpenMode.ForWrite);
                using var record = new Xrecord();
                var metadata = new JsonObject
                {
                    ["span_id"] = span["id"]?.DeepClone(), ["cad_handle"] = span["cad_handle"]?.DeepClone(),
                    ["system_id"] = span["system_id"]?.DeepClone(), ["pipe_id"] = span["pipe_id"]?.DeepClone(),
                    ["from_node"] = span["from_node"]?.DeepClone(), ["to_node"] = span["to_node"]?.DeepClone(),
                    ["instance_path"] = span["instance_path"]?.DeepClone(), ["source_instances"] = span["source_instances"]?.DeepClone(),
                    ["source_revision"] = result["source_revision"]?.DeepClone(), ["source_digest"] = result["source_digest"]?.DeepClone(),
                    ["proposal_id"] = result["proposal_id"]?.DeepClone(), ["review_only"] = true
                }.ToJsonString();
                record.Data = new ResultBuffer(Enumerable.Range(0, (metadata.Length + 239) / 240)
                    .Select(index => new TypedValue((int)DxfCode.Text, metadata.Substring(index * 240, Math.Min(240, metadata.Length - index * 240)))).ToArray());
                dictionary.SetAt("PIPEFORGE4_SOURCE", record);
                transaction.AddNewlyCreatedDBObject(record, true);
            }
        }
    }
}
