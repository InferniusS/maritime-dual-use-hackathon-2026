using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.GraphicsInterface;
using System.Globalization;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace Maritime.AutoCAD;

public sealed partial class PreviewCommand
{
    // Inspection is entirely transient: it must not mark the scan snapshot stale,
    // change source layers, or leave a highlight or camera change on cancellation.
    private static bool ShowSelectedSectionPreview(Document document, JsonObject layout, JsonObject candidate,
        double drawingScale, int number)
    {
        var editor = document.Editor;
        var points = ReadPoints(layout["pipe"]?["original_route"], "selected section")
            .Select(point => Scale(point, drawingScale)).ToList();
        if (points.Count < 2) throw new InvalidOperationException("The selected section has no inspectable route.");
        var diameter = Number(layout["pipe"]!, "diameter") / drawingScale;
        var markerSize = Math.Max(diameter, 25 / drawingScale);
        var reach = Math.Max(diameter * 8, 500 / drawingScale);
        var manager = TransientManager.CurrentTransientManager;
        var viewports = new IntegerCollection();
        var markers = new List<Line>();
        var componentMarkers = new List<Line>();
        var highlighted = new HashSet<ObjectId>();
        var cleanupFailed = false;
        var sources = new List<(string Handle, bool Nested, ObjectId Id, Extents3d? Bounds)>();
        using var previousView = editor.GetCurrentView();
        try
        {
            var instances = layout["cad_context"]?["source_instances"] as JsonArray;
            if (instances is not null)
            {
                using var transaction = document.Database.TransactionManager.StartOpenCloseTransaction();
                var blocks = (BlockTable)transaction.GetObject(document.Database.BlockTableId, OpenMode.ForRead);
                foreach (var instance in instances)
                {
                    if (instance is null) continue;
                    var handle = instance["source_handle"]?.GetValue<string>() ?? "?";
                    var path = instance["instance_path"] as JsonArray;
                    var nested = path is { Count: > 0 };
                    var ownerHandle = nested ? Regex.Match(path![0]?.GetValue<string>() ?? "", "^[a-fA-F0-9]+").Value : handle;
                    ObjectId id = ObjectId.Null;
                    Extents3d? bounds = null;
                    if (long.TryParse(ownerHandle, NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out var numeric))
                    {
                        try
                        {
                            var found = document.Database.GetObjectId(false, new Handle(numeric), 0);
                            if (!found.IsNull && !found.IsErased && transaction.GetObject(found, OpenMode.ForRead) is Entity entity && entity.OwnerId == blocks[BlockTableRecord.ModelSpace])
                            {
                                id = found;
                                if (highlighted.Add(id)) entity.Highlight();
                                // A nested definition's bounds are not its placed world bounds.
                                // Do not substitute the owning block's potentially huge extents.
                                if (!nested)
                                    try { bounds = entity.GeometricExtents; }
                                    catch (Autodesk.AutoCAD.Runtime.Exception) { }
                            }
                        }
                        catch (Autodesk.AutoCAD.Runtime.Exception) { }
                    }
                    sources.Add((handle, nested, id, bounds));
                }
            }
            for (var index = 1; index < points.Count; index++) AddLine(points[index - 1], points[index], 6, markers);
            if (layout["protected_endpoints"] is JsonArray stubs)
                foreach (var stub in stubs)
                {
                    var fixedPoints = ReadPoints(stub?["points"], "prepared fixed connection")
                        .Select(point => Scale(point, drawingScale)).ToList();
                    for (var index = 1; index < fixedPoints.Count; index++) AddLine(fixedPoints[index - 1], fixedPoints[index], 4, markers);
                }
            AddCross(points[0], 1); AddCross(points[^1], 5);
            FrameRoute();
            editor.WriteMessage($"\nSECTION {number}: magenta is the selected inferred pipe path; RED cross = START; BLUE cross = END. Source handles: {SourceHandleText(candidate)}.");
            editor.WriteMessage("\nThis is the original route, not a proposal. Use Start/End to inspect a connection, Component to locate a source object, Region to see the search box, or Enter to continue. Continuing does not approve endpoint contacts.");
            if (layout["protected_endpoints"] is JsonArray { Count: > 0 }) editor.WriteMessage("\nCYAN segments = unchanged fixed connection portions prepared from your explicit contact choices. They are not new pipes. Check their extent before continuing; this preview does not resolve any reported connection conflict.");
            editor.WriteMessage("\nInspection markers draw over source geometry to remain visible. Marker visibility does not prove physical clearance.");
            if (sources.Any(source => source.Nested)) editor.WriteMessage("\nNested source objects highlight their owning block; the magenta path and endpoint markers locate this particular section.");
            if (highlighted.Count == 0) editor.WriteMessage("\nSource highlighting is unavailable. Verify the recorded path against the actual CAD before continuing.");
            while (true)
            {
                editor.Regen();
                var choice = AskKeyword(editor, $"Inspect section {number}", ["Continue", "Start", "End", "Route", "Component", "Region", "Cancel"], "Continue");
                if (choice is null or "Cancel") return false;
                if (choice == "Continue") return true;
                Clear(componentMarkers);
                if (choice == "Route") FrameRoute();
                else if (choice is "Start" or "End") FrameEndpoint(choice == "Start");
                else if (choice == "Region")
                {
                    var minimum = ReadScanPoint(layout["bounds"]?["min"], drawingScale);
                    var maximum = ReadScanPoint(layout["bounds"]?["max"], drawingScale);
                    if (minimum is null || maximum is null) editor.WriteMessage("\nThe prepared layout did not provide a displayable search box.");
                    else
                    {
                        FrameMeshReview(document, AddBox(minimum.Value, maximum.Value, 7));
                        editor.WriteMessage($"\nWHITE BOX = search region for padding {layout["cad_context"]?["padding_mm"]} mm. Required clearance is {layout["rules"]?["clearance"]} mm. This box is not the ship boundary or an approved routing space. Route restores the close view.");
                    }
                }
                else if (choice == "Component")
                {
                    if (sources.Count == 0) { editor.WriteMessage("\nNo source-component identities were supplied for this section."); continue; }
                    for (var index = 0; index < sources.Count; index++) editor.WriteMessage($"\nComponent {index + 1}: source {sources[index].Handle}{(sources[index].Nested ? " (nested)" : "")}.");
                    var answer = editor.GetInteger(new Autodesk.AutoCAD.EditorInput.PromptIntegerOptions("\nSource component number")
                    { DefaultValue = 1, UseDefaultValue = true, LowerLimit = 1, UpperLimit = sources.Count });
                    if (answer.Status != Autodesk.AutoCAD.EditorInput.PromptStatus.OK) return false;
                    var source = sources[answer.Value - 1];
                    if (source.Bounds is Extents3d bounds)
                    {
                        FrameMeshReview(document, AddBox(bounds.MinPoint, bounds.MaxPoint, 2));
                        editor.WriteMessage($"\nYellow box = bounds of source component {answer.Value}, handle {source.Handle}. Magenta remains the selected route. Bounds do not establish a connection or pipe identity.");
                    }
                    else
                    {
                        FrameRoute();
                        editor.WriteMessage($"\nSource {source.Handle}: individual world-space bounds are unavailable{(source.Nested ? " for this nested instance" : "")}. The selected route remains framed; use endpoint contact INSPECT after Continue for its recorded neighboring-component bounds.");
                    }
                }
            }
        }
        finally
        {
            Clear(componentMarkers); Clear(markers);
            try
            {
                using var transaction = document.Database.TransactionManager.StartOpenCloseTransaction();
                foreach (var id in highlighted)
                    try { if (!id.IsErased && transaction.GetObject(id, OpenMode.ForRead) is Entity entity) entity.Unhighlight(); }
                    catch (System.Exception) { cleanupFailed = true; }
            }
            catch (System.Exception) { cleanupFailed = true; }
            try { editor.SetCurrentView(previousView); editor.Regen(); }
            catch (System.Exception) { cleanupFailed = true; }
            if (cleanupFailed)
                try { editor.WriteMessage("\nAutoCAD could not clear every inspection marker or restore the view. Run REGEN to refresh the display; source geometry was not edited."); }
                catch (System.Exception) { }
        }
        void AddLine(Point3d a, Point3d b, short color, List<Line> collection)
        {
            if (a.DistanceTo(b) <= 1e-12) return;
            var line = new Line(a, b) { ColorIndex = color, LineWeight = LineWeight.LineWeight070 };
            if (manager.AddTransient(line, TransientDrawingMode.DirectTopmost, 128, viewports)) collection.Add(line);
            else { line.Dispose(); throw new InvalidOperationException("AutoCAD could not display the section inspection markers."); }
        }
        void AddCross(Point3d center, short color)
        {
            foreach (var axis in new[] { Vector3d.XAxis, Vector3d.YAxis, Vector3d.ZAxis })
                AddLine(center - axis * markerSize, center + axis * markerSize, color, markers);
        }
        Point3d[] AddBox(Point3d minimum, Point3d maximum, short color)
        {
            var corners = Enumerable.Range(0, 8).Select(i => new Point3d(
                (i & 1) == 0 ? minimum.X : maximum.X,
                (i & 2) == 0 ? minimum.Y : maximum.Y,
                (i & 4) == 0 ? minimum.Z : maximum.Z)).ToArray();
            for (var i = 0; i < 8; i++) foreach (var bit in new[] { 1, 2, 4 })
                if ((i & bit) == 0) AddLine(corners[i], corners[i | bit], color, componentMarkers);
            return corners;
        }
        void FrameRoute()
        {
            var focus = points.ToList();
            foreach (var point in new[] { points[0], points[^1] })
                foreach (var axis in new[] { Vector3d.XAxis, Vector3d.YAxis, Vector3d.ZAxis })
                { focus.Add(point - axis * markerSize * 2); focus.Add(point + axis * markerSize * 2); }
            FrameMeshReview(document, focus);
        }
        void FrameEndpoint(bool start)
        {
            var ordered = start ? points : points.AsEnumerable().Reverse().ToList();
            var focus = new List<Point3d>();
            foreach (var axis in new[] { Vector3d.XAxis, Vector3d.YAxis, Vector3d.ZAxis })
            { focus.Add(ordered[0] - axis * reach * .5); focus.Add(ordered[0] + axis * reach * .5); }
            var remaining = reach;
            for (var index = 1; index < ordered.Count && remaining > 0; index++)
            {
                var distance = ordered[index - 1].DistanceTo(ordered[index]);
                if (distance <= 1e-12) continue;
                focus.Add(distance <= remaining ? ordered[index] : ordered[index - 1] + (ordered[index] - ordered[index - 1]) * (remaining / distance));
                remaining -= distance;
            }
            FrameMeshReview(document, focus);
            editor.WriteMessage(start ? "\nFocused START: red endpoint cross. The connection still needs review." : "\nFocused END: blue endpoint cross. The connection still needs review.");
        }
        void Clear(List<Line> collection)
        {
            foreach (var line in collection)
            {
                try { manager.EraseTransient(line, viewports); } catch (System.Exception) { cleanupFailed = true; }
                try { line.Dispose(); } catch (System.Exception) { cleanupFailed = true; }
            }
            collection.Clear();
        }
    }
}
