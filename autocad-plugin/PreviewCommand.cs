using Autodesk.AutoCAD.Colors;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.GraphicsInterface;
using Autodesk.AutoCAD.Runtime;
using AcDbPolyline = Autodesk.AutoCAD.DatabaseServices.Polyline;
using System.Net.Http;
using System.Text.Json;

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

    [CommandMethod("MARITIMEOPTIMIZE", CommandFlags.Modal)]
    public static void OptimizeSelectedRoute()
    {
        var document = Application.DocumentManager.MdiActiveDocument;
        if (document is null)
        {
            return;
        }

        var editor = document.Editor;
        var routeSelection = editor.GetSelection(new PromptSelectionOptions
        {
            MessageForAdding = "\nSelect the connected route entities to optimize (LINE or straight polyline): "
        }, new SelectionFilter(
        [new TypedValue((int)DxfCode.Start, "LINE,LWPOLYLINE,POLYLINE") ]));

        if (routeSelection.Status != PromptStatus.OK)
        {
            editor.WriteMessage("\nNo route selected.");
            return;
        }

        var obstacleSelection = editor.GetSelection(new PromptSelectionOptions
        {
            MessageForAdding = "\nSelect obstacle objects to avoid, or press Enter for none: ",
            AllowDuplicates = false
        });
        if (obstacleSelection.Status is not (PromptStatus.OK or PromptStatus.None))
        {
            editor.WriteMessage("\nObstacle selection cancelled.");
            return;
        }

        if (!TryGetPositiveDouble(editor, "Pipe outside diameter in drawing units", out var diameter) ||
            !TryGetNonNegativeDouble(editor, "Required clearance in drawing units", out var clearance) ||
            !TryGetPositiveDouble(editor, "Search grid step in drawing units", out var gridStep) ||
            !TryGetPositiveDouble(editor, "Search-bound padding in drawing units", out var padding))
        {
            return;
        }

        var unitsResult = editor.GetString(new PromptStringOptions("\nDrawing units (mm, m, or in): ")
        {
            AllowSpaces = false
        });
        if (unitsResult.Status != PromptStatus.OK ||
            unitsResult.StringResult is not ("mm" or "m" or "in"))
        {
            editor.WriteMessage("\nEnter one of: mm, m, in.");
            return;
        }

        var units = unitsResult.StringResult;
        var scaleToMillimeters = units switch { "m" => 1000.0, "in" => 25.4, _ => 1.0 };
        List<Point3d> route;
        List<Extents3d> obstacles;
        try
        {
            using var transaction = document.Database.TransactionManager.StartTransaction();
            route = ReadAndOrderRoute(routeSelection.Value, transaction);
            obstacles = ReadObstacleExtents(obstacleSelection, transaction);
            transaction.Commit();
        }
        catch (System.Exception exception)
        {
            editor.WriteMessage($"\nCould not read selected CAD geometry: {exception.Message}");
            return;
        }

        if (route.Count < 2)
        {
            editor.WriteMessage("\nThe route needs at least two connected points.");
            return;
        }

        var min = new[] { route.Min(point => point.X), route.Min(point => point.Y), route.Min(point => point.Z) };
        var max = new[] { route.Max(point => point.X), route.Max(point => point.Y), route.Max(point => point.Z) };
        foreach (var obstacle in obstacles)
        {
            min[0] = Math.Min(min[0], obstacle.MinPoint.X);
            min[1] = Math.Min(min[1], obstacle.MinPoint.Y);
            min[2] = Math.Min(min[2], obstacle.MinPoint.Z);
            max[0] = Math.Max(max[0], obstacle.MaxPoint.X);
            max[1] = Math.Max(max[1], obstacle.MaxPoint.Y);
            max[2] = Math.Max(max[2], obstacle.MaxPoint.Z);
        }
        for (var axis = 0; axis < 3; axis++)
        {
            min[axis] -= padding;
            max[axis] += padding;
        }

        var layout = new
        {
            name = "AutoCAD selected route",
            provenance = "cad-derived",
            units,
            system_type = "pressurized",
            bounds = new { min, max },
            obstacles = obstacles.Select((box, index) => new
            {
                id = $"autocad-obstacle-{index + 1}",
                min = new[] { box.MinPoint.X, box.MinPoint.Y, box.MinPoint.Z },
                max = new[] { box.MaxPoint.X, box.MaxPoint.Y, box.MaxPoint.Z }
            }),
            pipe = new
            {
                start = ToArray(route[0]),
                end = ToArray(route[^1]),
                diameter,
                original_route = route.Select(ToArray)
            },
            rules = new { clearance, grid_step = gridStep, bend_penalty = 0.0 },
            assumptions = new[]
            {
                "Obstacle entities are represented by axis-aligned CAD extents selected by the user.",
                "The search bounds are derived from selected geometry and expanded by the entered padding.",
                "This is a prototype route suggestion. A designer must verify the result and its engineering constraints."
            }
        };

        try
        {
            editor.WriteMessage("\nSending route to the local optimizer at http://127.0.0.1:8767 …");
            using var client = new HttpClient { Timeout = TimeSpan.FromMinutes(5) };
            using var content = new StringContent(JsonSerializer.Serialize(new { layout }), System.Text.Encoding.UTF8, "application/json");
            using var response = client.PostAsync("http://127.0.0.1:8767/api/run", content).GetAwaiter().GetResult();
            var responseText = response.Content.ReadAsStringAsync().GetAwaiter().GetResult();
            if (!response.IsSuccessStatusCode)
            {
                using var errorDocument = JsonDocument.Parse(responseText);
                var message = errorDocument.RootElement.TryGetProperty("error", out var error) ? error.GetString() : response.ReasonPhrase;
                editor.WriteMessage($"\nOptimizer returned HTTP {(int)response.StatusCode}: {message}");
                return;
            }

            using var resultDocument = JsonDocument.Parse(responseText);
            var result = resultDocument.RootElement.GetProperty("result");
            var status = result.GetProperty("status").GetString();
            if (!result.TryGetProperty("candidate", out var candidate) || candidate.ValueKind != JsonValueKind.Object ||
                !candidate.TryGetProperty("points", out var pointsElement))
            {
                editor.WriteMessage($"\nNo optimized route was found. Status: {status}.");
                return;
            }

            var optimized = pointsElement.EnumerateArray()
                .Select(point => new Point3d(
                    point[0].GetDouble() / scaleToMillimeters,
                    point[1].GetDouble() / scaleToMillimeters,
                    point[2].GetDouble() / scaleToMillimeters))
                .ToList();
            ShowOptimizedPreview(editor, optimized, status ?? "unknown");
        }
        catch (HttpRequestException exception)
        {
            editor.WriteMessage("\nCannot reach the local Python optimizer. Start it in another PowerShell window with: " +
                ".\\.venv\\Scripts\\python.exe scripts\\run_web.py");
            editor.WriteMessage($"\nDetails: {exception.Message}");
        }
        catch (System.Exception exception)
        {
            editor.WriteMessage($"\nOptimization request failed: {exception.Message}");
        }
    }

    private static bool TryGetPositiveDouble(Editor editor, string prompt, out double value) =>
        TryGetDouble(editor, prompt, allowZero: false, out value);

    private static bool TryGetNonNegativeDouble(Editor editor, string prompt, out double value) =>
        TryGetDouble(editor, prompt, allowZero: true, out value);

    private static bool TryGetDouble(Editor editor, string prompt, bool allowZero, out double value)
    {
        var result = editor.GetDouble(new PromptDoubleOptions($"\n{prompt}: ")
        {
            AllowNegative = false,
            AllowZero = allowZero
        });
        value = result.Value;
        return result.Status == PromptStatus.OK;
    }

    private static List<Point3d> ReadAndOrderRoute(SelectionSet selection, Transaction transaction)
    {
        const double joinTolerance = 1e-6;
        var segments = new List<List<Point3d>>();
        foreach (SelectedObject? selected in selection)
        {
            if (selected?.ObjectId is not ObjectId id || id.IsNull ||
                transaction.GetObject(id, OpenMode.ForRead) is not Entity entity)
            {
                continue;
            }

            switch (entity)
            {
                case Line line:
                    segments.Add([line.StartPoint, line.EndPoint]);
                    break;
                case AcDbPolyline polyline:
                    if (polyline.Closed)
                    {
                        throw new InvalidOperationException("Closed polylines are not valid single-pipe routes for this prototype.");
                    }
                    for (var i = 0; i < polyline.NumberOfVertices; i++)
                    {
                        if (i < polyline.NumberOfVertices - 1 && Math.Abs(polyline.GetBulgeAt(i)) > BulgeTolerance)
                        {
                            throw new InvalidOperationException("Curved polyline segments are not supported yet.");
                        }
                    }
                    segments.Add(Enumerable.Range(0, polyline.NumberOfVertices).Select(polyline.GetPoint3dAt).ToList());
                    break;
                case Polyline3d polyline3d:
                    if (polyline3d.Closed)
                    {
                        throw new InvalidOperationException("Closed polylines are not valid single-pipe routes for this prototype.");
                    }
                    var vertices = new List<Point3d>();
                    foreach (ObjectId vertexId in polyline3d)
                    {
                        if (transaction.GetObject(vertexId, OpenMode.ForRead) is PolylineVertex3d vertex)
                        {
                            vertices.Add(vertex.Position);
                        }
                    }
                    segments.Add(vertices);
                    break;
            }
        }

        if (segments.Count == 0)
        {
            throw new InvalidOperationException("Select at least one LINE or polyline.");
        }
        if (segments.Any(path => path.Count < 2))
        {
            throw new InvalidOperationException("Each selected route entity must contain at least two vertices.");
        }

        // Start at a free endpoint so selection order does not have to match route order.
        var startSegmentIndex = -1;
        var startAtBeginning = true;
        for (var segmentIndex = 0; segmentIndex < segments.Count && startSegmentIndex < 0; segmentIndex++)
        {
            for (var endpointIndex = 0; endpointIndex < 2; endpointIndex++)
            {
                var endpoint = endpointIndex == 0 ? segments[segmentIndex][0] : segments[segmentIndex][^1];
                var connectedElsewhere = segments.Where((_, otherIndex) => otherIndex != segmentIndex)
                    .Any(path => path[0].DistanceTo(endpoint) <= joinTolerance || path[^1].DistanceTo(endpoint) <= joinTolerance);
                if (!connectedElsewhere)
                {
                    startSegmentIndex = segmentIndex;
                    startAtBeginning = endpointIndex == 0;
                    break;
                }
            }
        }
        if (startSegmentIndex < 0)
        {
            throw new InvalidOperationException("Selected route pieces must form one open, non-branching path.");
        }

        var ordered = segments[startSegmentIndex];
        if (!startAtBeginning)
        {
            ordered.Reverse();
        }
        segments.RemoveAt(startSegmentIndex);
        while (segments.Count > 0)
        {
            var last = ordered[^1];
            var matches = segments.Select((path, index) => new { path, index })
                .Where(item => item.path[0].DistanceTo(last) <= joinTolerance || item.path[^1].DistanceTo(last) <= joinTolerance)
                .ToList();
            if (matches.Count != 1)
            {
                throw new InvalidOperationException("Selected route pieces must form one connected, non-branching path.");
            }
            var match = matches[0];
            var next = match.path;
            segments.RemoveAt(match.index);
            if (next[^1].DistanceTo(last) <= joinTolerance)
            {
                next.Reverse();
            }
            ordered.AddRange(next.Skip(1));
        }
        return ordered;
    }

    private static List<Extents3d> ReadObstacleExtents(PromptSelectionResult selection, Transaction transaction)
    {
        var extents = new List<Extents3d>();
        if (selection.Status != PromptStatus.OK)
        {
            return extents;
        }
        foreach (SelectedObject? selected in selection.Value)
        {
            if (selected?.ObjectId is ObjectId id && !id.IsNull &&
                transaction.GetObject(id, OpenMode.ForRead) is Entity entity)
            {
                extents.Add(entity.GeometricExtents);
            }
        }
        return extents;
    }

    private static double[] ToArray(Point3d point) => [point.X, point.Y, point.Z];

    private static void ShowOptimizedPreview(Editor editor, IReadOnlyList<Point3d> points, string status)
    {
        if (points.Count < 2)
        {
            editor.WriteMessage("\nOptimizer returned fewer than two route points.");
            return;
        }
        var manager = TransientManager.CurrentTransientManager;
        var viewports = new IntegerCollection();
        var previews = new List<Line>();
        try
        {
            for (var index = 0; index + 1 < points.Count; index++)
            {
                var line = new Line(points[index], points[index + 1]);
                line.Color = Color.FromColorIndex(ColorMethod.ByAci, 3);
                if (!manager.AddTransient(line, TransientDrawingMode.Main, 128, viewports))
                {
                    line.Dispose();
                    throw new InvalidOperationException("AutoCAD could not display the optimized route preview.");
                }
                previews.Add(line);
            }
            editor.Regen();
            editor.WriteMessage($"\nOptimizer status: {status}. Green is the suggested centerline; the DWG has not been changed.");
            editor.GetString(new PromptStringOptions("\nPress Enter to clear the optimization preview: "));
        }
        finally
        {
            foreach (var line in previews)
            {
                manager.EraseTransient(line, viewports);
                line.Dispose();
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
