using System.Collections.Concurrent;
using System.Diagnostics;
using System.Net.Http;
using System.Text.Json.Nodes;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.GraphicsInterface;
using Autodesk.AutoCAD.Runtime;

namespace PipeForge.AutoCAD;

internal sealed class CadSession : IDisposable
{
    private readonly Document _document;
    private readonly Metadata _metadata;
    private readonly Dictionary<string, (JsonObject Object, bool IsPipe)> _templates;
    private readonly EdgeClient _client;
    private readonly SemaphoreSlim _network = new(1, 1);
    private readonly CancellationTokenSource _cancellation = new();
    private readonly CancellationToken _token;
    private readonly ConcurrentDictionary<string, bool> _dirty = new(StringComparer.OrdinalIgnoreCase);
    private readonly List<Entity> _preview = [];
    private readonly List<ObjectId> _highlighted = [];
    private long _changedAt;
    private long _epoch;
    private long _resultEpoch = -1;
    private int _revision;
    private int _synchronizationsInFlight;
    private string? _sessionId;
    private JsonObject? _lastResult;
    private JsonObject? _proposal;
    private volatile bool _disposed;
    private volatile bool _syncFaulted;
    private bool _unmappedNotice;
    private bool _coverageDirty = true;
    private long _sourceGeneration;
    private JsonObject _extractionCoverage = new();
    private bool _liveEnabled;
    private bool _liveInFlight;
    private bool _livePending;
    private long _jobGeneration;
    private int _manualOptimizations;
    private long _lastOptimizationEpoch;
    private readonly double _scale;
    private readonly Uri _companionBase;

    public CadSession(Document document, Metadata metadata, Uri endpoint)
    {
        _document = document;
        _metadata = metadata;
        _templates = new Dictionary<string, (JsonObject, bool)>(StringComparer.Ordinal);
        foreach (var name in new[] { "pipes", "obstacles" })
            foreach (JsonObject item in (JsonArray)metadata.Model[name]!)
                _templates.Add(item["id"]!.GetValue<string>(), ((JsonObject)item.DeepClone(), name == "pipes"));
        _client = new EdgeClient(endpoint);
        _companionBase = new Uri(endpoint.AbsoluteUri.TrimEnd('/') + "/");
        _token = _cancellation.Token;
        _scale = DrawingReader.MetersPerUnit(document.Database);
    }

    private void Print(string text) => _document.Editor.WriteMessage("\nPIPEFORGE: " + text);
    private void Ui(Action action) => PipeForgePlugin.Post(() => { if (!_disposed) action(); });
    private static string Segment(string value) => Uri.EscapeDataString(value);
    private string SessionPath => "api/edge/sessions/" + Segment(_sessionId ?? throw new InvalidOperationException("Connection is still starting."));

    public void Connect()
    {
        // The caller is an AutoCAD command. Capture before starting any asynchronous work.
        if (_metadata.ObjectMap.Count == 0)
            throw new InvalidDataException("Supply at least one explicit engineering-ID → CAD-handle mapping in object_map.");
        _ = Capture(_metadata.ObjectMap.Select(item => new KeyValuePair<string, bool>(item.Value, false)), requireLive: true);
        var model = _metadata.Model.DeepClone();
        var map = ActiveMap();
        var coverage = _extractionCoverage.DeepClone();
        var coordinateTransform = new JsonObject
        {
            ["source_units"] = DrawingReader.SourceUnits(_document.Database), ["meters_per_unit"] = _scale
        };
        _document.Database.ObjectModified += Modified;
        _document.Database.ObjectAppended += Appended;
        _document.Database.ObjectErased += Erased;
        var drawingId = string.IsNullOrWhiteSpace(_document.Name) ? _metadata.Model["id"]!.GetValue<string>() : _document.Name;
        Background(async () =>
        {
            await _network.WaitAsync(_token).ConfigureAwait(false);
            try
            {
                var snapshot = await _client.JsonAsync(HttpMethod.Post, "api/edge/sessions", new JsonObject
                {
                    ["drawing_id"] = drawingId, ["model"] = model, ["object_map"] = map, ["source_adapter"] = "autocad",
                    ["coordinate_transform"] = coordinateTransform, ["extraction_coverage"] = coverage
                }, _token).ConfigureAwait(false);
                _sessionId = snapshot["id"]!.GetValue<string>();
                Interlocked.Exchange(ref _revision, snapshot["revision"]!.GetValue<int>());
                Ui(() => Print($"Connected at revision {_revision}; {_metadata.ObjectMap.Count} mapped entities; {_scale:G8} m/drawing unit. Coverage: {_extractionCoverage["status"]}; {_extractionCoverage["unmapped_entities"]} unclassified ModelSpace entities. Incomplete/unverified coverage blocks approval and export. Source DWG is read only."));
            }
            finally { _network.Release(); }
        }, synchronization: true);
    }

    private JsonObject ActiveMap()
    {
        var map = new JsonObject();
        var activeIds = new HashSet<string>(((JsonArray)_metadata.Model["pipes"]!).Concat((JsonArray)_metadata.Model["obstacles"]!)
            .OfType<JsonObject>().Select(item => item["id"]!.GetValue<string>()));
        foreach (var item in _metadata.ObjectMap)
            if (activeIds.Contains(item.Key)) map[item.Key] = item.Value;
        return map;
    }

    private void RecordObject(DBObject value, bool erased)
    {
        // Vertex edits may notify the child rather than the Polyline3d. OwnerId is an
        // event identifier, not a background database traversal or semantic inference.
        if (value is PolylineVertex3d && !value.OwnerId.IsNull)
            Record(value.OwnerId.Handle.ToString(), false);
        else if (value is Entity) Record(value.Handle.ToString(), erased);
    }

    private void Modified(object? sender, ObjectEventArgs args) => RecordObject(args.DBObject, args.DBObject.IsErased);
    private void Erased(object? sender, ObjectErasedEventArgs args) => RecordObject(args.DBObject, args.Erased);
    private void Appended(object? sender, ObjectEventArgs args) => RecordObject(args.DBObject, false);

    private void Record(string handle, bool erased)
    {
        // Event handlers capture only identifiers. No transaction, HTTP, or geometry traversal here.
        if (_disposed) return;
        if (_metadata.ObjectMap.Values.Contains(handle, StringComparer.OrdinalIgnoreCase)) _dirty[handle] = erased;
        else if (!_unmappedNotice)
        {
            _unmappedNotice = true;
            Ui(() => Print("Unclassified source entity changed. Existing results are invalid; ModelSpace coverage will be rescanned. No system, size or obstacle semantics are inferred. Unresolved coverage blocks approval and export."));
        }
        _coverageDirty = true;
        Interlocked.Increment(ref _sourceGeneration);
        Interlocked.Increment(ref _epoch);
        Interlocked.Exchange(ref _changedAt, Environment.TickCount64);
    }

    public void OnIdle()
    {
        if (_disposed || _syncFaulted) return;
        try { RequireConsistentUnits(); }
        catch (System.Exception error) { ClearVisuals(); Print(error.Message); return; }
        if ((_dirty.IsEmpty && !_coverageDirty) || Environment.TickCount64 - Interlocked.Read(ref _changedAt) < 400) return;
        ClearVisuals();
        var dirty = new List<KeyValuePair<string, bool>>();
        foreach (var key in _dirty.Keys)
            if (_dirty.TryRemove(key, out var erased)) dirty.Add(new(key, erased));
        try
        {
            var delta = Capture(dirty);
            Background(async () =>
            {
                await PushDelta(delta).ConfigureAwait(false);
                Ui(ScheduleLiveAnalysis);
            }, synchronization: true);
        }
        catch (System.Exception error)
        {
            _syncFaulted = true;
            Print("CAD capture stopped: " + error.Message + " Correct the mapping/geometry and reconnect with PFCONNECT.");
        }
    }

    private JsonObject Capture(IEnumerable<KeyValuePair<string, bool>> handles, bool requireLive = false)
    {
        try { return CaptureCore(handles, requireLive); }
        catch { _syncFaulted = true; throw; }
    }

    private JsonObject CaptureCore(IEnumerable<KeyValuePair<string, bool>> handles, bool requireLive)
    {
        if (Math.Abs(DrawingReader.MetersPerUnit(_document.Database) - _scale) > 1e-12)
            throw new InvalidDataException("INSUNITS changed; reconnect to establish a consistent transform.");
        var nextModel = (JsonObject)_metadata.Model.DeepClone();
        var geometryChanged = false;
        JsonObject coverage;
        var pipes = new JsonArray(); var obstacles = new JsonArray();
        var removedPipes = new JsonArray(); var removedObstacles = new JsonArray();
        using (_document.LockDocument())
        using (var transaction = _document.Database.TransactionManager.StartTransaction())
        {
            foreach (var change in handles.DistinctBy(item => item.Key, StringComparer.OrdinalIgnoreCase))
            {
                var binding = _metadata.ObjectMap.FirstOrDefault(item => item.Value.Equals(change.Key, StringComparison.OrdinalIgnoreCase));
                if (binding.Key == null) continue;
                if (requireLive && IsErased(change.Key))
                    throw new InvalidDataException($"Initial CAD binding {binding.Key} → {change.Key} is erased. Correct the mapping before connecting.");
                var template = _templates[binding.Key];
                var collection = (JsonArray)nextModel[template.IsPipe ? "pipes" : "obstacles"]!;
                var old = collection.OfType<JsonObject>().FirstOrDefault(item => item["id"]?.GetValue<string>() == binding.Key);
                if (old != null) collection.Remove(old);
                // Events are dirty hints; the stable database state is authoritative,
                // including an erase followed by an undo within one debounce window.
                var erased = IsErased(change.Key);
                if (erased && old != null)
                {
                    geometryChanged = true;
                    (template.IsPipe ? removedPipes : removedObstacles).Add(binding.Key);
                }
                else if (!erased)
                {
                    var item = DrawingReader.ReadObject(_document.Database, transaction, template.Object, change.Key, template.IsPipe, _scale);
                    geometryChanged |= !JsonNode.DeepEquals(old, item);
                    collection.Add(item.DeepClone());
                    (template.IsPipe ? pipes : obstacles).Add(item);
                }
            }
            // An event already invalidated this state. Only a missed geometry change
            // needs another generation/epoch; no-op scans must preserve approvals.
            if (geometryChanged && !_coverageDirty)
            {
                Interlocked.Increment(ref _epoch);
                Interlocked.Increment(ref _sourceGeneration);
            }
            coverage = SourceCoverage.Scan(_document.Database, transaction, nextModel, _metadata.ObjectMap, Interlocked.Read(ref _sourceGeneration));
            // Read-only transaction: no entity is ever opened ForWrite.
            transaction.Commit();
        }
        _metadata.Model["pipes"] = nextModel["pipes"]!.DeepClone();
        _metadata.Model["obstacles"] = nextModel["obstacles"]!.DeepClone();
        _extractionCoverage = coverage;
        _coverageDirty = false;
        return new JsonObject
        {
            ["upsert_pipes"] = pipes, ["remove_pipe_ids"] = removedPipes,
            ["upsert_obstacles"] = obstacles, ["remove_obstacle_ids"] = removedObstacles, ["object_map"] = ActiveMap(),
            ["extraction_coverage"] = coverage.DeepClone(), ["compact"] = true
        };
    }

    private async Task PushDelta(JsonObject delta)
    {
        Interlocked.Increment(ref _synchronizationsInFlight);
        var acquired = false;
        try
        {
            await _network.WaitAsync(_token).ConfigureAwait(false);
            acquired = true;
            var previousRevision = Volatile.Read(ref _revision);
            delta["base_revision"] = previousRevision;
            var snapshot = await _client.JsonAsync(HttpMethod.Patch, SessionPath, delta, _token).ConfigureAwait(false);
            var nextRevision = snapshot["revision"]!.GetValue<int>();
            Interlocked.Exchange(ref _revision, nextRevision);
            Ui(() => Print(nextRevision == previousRevision
                ? $"Source synchronized: revision {nextRevision} is unchanged."
                : $"Live delta synchronized: revision {nextRevision}. Previous results require re-analysis."));
        }
        catch { _syncFaulted = true; throw; }
        finally
        {
            if (acquired) _network.Release();
            Interlocked.Decrement(ref _synchronizationsInFlight);
        }
    }

    public void OpenCompanion()
    {
        EnsureSynchronized();
        var url = new Uri(_companionBase, "?edge_session=" + Segment(_sessionId!)).AbsoluteUri;
        Print("Companion for this native session: " + url);
        if (!_dirty.IsEmpty || _coverageDirty || Volatile.Read(ref _synchronizationsInFlight) != 0)
            Print("Native edits are still synchronizing; wait for the revision update before reviewing browser results.");
        try { Process.Start(new ProcessStartInfo { FileName = url, UseShellExecute = true }); }
        catch (System.Exception error) { Print("Open the printed URL manually; browser launch failed: " + error.Message); }
    }

    public void RefreshCompanion()
    {
        EnsureSynchronized();
        if (!_dirty.IsEmpty || _coverageDirty || Volatile.Read(ref _synchronizationsInFlight) != 0)
            throw new InvalidOperationException("Native drawing edits are pending synchronization. Wait for the revision update, then PFREFRESH again.");
        // Recapture even when no event was observed. This prevents a browser proposal from
        // being adopted against geometry that changed through an unsupported event path.
        ClearVisuals();
        var delta = Capture(_metadata.ObjectMap.Select(item => new KeyValuePair<string, bool>(item.Value, IsErased(item.Value))));
        var epoch = Interlocked.Read(ref _epoch);
        var generation = Interlocked.Increment(ref _jobGeneration);
        var selectedId = _proposal?["id"]?.GetValue<string>();
        var expectedSession = _sessionId!;
        Background(async () =>
        {
            await PushDelta(delta).ConfigureAwait(false);
            JsonObject snapshot;
            int revision;
            await _network.WaitAsync(_token).ConfigureAwait(false);
            try
            {
                revision = Volatile.Read(ref _revision);
                snapshot = await _client.JsonAsync(HttpMethod.Get, SessionPath, null, _token).ConfigureAwait(false);
            }
            finally { _network.Release(); }
            if (snapshot["id"]?.GetValue<string>() != expectedSession ||
                snapshot["source_adapter"]?.GetValue<string>() != "autocad" ||
                snapshot["revision"]?.GetValue<int>() != revision)
            {
                _syncFaulted = true;
                throw new InvalidOperationException("Companion session/revision diverged from the native source. Reconnect using PFCONNECT; remote geometry was not adopted.");
            }
            Ui(() =>
            {
                EnsureSynchronized();
                if (epoch != Interlocked.Read(ref _epoch) || generation != Interlocked.Read(ref _jobGeneration) ||
                    revision != Volatile.Read(ref _revision) || !_dirty.IsEmpty || _coverageDirty || Volatile.Read(ref _synchronizationsInFlight) != 0)
                {
                    Print("Companion reply discarded because native state changed. Wait for synchronization, then PFREFRESH again.");
                    return;
                }
                var proposals = new JsonArray();
                foreach (JsonObject proposal in snapshot["proposals"] as JsonArray ?? new JsonArray())
                    if (proposal["source_revision"]?.GetValue<int>() == revision &&
                        proposal["status"]?.GetValue<string>() is "pending" or "approved" or "rejected")
                        proposals.Add(proposal.DeepClone());
                var analysis = snapshot["analysis"] as JsonObject;
                var recommendations = analysis?["source_revision"]?.GetValue<int>() == revision
                    ? analysis["recommendations"]?.DeepClone() : null;
                _lastResult = new JsonObject { ["proposals"] = proposals, ["recommendations"] = recommendations ?? new JsonArray() };
                _resultEpoch = epoch;
                _proposal = proposals.OfType<JsonObject>().FirstOrDefault(item => item["id"]?.GetValue<string>() == selectedId)
                    ?? proposals.OfType<JsonObject>().FirstOrDefault();
                Print($"Companion refreshed at native revision {revision}: {proposals.Count} current proposals. No remote model or CAD geometry was applied.");
                for (var index = 0; index < proposals.Count; index++)
                    Print($"Proposal {index + 1}: {proposals[index]!["id"]!.GetValue<string>()}, {proposals[index]!["status"]!.GetValue<string>()}. Use PFPREVIEW to select; PFEXPORT requires approved status.");
            });
        }, synchronization: false);
    }

    public void Map(string engineeringId, ObjectId entityId)
    {
        if (!_templates.ContainsKey(engineeringId)) throw new InvalidDataException("ID is absent from supplied metadata. Add explicit metadata and reconnect first.");
        var handle = entityId.Handle.ToString();
        if (_metadata.ObjectMap.Any(item => item.Key != engineeringId && item.Value.Equals(handle, StringComparison.OrdinalIgnoreCase)))
            throw new InvalidDataException("That entity already maps to another engineering ID.");
        using (var transaction = _document.Database.TransactionManager.StartTransaction())
            _ = DrawingReader.ReadObject(_document.Database, transaction, _templates[engineeringId].Object, handle, _templates[engineeringId].IsPipe, _scale);
        _metadata.ObjectMap[engineeringId] = handle;
        Record(handle, false);
        Print($"Mapped {engineeringId} → {handle}. Mapping is session-local; record it in the metadata file to retain it after reconnect.");
    }

    public void RunJob(string operation)
    {
        EnsureSynchronized();
        ClearVisuals();
        _lastResult = null; _proposal = null;
        // Capture current mapped geometry synchronously; the request contains plain JSON only.
        var delta = Capture(_metadata.ObjectMap.Select(item => new KeyValuePair<string, bool>(item.Value, IsErased(item.Value))));
        _dirty.Clear();
        var epoch = Interlocked.Read(ref _epoch);
        var generation = Interlocked.Increment(ref _jobGeneration);
        if (operation == "optimize") { _manualOptimizations++; _lastOptimizationEpoch = epoch; _livePending = false; }
        Background(async () =>
        {
            try
            {
                await PushDelta(delta).ConfigureAwait(false);
                await ProcessJob(operation, epoch, generation).ConfigureAwait(false);
            }
            finally
            {
                if (operation == "optimize") Ui(() =>
                {
                    _manualOptimizations--;
                    if (_manualOptimizations == 0)
                    {
                        if (_livePending && _lastOptimizationEpoch != Interlocked.Read(ref _epoch)) ScheduleLiveAnalysis();
                        else _livePending = false;
                    }
                });
            }
        }, synchronization: false);
    }

    public void SetLive(bool enabled)
    {
        EnsureSynchronized();
        _liveEnabled = enabled;
        _livePending = enabled;
        Print(enabled ? "Live analysis ON: debounced deltas trigger analysis; one live job at a time. Optimization remains explicit."
            : "Live analysis OFF. Geometry synchronization continues; use PFANALYZE explicitly.");
        if (enabled) ScheduleLiveAnalysis();
    }

    private void ScheduleLiveAnalysis()
    {
        if (!_liveEnabled || _disposed || _syncFaulted || _sessionId == null) return;
        if (_liveInFlight || _manualOptimizations > 0 || !_dirty.IsEmpty || _coverageDirty || Volatile.Read(ref _synchronizationsInFlight) != 0) { _livePending = true; return; }
        _liveInFlight = true;
        _livePending = false;
        var epoch = Interlocked.Read(ref _epoch);
        var generation = Interlocked.Increment(ref _jobGeneration);
        Background(async () =>
        {
            try { await ProcessJob("analyze", epoch, generation).ConfigureAwait(false); }
            finally
            {
                Ui(() =>
                {
                    _liveInFlight = false;
                    if (_livePending) ScheduleLiveAnalysis();
                });
            }
        }, synchronization: false);
    }

    private async Task ProcessJob(string operation, long epoch, long generation)
    {
            JsonObject job;
            int sourceRevision;
            await _network.WaitAsync(_token).ConfigureAwait(false);
            try
            {
                sourceRevision = Volatile.Read(ref _revision);
                job = await _client.JsonAsync(HttpMethod.Post, SessionPath + "/jobs", new JsonObject
                {
                    ["revision"] = sourceRevision, ["operation"] = operation, ["mode"] = "balanced", ["resolution"] = 0.5, ["clearance"] = 0.1
                }, _token).ConfigureAwait(false);
            }
            finally { _network.Release(); }
            var jobId = job["id"]!.GetValue<string>();
            Ui(() => Print($"{operation} job submitted at revision {sourceRevision}. CAD remains interactive."));
            JsonObject status;
            var pollingStarted = Environment.TickCount64;
            do
            {
                if (Environment.TickCount64 - pollingStarted > 180_000)
                    throw new TimeoutException("Job polling exceeded three minutes. Check the server job before re-running.");
                await Task.Delay(250, _token).ConfigureAwait(false);
                status = await _client.JsonAsync(HttpMethod.Get, "api/edge/jobs/" + Segment(jobId), null, _token).ConfigureAwait(false);
            } while (status["status"]?.GetValue<string>() is "pending" or "queued" or "running");
            if (status["status"]?.GetValue<string>() is not ("completed" or "succeeded" or "complete"))
                throw new InvalidOperationException(status["error"]?.ToJsonString() ?? "Edge job failed.");
            var result = status["result"] as JsonObject ?? throw new InvalidDataException("Job result was missing.");
            Ui(() =>
            {
                EnsureSynchronized();
                if (epoch != Interlocked.Read(ref _epoch) || sourceRevision != Volatile.Read(ref _revision) || generation != Interlocked.Read(ref _jobGeneration) || _coverageDirty || Volatile.Read(ref _synchronizationsInFlight) != 0)
                {
                    Print("Result arrived for an older drawing state and was discarded. Run PFANALYZE/PFOPTIMIZE again.");
                    return;
                }
                _lastResult = result; _resultEpoch = epoch;
                _proposal = (result["proposals"] as JsonArray)?.OfType<JsonObject>().FirstOrDefault();
                Print($"{operation} complete. {((JsonArray?)result["recommendations"])?.Count ?? 0} recommendations; {((JsonArray?)result["proposals"])?.Count ?? 0} proposals. PFHIGHLIGHT / PFPREVIEW show results; PFAPPROVE records an engineer decision.");
            });
    }

    private bool IsErased(string handle) => DrawingReader.Resolve(_document.Database, handle).IsErased;

    private void EnsureSynchronized()
    {
        if (_syncFaulted) throw new InvalidOperationException("Synchronization failed. Resolve the reported issue and PFCONNECT again.");
        RequireConsistentUnits();
        if (_sessionId == null) throw new InvalidOperationException("Connection has not completed.");
    }

    private void RequireConsistentUnits()
    {
        // System-variable edits need not raise ObjectModified on a mapped entity.
        // Validate on idle, every decision/preview, and immediately before publishing exports.
        try
        {
            if (Math.Abs(DrawingReader.MetersPerUnit(_document.Database) - _scale) <= 1e-12) return;
        }
        catch (InvalidDataException) { }
        _syncFaulted = true;
        Interlocked.Increment(ref _epoch);
        throw new InvalidDataException("INSUNITS changed or became unsupported. Results are invalid; reconnect using PFCONNECT.");
    }

    private JsonObject CurrentProposal()
    {
        EnsureSynchronized();
        if (_resultEpoch != Interlocked.Read(ref _epoch) || !_dirty.IsEmpty || _coverageDirty || Volatile.Read(ref _synchronizationsInFlight) != 0)
            throw new InvalidOperationException("Drawing changed or is synchronizing; wait and run PFOPTIMIZE/PFREFRESH before preview, decision or export.");
        var proposal = _proposal ?? throw new InvalidOperationException("No current proposal. Run PFOPTIMIZE first.");
        if (proposal["source_revision"]!.GetValue<int>() != Volatile.Read(ref _revision))
            throw new InvalidOperationException("Proposal revision is stale; run PFOPTIMIZE again.");
        return proposal;
    }

    private void RequireCompleteCoverage()
    {
        if (_coverageDirty || _extractionCoverage["status"]?.GetValue<string>() != "complete")
            throw new InvalidOperationException("CAD extraction coverage is incomplete or unverified. Classify every ModelSpace entity with explicit supported metadata and bindings, then synchronize before review or export. Static metadata alone does not establish live source coverage.");
    }

    public void Preview(int oneBasedIndex)
    {
        if (_lastResult?["proposals"] is not JsonArray proposals || oneBasedIndex < 1 || oneBasedIndex > proposals.Count)
            throw new InvalidOperationException("Choose an existing proposal number after PFOPTIMIZE.");
        _proposal = (JsonObject)proposals[oneBasedIndex - 1]!;
        var proposal = CurrentProposal();
        ClearVisuals();
        if (_extractionCoverage["status"]?.GetValue<string>() != "complete")
            Print("UNVERIFIED diagnostic preview: CAD extraction coverage is incomplete or unverified. Omitted source geometry may invalidate route clearances; approval and export remain blocked.");
        var model = proposal["model"] as JsonObject ?? throw new InvalidDataException("Proposal model missing.");
        foreach (JsonObject pipe in (JsonArray)model["pipes"]!)
        {
            var id = pipe["id"]!.GetValue<string>();
            if (_templates.ContainsKey(id) && JsonNode.DeepEquals(pipe["points"], _metadata.Find(id).Object["points"])) continue;
            var points = (JsonArray)pipe["points"]!;
            for (var i = 1; i < points.Count; i++)
            {
                Point3d ReadPoint(JsonNode? node)
                {
                    var point = (JsonArray)node!;
                    return new(point[0]!.GetValue<double>() / _scale, point[1]!.GetValue<double>() / _scale, point[2]!.GetValue<double>() / _scale);
                }
                var line = new Line(ReadPoint(points[i - 1]), ReadPoint(points[i])) { ColorIndex = 4 };
                try
                {
                    TransientManager.CurrentTransientManager.AddTransient(line, TransientDrawingMode.DirectShortTerm, 128, new IntegerCollection());
                    _preview.Add(line);
                }
                catch { line.Dispose(); throw; }
            }
        }
        Print($"Proposal {oneBasedIndex}: cyan transient centerlines only. Originals unchanged; no construction geometry created. PFAPPROVE records a decision; it does not apply geometry.");
    }

    public void Highlight(string? engineeringId)
    {
        EnsureSynchronized();
        if (_resultEpoch != Interlocked.Read(ref _epoch)) throw new InvalidOperationException("Result is stale. Analyze again.");
        ClearVisuals();
        var ids = new HashSet<string>(StringComparer.Ordinal);
        if (!string.IsNullOrWhiteSpace(engineeringId)) ids.Add(engineeringId);
        else foreach (JsonObject recommendation in (JsonArray?)_lastResult?["recommendations"] ?? new JsonArray())
        {
            if (recommendation["pipe_id"] is JsonValue primary) ids.Add(primary.GetValue<string>());
            foreach (var item in recommendation["object_ids"] as JsonArray ?? new JsonArray())
                if (item != null) ids.Add(item.GetValue<string>());
        }
        using (_document.LockDocument())
        using (var transaction = _document.Database.TransactionManager.StartTransaction())
        {
            foreach (var id in ids)
                if (_metadata.ObjectMap.TryGetValue(id, out var handle))
                {
                    var objectId = DrawingReader.Resolve(_document.Database, handle);
                    if (!objectId.IsErased && transaction.GetObject(objectId, OpenMode.ForRead) is Entity entity)
                    { entity.Highlight(); _highlighted.Add(objectId); }
                }
        }
        Print($"Highlighted {_highlighted.Count} mapped source entities. PFCLEAR removes visual overlays.");
    }

    public void Decide(string decision, string engineer, string comment)
    {
        var proposal = CurrentProposal();
        if (decision == "approve") RequireCompleteCoverage();
        // An already-started refresh must not later overwrite this decision with its
        // earlier pending snapshot. A subsequent refresh queues after the decision.
        Interlocked.Increment(ref _jobGeneration);
        var id = proposal["id"]!.GetValue<string>();
        var revision = Volatile.Read(ref _revision);
        var epoch = Interlocked.Read(ref _epoch);
        Background(async () =>
        {
            await _network.WaitAsync(_token).ConfigureAwait(false);
            try
            {
                await _client.JsonAsync(HttpMethod.Post, SessionPath + "/proposals/" + Segment(id) + "/decision", new JsonObject
                {
                    ["revision"] = revision, ["decision"] = decision, ["engineer"] = engineer, ["comment"] = comment
                }, _token).ConfigureAwait(false);
            }
            finally { _network.Release(); }
            Ui(() =>
            {
                if (epoch != Interlocked.Read(ref _epoch)) { Print("Decision stored, but drawing changed and this proposal is now stale."); return; }
                var status = decision == "approve" ? "approved" : "rejected";
                proposal["status"] = status;
                if (_proposal?["id"]?.GetValue<string>() == id) _proposal["status"] = status;
                foreach (JsonObject item in _lastResult?["proposals"] as JsonArray ?? new JsonArray())
                    if (item["id"]?.GetValue<string>() == id) item["status"] = status;
                Print($"Engineer decision recorded: {decision}. Source drawing is unchanged. Approval enables only export for the current revision; it is not certification.");
            });
        }, synchronization: false);
    }

    public void Export(string format, string path)
    {
        var proposal = CurrentProposal();
        RequireCompleteCoverage();
        if (proposal["status"]?.GetValue<string>() != "approved") throw new InvalidOperationException("PFAPPROVE is required before export.");
        var id = proposal["id"]!.GetValue<string>();
        var epoch = Interlocked.Read(ref _epoch);
        Background(async () =>
        {
            var bytes = await _client.DownloadAsync(SessionPath + "/proposals/" + Segment(id) + "/export/" + Segment(format), _token).ConfigureAwait(false);
            var temporary = Path.Combine(Path.GetDirectoryName(Path.GetFullPath(path))!, ".pipeforge-" + Guid.NewGuid().ToString("N") + ".tmp");
            try
            {
                // Stage I/O off the UI thread. Publish the file only after an atomic UI-thread
                // epoch check, avoiding a drawing edit between a background check and saving.
                await using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    await file.WriteAsync(bytes, _token).ConfigureAwait(false);
                var finished = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                using var registration = _token.Register(() => finished.TrySetCanceled(_token));
                PipeForgePlugin.Post(() =>
                {
                    try
                    {
                        if (_disposed) throw new OperationCanceledException("Connection was closed.");
                        EnsureSynchronized();
                        RequireCompleteCoverage();
                        if (epoch != Interlocked.Read(ref _epoch) || !_dirty.IsEmpty || _coverageDirty || Volatile.Read(ref _synchronizationsInFlight) != 0 ||
                            proposal["source_revision"]!.GetValue<int>() != Volatile.Read(ref _revision) ||
                            proposal["status"]?.GetValue<string>() != "approved")
                            throw new InvalidOperationException("Drawing changed during download. Export discarded; re-analyze.");
                        File.Move(temporary, path, overwrite: false);
                        Print("Approved exchange artifact saved: " + path + ". Review/import it separately; no source geometry was replaced.");
                        finished.TrySetResult(true);
                    }
                    catch (System.Exception error) { finished.TrySetException(error); }
                });
                await finished.Task.ConfigureAwait(false);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }, synchronization: false);
    }

    private void Background(Func<Task> action, bool synchronization)
    {
        // Start asynchronous operations in UI submission order so SemaphoreSlim waiters
        // preserve delta ordering. Every I/O await avoids the AutoCAD context; no .Wait/.Result.
        _ = Execute();
        async Task Execute()
        {
            try { await action().ConfigureAwait(false); }
            catch (OperationCanceledException) when (_token.IsCancellationRequested) { }
            catch (System.Exception error)
            {
                if (synchronization) _syncFaulted = true;
                Ui(() => Print(error.Message + (synchronization ? " Synchronization paused; reconnect using PFCONNECT." : "")));
            }
        }
    }

    public void ClearVisuals()
    {
        foreach (var entity in _preview)
        {
            try { TransientManager.CurrentTransientManager.EraseTransient(entity, new IntegerCollection()); }
            finally { entity.Dispose(); }
        }
        _preview.Clear();
        using (_document.LockDocument())
        using (var transaction = _document.Database.TransactionManager.StartTransaction())
        {
            foreach (var id in _highlighted)
                if (!id.IsErased && transaction.GetObject(id, OpenMode.ForRead) is Entity entity) entity.Unhighlight();
        }
        _highlighted.Clear();
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        _document.Database.ObjectModified -= Modified;
        _document.Database.ObjectAppended -= Appended;
        _document.Database.ObjectErased -= Erased;
        _cancellation.Cancel();
        try { ClearVisuals(); } catch (Autodesk.AutoCAD.Runtime.Exception) { /* Document may already be closing. */ }
        _client.Dispose();
        _cancellation.Dispose();
    }
}
