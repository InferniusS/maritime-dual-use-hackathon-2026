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

public sealed partial class PreviewCommand
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
                "This is the selected baseline. It has not been optimized. The drawing has not been changed.");

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

    [CommandMethod("MARITIMELINEOPTIMIZE", CommandFlags.Modal)]
    public static void OptimizeSelectedRoute()
    {
        var document = Application.DocumentManager.MdiActiveDocument;
        if (document is null)
        {
            return;
        }

        var editor = document.Editor;
        if (!document.Database.TileMode)
        {
            editor.WriteMessage("\nRun MARITIMELINEOPTIMIZE in the Model tab. Paper-space routes are not supported.");
            return;
        }
        editor.WriteMessage("\nPressure-pipe centerline workflow. Select all surrounding obstacles that this local check must avoid. Entered prototype rules do not establish V3 compliance.");
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
            route = ReadAndOrderRoute(routeSelection.Value, transaction, 0.001 / scaleToMillimeters);
            obstacles = ReadObstacleExtents(obstacleSelection, transaction, routeSelection.Value.GetObjectIds().ToHashSet());
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
            cad_context = new
            {
                document = new { name = System.IO.Path.GetFileName(document.Name), fingerprint = document.Database.FingerprintGuid.ToString() },
                source_handles = routeSelection.Value.GetObjectIds().Select(id => id.Handle.ToString()).ToArray(),
                obstacle_sources = obstacleSelection.Status == PromptStatus.OK
                    ? obstacleSelection.Value.GetObjectIds().Select((id, index) => new { id = $"autocad-obstacle-{index + 1}", source_handle = id.Handle.ToString() }).ToArray()
                    : [],
                obstacle_method = "selected_entity_geometric_extents",
                complete_ship_geometry = false,
                connectivity_verified = false
            },
            assumptions = new[]
            {
                "Obstacle entities are represented by axis-aligned CAD extents selected by the user.",
                "The search bounds are derived from selected geometry and expanded by the entered padding.",
                "This is a prototype route suggestion. A designer must verify the result and its engineering constraints."
            }
        };

        try
        {
            using var client = CreateClient();
            CheckService(client);
            var requestLayout = JsonSerializer.SerializeToNode(layout)!.AsObject();
            var rounded = AskKeyword(editor, "Create a rounded 3D pressure-pipe proposal", ["Yes", "No"], "Yes");
            if (rounded is null) return;
            if (rounded == "Yes")
            {
                if (!AskNumber(editor, "Proposed bend centerline radius in mm (review assumption)", Math.Max(304.8, 1.5 * diameter * scaleToMillimeters), false, out var radius)) return;
                requestLayout["proposal"] = new System.Text.Json.Nodes.JsonObject { ["mode"] = "rounded_pipe", ["bend_radius_mm"] = radius };
            }
            RunAndReview(document, client, requestLayout, scaleToMillimeters, document.Database.FingerprintGuid.ToString());
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

    private static List<Point3d> ReadAndOrderRoute(SelectionSet selection, Transaction transaction, double joinTolerance)
    {
        var paths = new List<List<Point3d>>();
        foreach (SelectedObject? selected in selection)
        {
            if (selected?.ObjectId is not ObjectId id || id.IsNull ||
                transaction.GetObject(id, OpenMode.ForRead) is not Entity entity)
                throw new InvalidOperationException("A selected route object could not be read.");
            switch (entity)
            {
                case Line line:
                    paths.Add([line.StartPoint, line.EndPoint]);
                    break;
                case AcDbPolyline polyline:
                    if (polyline.Closed)
                        throw new InvalidOperationException("Closed polylines are not valid single-pipe routes.");
                    for (var i = 0; i + 1 < polyline.NumberOfVertices; i++)
                        if (Math.Abs(polyline.GetBulgeAt(i)) > BulgeTolerance)
                            throw new InvalidOperationException("Curved polyline segments are not supported by MARITIMELINEOPTIMIZE. Use MARITIMECAD for supported solid pipe geometry.");
                    paths.Add(Enumerable.Range(0, polyline.NumberOfVertices).Select(polyline.GetPoint3dAt).ToList());
                    break;
                case Polyline3d polyline3d:
                    if (polyline3d.Closed || polyline3d.PolyType != Poly3dType.SimplePoly)
                        throw new InvalidOperationException("Select an open simple 3D polyline. Fitted or closed polylines are unsupported.");
                    var vertices = new List<Point3d>();
                    foreach (ObjectId vertexId in polyline3d)
                        if (transaction.GetObject(vertexId, OpenMode.ForRead) is PolylineVertex3d vertex)
                            vertices.Add(vertex.Position);
                    paths.Add(vertices);
                    break;
                default:
                    throw new InvalidOperationException($"Route object {entity.Handle} ({entity.GetType().Name}) is unsupported. No selected objects have been skipped.");
            }
        }
        if (paths.Count == 0 || paths.Any(path => path.Count < 2))
            throw new InvalidOperationException("Each route entity must contain at least two vertices.");
        if (paths.Sum(path => path.Count) > 2000)
            throw new InvalidOperationException("Select a smaller route with at most 2000 centerline vertices.");

        // Every vertex participates in the graph, including vertices inside a polyline.
        var nodes = new List<Point3d>();
        var edges = new HashSet<(int, int)>();
        var neighbors = new Dictionary<int, List<int>>();
        int Node(Point3d point)
        {
            if (!double.IsFinite(point.X) || !double.IsFinite(point.Y) || !double.IsFinite(point.Z))
                throw new InvalidOperationException("Route contains a non-finite coordinate.");
            var matches = Enumerable.Range(0, nodes.Count).Where(i => nodes[i].DistanceTo(point) <= joinTolerance).ToList();
            if (matches.Count > 1) throw new InvalidOperationException("Route vertices are ambiguous within the connection tolerance.");
            if (matches.Count == 1) return matches[0];
            nodes.Add(point);
            neighbors[nodes.Count - 1] = [];
            return nodes.Count - 1;
        }
        foreach (var path in paths)
        {
            for (var index = 0; index + 1 < path.Count; index++)
            {
                var first = Node(path[index]);
                var second = Node(path[index + 1]);
                if (first == second) throw new InvalidOperationException("Route contains a zero-length or duplicate segment.");
                var key = first < second ? (first, second) : (second, first);
                if (!edges.Add(key)) throw new InvalidOperationException("Route contains overlapping duplicate segments.");
                neighbors[first].Add(second);
                neighbors[second].Add(first);
            }
        }
        if (neighbors.Values.Any(links => links.Count > 2))
            throw new InvalidOperationException("The selected centerline branches or revisits a connection. Select one non-branching path.");
        var ends = neighbors.Where(item => item.Value.Count == 1).Select(item => item.Key).ToList();
        if (ends.Count != 2) throw new InvalidOperationException("Select one open path with exactly two free ends.");
        foreach (var (first, second) in edges)
        {
            var direction = nodes[second] - nodes[first];
            var length = direction.Length;
            for (var index = 0; index < nodes.Count; index++)
            {
                if (index == first || index == second) continue;
                var fraction = (nodes[index] - nodes[first]).DotProduct(direction) / (length * length);
                if (fraction <= joinTolerance / length || fraction >= 1 - joinTolerance / length) continue;
                var closest = nodes[first] + direction * fraction;
                if (closest.DistanceTo(nodes[index]) <= joinTolerance)
                    throw new InvalidOperationException("A selected vertex touches the middle of another segment. Resolve this branch or overlap before routing.");
            }
        }
        var ordered = new List<Point3d>();
        var visited = new HashSet<int>();
        var current = ends[0];
        var previous = -1;
        while (true)
        {
            if (!visited.Add(current)) throw new InvalidOperationException("Selected route contains a loop.");
            ordered.Add(nodes[current]);
            var next = neighbors[current].Where(id => id != previous).ToList();
            if (next.Count == 0) break;
            previous = current;
            current = next[0];
        }
        if (visited.Count != nodes.Count) throw new InvalidOperationException("Selected route pieces are disconnected.");
        return ordered;
    }

    private static List<Extents3d> ReadObstacleExtents(PromptSelectionResult selection, Transaction transaction, HashSet<ObjectId> routeIds)
    {
        var extents = new List<Extents3d>();
        if (selection.Status != PromptStatus.OK) return extents;
        foreach (SelectedObject? selected in selection.Value)
        {
            if (selected?.ObjectId is not ObjectId id || id.IsNull ||
                transaction.GetObject(id, OpenMode.ForRead) is not Entity entity)
                throw new InvalidOperationException("A selected obstacle could not be read. No obstacle has been silently skipped.");
            if (routeIds.Contains(id))
                throw new InvalidOperationException($"Route object {entity.Handle} was also selected as an obstacle. Select the surrounding objects only.");
            Extents3d box;
            try { box = entity.GeometricExtents; }
            catch (System.Exception)
            {
                throw new InvalidOperationException($"Obstacle {entity.Handle} ({entity.GetType().Name}) has no readable 3D bounds.");
            }
            if (box.MinPoint.X >= box.MaxPoint.X || box.MinPoint.Y >= box.MaxPoint.Y || box.MinPoint.Z >= box.MaxPoint.Z)
                throw new InvalidOperationException($"Obstacle {entity.Handle} has zero thickness on an axis. Use a solid with actual thickness for this obstacle.");
            extents.Add(box);
        }
        return extents;
    }

    private static double[] ToArray(Point3d point) => [point.X, point.Y, point.Z];

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
