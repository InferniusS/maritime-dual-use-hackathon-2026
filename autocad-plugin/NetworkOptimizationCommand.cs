using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Runtime;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Application;
using Forms = System.Windows.Forms;

namespace Maritime.AutoCAD;

public sealed partial class PreviewCommand
{
    /// <summary>Optimize the complete declared environment while limiting permitted changes.</summary>
    [CommandMethod("PIPEFORGEOPTIMIZE", CommandFlags.Modal)]
    public static void OptimizeConnectedNetwork()
    {
        var document = AcApplication.DocumentManager.MdiActiveDocument;
        if (document is null) return;
        var editor = document.Editor;
        try
        {
            var source = OpenNetworkJson(editor, "Open reviewed connected-network JSON");
            if (source is null) return;
            var network = source.Value.Json;
            VerifyNetworkSource(document, network);
            var selected = SelectOptimizationSpans(document, network);
            if (selected is null || selected.Length == 0) return;
            var choice = AskKeyword(editor, "Optimization objective", ["Balanced", "Length", "Bends", "Hydraulic", "Changes", "Feasibility"], "Balanced");
            if (choice is null) return;
            var strategy = choice switch
            {
                "Length" => "length", "Bends" => "bends", "Hydraulic" => "hydraulic",
                "Changes" => "minimal_modifications", "Feasibility" => "feasibility", _ => "balanced"
            };
            var budget = editor.GetInteger(new PromptIntegerOptions("\nMaximum coordinated candidate evaluations [1-64]: ")
            { DefaultValue = 24, UseDefaultValue = true, LowerLimit = 1, UpperLimit = 64 });
            if (budget.Status != PromptStatus.OK) return;
            if (!AskNumber(editor, "Maximum search duration in seconds [1-120]", 30, false, out var seconds)) return;
            if (seconds < 1 || seconds > 120) throw new InvalidOperationException("Search duration must be between 1 and 120 seconds.");
            VerifyNetworkSource(document, network);
            using var client = CreateClient();
            CheckService(client);
            var health = SendServiceJson(client, HttpMethod.Get, "/api/health");
            if (health["capabilities"] is not JsonArray capabilities
                || !capabilities.Any(value => value?.GetValue<string>() == "industrial_network_v51"))
                throw new InvalidOperationException("Update and restart the Python service. This command requires industrial_network_v51 for bounded industrial-network routing and measured progress.");
            editor.WriteMessage($"\nSubmitting {selected.Length} selected spans for coordinated optimization; fixed/preserved spans remain protected and the entire declared network remains in validation. Strategy: {strategy}. Source geometry is read-only.");
            var job = PostJson(client, "/api/network/jobs", new
            {
                network, selected_span_ids = selected, strategy, max_evaluations = budget.Value, max_seconds = seconds
            });
            var identifier = Text(job, "id");
            if (identifier.Length != 32 || identifier.Any(character => !Uri.IsHexDigit(character)))
                throw new InvalidOperationException("The local service returned an invalid job identity.");
            using var progress = new NetworkProgressDialog(client, identifier);
            AcApplication.ShowModalDialog(progress);
            if (progress.Failure is not null) throw new InvalidOperationException(progress.Failure);
            if (progress.Response is not JsonObject response)
            {
                editor.WriteMessage("\nNetwork optimization cancelled. No result was approved, exported, or applied.");
                return;
            }
            // A modal progress window does not constitute proof that the drawing stayed unchanged.
            VerifyNetworkSource(document, network);
            var result = response["result"] as JsonObject ?? throw new InvalidOperationException("No calculated network result was returned.");
            var original = result["original_network"] as JsonObject ?? throw new InvalidOperationException("The calculation has no source network.");
            var proposed = result["proposed_network"] as JsonObject;
            VerifyNetworkSource(document, original);
            if (Text(result, "source_revision") != Text(original["drawing"]!, "revision"))
                throw new InvalidOperationException("The calculated proposal has an inconsistent source revision.");
            var checkedResult = PostJson(client, "/api/network/validate", new { network = proposed ?? original, reference = network });
            if (Text(checkedResult, "source_digest") != Text(result, "source_digest"))
                throw new InvalidOperationException("The result is not bound to the reviewed source network.");
            var validation = checkedResult["validation"] as JsonObject ?? throw new InvalidOperationException("No independent validation was returned.");
            VerifyNetworkSource(document, original);
            editor.WriteMessage($"\nCalculated status: {Text(result, "status")}; hard failures: {Number(validation, "hard_failures"):0}; unverified: {Number(validation, "unverified"):0}. This bounded search does not prove global optimality.");
            WriteNetworkChecks(editor, validation);
            WriteNetworkCoordinates(editor, proposed ?? original);
            if (AskKeyword(editor, "Inspect original/proposed temporary overlays", ["No", "Yes"], "No") == "Yes")
            {
                VerifyNetworkSource(document, original);
                ShowNetworkOverlay(document, original, proposed, validation, result, allowKeep: false);
            }
            VerifyNetworkSource(document, original);
            if (Number(validation, "hard_failures") > 0)
            {
                editor.WriteMessage("\nHard failures remain. This proposal cannot be approved for implementation export. Original geometry was preserved.");
                return;
            }
            editor.WriteMessage("\nApproval authorizes a review/instruction bundle only. UNVERIFIED requirements still need engineering evidence; no source solids will be replaced.");
            if (AskKeyword(editor, "Approve this exact proposal and export its instructions", ["No", "Yes"], "No") != "Yes") return;
            var target = editor.GetFileNameForSave(new PromptSaveFileOptions("\nSelect location for a new PipeForge review bundle (JSON): ")
            { Filter = "PipeForge result (*.json)|*.json" });
            if (target.Status != PromptStatus.OK) return;
            VerifyNetworkSource(document, original);
            var approved = PostJson(client, "/api/network/export", new
            {
                network, review_token = Text(response, "review_token"), proposal_id = Text(result, "proposal_id"), approved = true
            });
            VerifyNetworkSource(document, original);
            var folder = DownloadNetworkBundle(client, approved, target.StringResult, () => VerifyNetworkSource(document, original));
            VerifyNetworkSource(document, original);
            editor.WriteMessage($"\nApproved review bundle: {folder}. Source handles and exact coordinates are retained. Nothing was applied to source solids.");
            if (AskKeyword(editor, "Add separate undoable review centerlines", ["Discard", "Keep"], "Discard") == "Keep")
            {
                VerifyNetworkSource(document, original);
                var units = original["drawing"]?["source_units"]?.GetValue<string>()
                    ?? throw new InvalidOperationException("Source drawing units are required before saving review centerlines.");
                SaveNetworkReview(document, original, proposed, result, UnitScale(units));
            }
        }
        catch (System.Exception exception) { editor.WriteMessage($"\nPipeForge coordinated optimization stopped: {exception.Message}"); }
    }

    private static string[]? SelectOptimizationSpans(Document document, JsonObject network)
    {
        var editor = document.Editor;
        var spans = (network["spans"] as JsonArray)?.OfType<JsonObject>().ToArray()
            ?? throw new InvalidOperationException("The reviewed network has no spans.");
        var mode = AskKeyword(editor, "Permit coordinated changes to", ["Multiple", "Systems", "All"], "Multiple");
        if (mode is null) return null;
        if (mode == "All") return spans.Select(span => Text(span, "id")).ToArray();
        if (mode == "Systems")
        {
            var systems = spans.Select(span => Text(span, "system_id")).Distinct(StringComparer.Ordinal).OrderBy(value => value).ToArray();
            for (var index = 0; index < systems.Length; index++) editor.WriteMessage($"\n{index + 1}: {systems[index]}");
            var input = editor.GetString(new PromptStringOptions("\nSystem numbers, separated by commas: ") { AllowSpaces = true });
            if (input.Status != PromptStatus.OK) return null;
            var chosen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var token in input.StringResult.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
            {
                if (!int.TryParse(token, NumberStyles.None, CultureInfo.InvariantCulture, out var index) || index < 1 || index > systems.Length)
                    throw new InvalidOperationException("Choose only the displayed system numbers.");
                chosen.Add(systems[index - 1]);
            }
            if (chosen.Count == 0) throw new InvalidOperationException("Select at least one system.");
            return spans.Where(span => chosen.Contains(Text(span, "system_id"))).Select(span => Text(span, "id")).ToArray();
        }
        var selection = editor.GetSelection(new PromptSelectionOptions
        { MessageForAdding = "\nSelect multiple traced source pipe solids/blocks; surrounding spans stay in validation: ", AllowDuplicates = false },
            new SelectionFilter([new TypedValue((int)DxfCode.Start, "3DSOLID,INSERT")]));
        if (selection.Status != PromptStatus.OK) return null;
        if (selection.Value.Count is < 1 or > 256) throw new InvalidOperationException("Select between 1 and 256 source entities.");
        var handles = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        using (var transaction = document.Database.TransactionManager.StartTransaction())
            foreach (var id in selection.Value.GetObjectIds())
                if (transaction.GetObject(id, OpenMode.ForRead) is Entity entity) handles.Add(entity.Handle.ToString());
        var matched = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        bool Matches(JsonObject span)
        {
            IEnumerable<string> SourceHandles()
            {
                if (span["cad_handle"] is JsonValue direct) yield return direct.GetValue<string>();
                if (span["instance_path"] is JsonArray path)
                    foreach (var item in path.OfType<JsonValue>()) yield return item.GetValue<string>();
                if (span["source_instances"] is JsonArray instances)
                    foreach (var source in instances.OfType<JsonObject>())
                    {
                        if (source["source_handle"] is JsonValue handle) yield return handle.GetValue<string>();
                        if (source["instance_path"] is JsonArray placement)
                            foreach (var item in placement.OfType<JsonValue>()) yield return item.GetValue<string>();
                    }
            }
            var sourceHandles = SourceHandles().Select(handle => handle.Split('[')[0]).Where(handles.Contains).ToArray();
            foreach (var handle in sourceHandles) matched.Add(handle);
            return sourceHandles.Length > 0;
        }
        var selected = spans.Where(Matches).Select(span => Text(span, "id")).ToArray();
        if (selected.Length == 0) throw new InvalidOperationException("No selected source handles match reviewed network spans. Reinspect the source selection.");
        if (handles.Any(handle => !matched.Contains(handle)))
            throw new InvalidOperationException("Some selected source objects do not map to reviewed spans. Reinspect them; no selected object will be silently omitted.");
        return selected;
    }

    private static string DownloadNetworkBundle(HttpClient client, JsonObject approved, string target, Action verifySource)
    {
        if (approved["approved"]?.GetValue<bool>() != true || approved["exports"] is not JsonObject exports)
            throw new InvalidOperationException("The service did not approve an export bundle.");
        var allowed = new HashSet<string>(["instructions.json", "instructions.md", "components.xlsx", "components.csv", "network-preview.dxf", "network-result.json", "network-comparison.svg", "coordinates.csv"], StringComparer.Ordinal);
        // A fresh folder avoids overwriting earlier engineering deliverables.
        verifySource();
        var folder = Path.Combine(Path.GetDirectoryName(Path.GetFullPath(target))!, $"{Path.GetFileNameWithoutExtension(target)}-{Guid.NewGuid():N}");
        Directory.CreateDirectory(folder);
        try
        {
            foreach (var item in exports)
            {
                verifySource();
                var path = item.Value?.GetValue<string>() ?? throw new InvalidOperationException("Empty export URL.");
                var filename = path.Split('/').Last();
                var identifier = Text(approved, "export_id");
                if (identifier.Length != 32 || identifier.Any(character => !Uri.IsHexDigit(character)) || !allowed.Contains(filename)
                    || path != $"/api/network/downloads/{identifier}/{filename}")
                    throw new InvalidOperationException("The service returned an unexpected export path.");
                using var reply = client.GetAsync(path, HttpCompletionOption.ResponseHeadersRead).GetAwaiter().GetResult();
                reply.EnsureSuccessStatusCode();
                if (reply.Content.Headers.ContentLength is long length && length > NetworkJsonLimitBytes)
                    throw new InvalidOperationException("Export exceeds the 32 MB download limit.");
                using var source = reply.Content.ReadAsStream();
                using var file = File.Create(Path.Combine(folder, filename));
                var buffer = new byte[65536];
                long count = 0;
                int read;
                while ((read = source.Read(buffer, 0, buffer.Length)) > 0)
                {
                    count += read;
                    if (count > NetworkJsonLimitBytes) throw new InvalidOperationException("Export exceeds the 32 MB download limit.");
                    file.Write(buffer, 0, read);
                }
            }
            if (allowed.Any(name => !File.Exists(Path.Combine(folder, name))))
                throw new InvalidOperationException("The approved bundle is incomplete.");
            verifySource(); // Failure removes this folder, including already-downloaded files.
            return folder;
        }
        catch
        {
            Directory.Delete(folder, recursive: true);
            throw;
        }
    }

    /// <summary>Local API polling; Cancel requests cooperative server cancellation, never approval.</summary>
    private sealed class NetworkProgressDialog : Forms.Form
    {
        private readonly HttpClient client;
        private readonly string identifier;
        private readonly Forms.Timer timer = new() { Interval = 400 };
        private readonly Forms.Label status = new() { Dock = Forms.DockStyle.Fill, AutoSize = false, Padding = new Forms.Padding(16) };
        private readonly Forms.Button cancel = new() { Text = "Cancel calculation", Dock = Forms.DockStyle.Bottom, Height = 40 };
        private readonly DateTime started = DateTime.UtcNow;
        private bool polling, cancelRequested, finishing;
        public JsonObject? Response { get; private set; }
        public string? Failure { get; private set; }
        public NetworkProgressDialog(HttpClient client, string identifier)
        {
            this.client = client;
            this.identifier = identifier;
            Text = "PipeForge coordinated network calculation";
            Width = 730; Height = 310; MinimizeBox = false; MaximizeBox = false;
            FormBorderStyle = Forms.FormBorderStyle.FixedDialog;
            StartPosition = Forms.FormStartPosition.CenterParent;
            status.Text = "Queued locally. No source geometry will be changed.";
            Controls.Add(status); Controls.Add(cancel);
            timer.Tick += async (_, _) => await Poll();
            cancel.Click += async (_, _) => await RequestCancel();
            FormClosing += (_, args) =>
            {
                if (finishing) return;
                args.Cancel = true;
                _ = RequestCancel();
            };
            Shown += (_, _) => timer.Start();
        }
        private async Task<JsonObject> Request(HttpMethod method, string path)
        {
            using var request = new HttpRequestMessage(method, path);
            request.Headers.ConnectionClose = true;
            if (method == HttpMethod.Post) request.Content = new StringContent("{}", Encoding.UTF8, "application/json");
            using var reply = await client.SendAsync(request);
            var body = await reply.Content.ReadAsStringAsync();
            var json = JsonNode.Parse(body) as JsonObject ?? throw new InvalidOperationException("Invalid job response.");
            if (!reply.IsSuccessStatusCode) throw new InvalidOperationException(json["error"]?.GetValue<string>() ?? "The local calculation service failed.");
            return json;
        }
        private async Task RequestCancel()
        {
            if (cancelRequested || finishing) return;
            cancelRequested = true; cancel.Enabled = false;
            status.Text = "Cancellation requested. Waiting for the local engine to stop safely…";
            try { await Request(HttpMethod.Post, $"/api/network/jobs/{identifier}/cancel"); }
            catch (System.Exception exception) { Finish(null, $"Could not confirm cancellation: {exception.Message}. No result was approved or applied."); }
        }
        private async Task Poll()
        {
            if (polling || finishing) return;
            polling = true;
            try
            {
                var job = await Request(HttpMethod.Get, $"/api/network/jobs/{identifier}");
                var state = job["status"]?.GetValue<string>();
                if (state == "complete")
                {
                    if (!cancelRequested && job["response"] is not JsonObject)
                        throw new InvalidOperationException("The completed calculation has no result.");
                    Finish(cancelRequested ? null : job["response"] as JsonObject, null);
                }
                else if (state == "cancelled") Finish(null, null);
                else if (state == "failed") Finish(null, job["error"]?.GetValue<string>() ?? "Network calculation failed.");
                else if (state is "queued" or "running")
                {
                    if (!cancelRequested) status.Text = NetworkProgressStatus.Format(job, (DateTime.UtcNow - started).TotalSeconds);
                    if ((DateTime.UtcNow - started).TotalSeconds > 150) await RequestCancel();
                }
                else throw new InvalidOperationException("Unknown network job state.");
            }
            catch (System.Exception exception) { Finish(null, $"Lost contact with the local calculation: {exception.Message}. No result was approved or applied."); }
            finally { polling = false; }
        }
        private void Finish(JsonObject? response, string? failure)
        {
            if (finishing) return;
            finishing = true; timer.Stop(); Response = response; Failure = failure; Close();
        }
        protected override void Dispose(bool disposing)
        {
            if (disposing) timer.Dispose();
            base.Dispose(disposing);
        }
    }
}
