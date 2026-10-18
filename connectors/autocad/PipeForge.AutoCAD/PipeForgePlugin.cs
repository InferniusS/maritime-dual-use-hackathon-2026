using System.Collections.Concurrent;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Runtime;
using CadApplication = Autodesk.AutoCAD.ApplicationServices.Core.Application;

[assembly: ExtensionApplication(typeof(PipeForge.AutoCAD.PipeForgePlugin))]
[assembly: CommandClass(typeof(PipeForge.AutoCAD.PipeForgePlugin))]

namespace PipeForge.AutoCAD;

/// <summary>Native commands and the only UI dispatcher. All document work stays on AutoCAD's thread.</summary>
public sealed class PipeForgePlugin : IExtensionApplication
{
    private static readonly ConcurrentQueue<Action> UiQueue = new();
    private static readonly Dictionary<Document, CadSession> Sessions = [];

    internal static void Post(Action action) => UiQueue.Enqueue(action);

    public void Initialize()
    {
        CadApplication.Idle += Idle;
        CadApplication.DocumentManager.DocumentToBeDestroyed += Closing;
        CadApplication.DocumentManager.MdiActiveDocument?.Editor.WriteMessage(
            "\nPIPEFORGE EDGE loaded. PFCONNECT reads explicit metadata and mapped source geometry. PFHELP lists commands.");
    }

    public void Terminate()
    {
        CadApplication.Idle -= Idle;
        CadApplication.DocumentManager.DocumentToBeDestroyed -= Closing;
        foreach (var session in Sessions.Values) session.Dispose();
        Sessions.Clear();
        while (UiQueue.TryDequeue(out _)) { }
    }

    private static void Closing(object? sender, DocumentCollectionEventArgs args)
    {
        if (Sessions.Remove(args.Document, out var session)) session.Dispose();
    }

    private static void Idle(object? sender, EventArgs args)
    {
        while (UiQueue.TryDequeue(out var action))
        {
            try { action(); }
            catch (System.Exception error) { CadApplication.DocumentManager.MdiActiveDocument?.Editor.WriteMessage("\nPIPEFORGE: " + error.Message); }
        }
        var active = CadApplication.DocumentManager.MdiActiveDocument;
        if (active != null && active.Editor.IsQuiescent && Sessions.TryGetValue(active, out var session))
        {
            try { session.OnIdle(); }
            catch (System.Exception error) { active.Editor.WriteMessage("\nPIPEFORGE: " + error.Message); }
        }
    }

    private static void WithDocument(Action<Document> action)
    {
        var document = CadApplication.DocumentManager.MdiActiveDocument;
        if (document == null) return;
        try { action(document); }
        catch (System.Exception error) { document.Editor.WriteMessage("\nPIPEFORGE: " + error.Message); }
    }

    private static CadSession Session(Document document) => Sessions.TryGetValue(document, out var session)
        ? session : throw new InvalidOperationException("Run PFCONNECT first.");

    [CommandMethod("PFCONNECT", CommandFlags.Modal)]
    public static void Connect() => WithDocument(document =>
    {
        var file = document.Editor.GetFileNameForOpen(new PromptOpenFileOptions("\nPipeForge metadata envelope JSON") { Filter = "JSON (*.json)|*.json" });
        if (file.Status != PromptStatus.OK) return;
        var endpoint = document.Editor.GetString(new PromptStringOptions("\nEdge server URL <http://127.0.0.1:8000>: ") { AllowSpaces = false });
        if (endpoint.Status != PromptStatus.OK && endpoint.Status != PromptStatus.None) return;
        var url = string.IsNullOrWhiteSpace(endpoint.StringResult) ? "http://127.0.0.1:8000" : endpoint.StringResult;
        var metadata = Metadata.Load(file.StringResult);
        var next = new CadSession(document, metadata, new Uri(url));
        try
        {
            if (Sessions.Remove(document, out var previous)) previous.Dispose();
            Sessions.Add(document, next);
            next.Connect();
        }
        catch { Sessions.Remove(document); next.Dispose(); throw; }
    });

    [CommandMethod("PFMAP", CommandFlags.Modal)]
    public static void Map() => WithDocument(document =>
    {
        var session = Session(document);
        var id = document.Editor.GetString(new PromptStringOptions("\nEngineering object ID from supplied metadata: ") { AllowSpaces = true });
        if (id.Status != PromptStatus.OK || string.IsNullOrWhiteSpace(id.StringResult)) return;
        var entity = document.Editor.GetEntity(new PromptEntityOptions("\nSelect source 3D pipe centerline or obstacle entity: "));
        if (entity.Status == PromptStatus.OK) session.Map(id.StringResult.Trim(), entity.ObjectId);
    });

    [CommandMethod("PFANALYZE", CommandFlags.Modal)]
    public static void Analyze() => WithDocument(document => Session(document).RunJob("analyze"));

    [CommandMethod("PFOPTIMIZE", CommandFlags.Modal)]
    public static void Optimize() => WithDocument(document => Session(document).RunJob("optimize"));

    [CommandMethod("PFCOMPANION", CommandFlags.Modal)]
    public static void Companion() => WithDocument(document => Session(document).OpenCompanion());

    [CommandMethod("PFREFRESH", CommandFlags.Modal)]
    public static void Refresh() => WithDocument(document => Session(document).RefreshCompanion());

    [CommandMethod("PFLIVEON", CommandFlags.Modal)]
    public static void LiveOn() => WithDocument(document => Session(document).SetLive(true));

    [CommandMethod("PFLIVEOFF", CommandFlags.Modal)]
    public static void LiveOff() => WithDocument(document => Session(document).SetLive(false));

    [CommandMethod("PFPREVIEW", CommandFlags.Modal)]
    public static void Preview() => WithDocument(document =>
    {
        var number = document.Editor.GetInteger(new PromptIntegerOptions("\nProposal number <1>: ")
            { DefaultValue = 1, UseDefaultValue = true, LowerLimit = 1 });
        if (number.Status == PromptStatus.OK) Session(document).Preview(number.Value);
    });

    [CommandMethod("PFHIGHLIGHT", CommandFlags.Modal)]
    public static void Highlight() => WithDocument(document =>
    {
        var id = document.Editor.GetString(new PromptStringOptions("\nEngineering ID <all recommended objects>: ") { AllowSpaces = true });
        if (id.Status == PromptStatus.OK || id.Status == PromptStatus.None)
            Session(document).Highlight(id.StringResult?.Trim());
    });

    [CommandMethod("PFAPPROVE", CommandFlags.Modal)]
    public static void Approve() => Decision("approve");

    [CommandMethod("PFREJECT", CommandFlags.Modal)]
    public static void Reject() => Decision("reject");

    private static void Decision(string decision) => WithDocument(document =>
    {
        var engineer = document.Editor.GetString(new PromptStringOptions("\nEngineer name / identifier: ") { AllowSpaces = true });
        if (engineer.Status != PromptStatus.OK || string.IsNullOrWhiteSpace(engineer.StringResult)) return;
        var comment = document.Editor.GetString(new PromptStringOptions("\nDecision comment (optional): ") { AllowSpaces = true });
        if (comment.Status != PromptStatus.OK && comment.Status != PromptStatus.None) return;
        var confirm = new PromptKeywordOptions($"\nRecord '{decision}' for the selected proposal? This does not modify source geometry [Yes/No] <No>: ", "Yes No")
            { AllowNone = true };
        var answer = document.Editor.GetKeywords(confirm);
        if (answer.Status == PromptStatus.OK && answer.StringResult == "Yes")
            Session(document).Decide(decision, engineer.StringResult.Trim(), comment.StringResult ?? "");
    });

    [CommandMethod("PFEXPORT", CommandFlags.Modal)]
    public static void Export() => WithDocument(document =>
    {
        var format = document.Editor.GetKeywords(new PromptKeywordOptions("\nExchange format [Json/Dxf/Csv/Report] <Json>: ", "Json Dxf Csv Report") { AllowNone = true });
        if (format.Status != PromptStatus.OK && format.Status != PromptStatus.None) return;
        var value = string.IsNullOrWhiteSpace(format.StringResult) ? "json" : format.StringResult.ToLowerInvariant();
        var extension = value == "report" ? "txt" : value;
        var path = document.Editor.GetFileNameForSave(new PromptSaveFileOptions("\nSave new approved exchange artifact")
            { Filter = $"{extension.ToUpperInvariant()} (*.{extension})|*.{extension}" });
        if (path.Status != PromptStatus.OK) return;
        if (!string.Equals(Path.GetExtension(path.StringResult), "." + extension, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Use the selected format's file extension; source DWG export is unsupported.");
        if (File.Exists(path.StringResult)) throw new IOException("Choose a new file path. PIPEFORGE never overwrites existing files.");
        Session(document).Export(value, path.StringResult);
    });

    [CommandMethod("PFCLEAR", CommandFlags.Modal)]
    public static void Clear() => WithDocument(document => Session(document).ClearVisuals());

    [CommandMethod("PFDISCONNECT", CommandFlags.Modal)]
    public static void Disconnect() => WithDocument(document =>
    {
        if (Sessions.Remove(document, out var session)) session.Dispose();
        document.Editor.WriteMessage("\nPIPEFORGE: Disconnected; event handlers and visual overlays removed.");
    });

    [CommandMethod("PFHELP", CommandFlags.Modal)]
    public static void Help() => WithDocument(document => document.Editor.WriteMessage(
        "\nPIPEFORGE EDGE: PFCONNECT, PFMAP, PFCOMPANION, PFREFRESH, PFANALYZE, PFOPTIMIZE, PFLIVEON, PFLIVEOFF, PFHIGHLIGHT, PFPREVIEW, PFAPPROVE, PFREJECT, PFEXPORT, PFCLEAR, PFDISCONNECT.\nMetadata semantics are explicit; native geometry is read only. Live deltas debounce 400 ms while AutoCAD is idle.\nLive analysis defaults OFF. Source drawing edits invalidate results. Preview uses cyan transient lines, never database construction geometry."));
}
