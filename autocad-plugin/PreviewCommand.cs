using Autodesk.AutoCAD.Colors;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.GraphicsInterface;
using Autodesk.AutoCAD.Runtime;
using AcDbPolyline = Autodesk.AutoCAD.DatabaseServices.Polyline;

namespace Maritime.AutoCAD;

public sealed class PreviewCommand
{
    private const double BulgeTolerance = 1e-9;

    [CommandMethod("MARITIMEPREVIEW", CommandFlags.Modal)]
    public static void PreviewSelectedRoute()
    {
        var document = Application.DocumentManager.MdiActiveDocument;
        if (document is null)
        {
            return;
        }

        var editor = document.Editor;
        var selectionOptions = new PromptSelectionOptions
        {
            MessageForAdding = "\nSelect route centerlines (LINE or polyline): "
        };
        var selectionFilter = new SelectionFilter(
        [
            new TypedValue((int)DxfCode.Start, "LINE,LWPOLYLINE,POLYLINE")
        ]);
        var selectionResult = editor.GetSelection(selectionOptions, selectionFilter);

        if (selectionResult.Status != PromptStatus.OK)
        {
            editor.WriteMessage("\nNo route geometry selected.");
            return;
        }

        var previews = new List<Line>();
        var manager = TransientManager.CurrentTransientManager;
        var viewports = new IntegerCollection();
        var addedTransients = new List<Line>();
        var unsupportedCurves = 0;
        var routeLength = 0.0;

        try
        {
            using (var transaction = document.Database.TransactionManager.StartTransaction())
            {
                foreach (SelectedObject? selected in selectionResult.Value)
                {
                    if (selected?.ObjectId is not ObjectId objectId || objectId.IsNull)
                    {
                        continue;
                    }

                    if (transaction.GetObject(objectId, OpenMode.ForRead) is not Entity entity)
                    {
                        continue;
                    }

                    switch (entity)
                    {
                        case Line line:
                            AddSegment(line.StartPoint, line.EndPoint, previews, ref routeLength);
                            break;
                        case AcDbPolyline polyline:
                            ReadPolyline(polyline, previews, ref routeLength, ref unsupportedCurves);
                            break;
                        case Polyline3d polyline3d:
                            ReadPolyline3d(polyline3d, transaction, previews, ref routeLength);
                            break;
                    }
                }

                transaction.Commit();
            }

            foreach (var preview in previews)
            {
                preview.Color = Color.FromColorIndex(ColorMethod.ByAci, 4);
                if (!manager.AddTransient(preview, TransientDrawingMode.Main, 128, viewports))
                {
                    throw new InvalidOperationException("AutoCAD could not display the temporary route preview.");
                }

                addedTransients.Add(preview);
            }

            editor.Regen();
            editor.WriteMessage(
                $"\nPreviewing {previews.Count} straight segments ({routeLength:0.###} drawing units) in cyan. " +
                "This is the selected baseline, not an optimized route; the drawing has not been changed.");

            if (unsupportedCurves > 0)
            {
                editor.WriteMessage($"\nSkipped {unsupportedCurves} curved polyline segment(s) in this first preview.");
            }

            editor.GetString(new PromptStringOptions("\nPress Enter to clear the temporary preview: "));
        }
        catch (System.Exception exception)
        {
            editor.WriteMessage($"\nMaritime preview failed: {exception.Message}");
        }
        finally
        {
            foreach (var preview in addedTransients)
            {
                manager.EraseTransient(preview, viewports);
            }

            foreach (var preview in previews)
            {
                preview.Dispose();
            }

            editor.Regen();
        }
    }

    private static void ReadPolyline(
        AcDbPolyline polyline,
        ICollection<Line> previews,
        ref double routeLength,
        ref int unsupportedCurves)
    {
        var count = polyline.NumberOfVertices;
        var segmentCount = polyline.Closed ? count : count - 1;

        for (var index = 0; index < segmentCount; index++)
        {
            if (Math.Abs(polyline.GetBulgeAt(index)) > BulgeTolerance)
            {
                unsupportedCurves++;
                continue;
            }

            var next = (index + 1) % count;
            AddSegment(polyline.GetPoint3dAt(index), polyline.GetPoint3dAt(next), previews, ref routeLength);
        }
    }

    private static void ReadPolyline3d(
        Polyline3d polyline,
        Transaction transaction,
        ICollection<Line> previews,
        ref double routeLength)
    {
        var points = new List<Autodesk.AutoCAD.Geometry.Point3d>();
        foreach (ObjectId vertexId in polyline)
        {
            if (transaction.GetObject(vertexId, OpenMode.ForRead) is PolylineVertex3d vertex)
            {
                points.Add(vertex.Position);
            }
        }

        for (var index = 0; index + 1 < points.Count; index++)
        {
            AddSegment(points[index], points[index + 1], previews, ref routeLength);
        }

        if (polyline.Closed && points.Count > 2)
        {
            AddSegment(points[^1], points[0], previews, ref routeLength);
        }
    }

    private static void AddSegment(
        Autodesk.AutoCAD.Geometry.Point3d start,
        Autodesk.AutoCAD.Geometry.Point3d end,
        ICollection<Line> previews,
        ref double routeLength)
    {
        if (start.DistanceTo(end) <= 1e-9)
        {
            return;
        }

        previews.Add(new Line(start, end));
        routeLength += start.DistanceTo(end);
    }
}
