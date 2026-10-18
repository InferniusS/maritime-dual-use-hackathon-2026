using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.Colors;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.GraphicsInterface;
using Autodesk.AutoCAD.Runtime;
using System.Text.Json.Nodes;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Application;

namespace Maritime.AutoCAD;

public sealed partial class PreviewCommand
{
    private const string ProposalMeshLayer = "MARITIME_PIPE_PROPOSAL";
    private const string ReferenceMeshLayer = "MARITIME_PIPE_REFERENCE";
    private const string DemoSceneLayer = "MARITIME_DEMO_CONTEXT";
    private const int MaximumMeshVertices = 100000;
    private const int MaximumMeshFaces = 200000;
    private sealed record MeshPart(string Id, string Kind, Point3d[] Vertices, int[] Indices);

    [CommandMethod("MARITIMEDEMO", CommandFlags.Modal)]
    public static void RunSyntheticDemo()
    {
        var document = AcApplication.DocumentManager.MdiActiveDocument;
        if (document is null) return;
        var editor = document.Editor;
        try
        {
            RequireEmptyDemoDrawing(document);
            var selected = editor.GetFileNameForOpen(new PromptOpenFileOptions("\nChoose a synthetic demo layout JSON")
                { Filter = "JSON files (*.json)|*.json" });
            if (selected.Status != PromptStatus.OK) return;
            var info = new FileInfo(selected.StringResult);
            if (info.Length > 8 * 1024 * 1024) throw new InvalidOperationException("Demo layouts must be at most 8 MB.");
            var root = JsonNode.Parse(File.ReadAllText(selected.StringResult)) as JsonObject
                ?? throw new InvalidOperationException("The demo file must contain a JSON object.");
            var layout = (JsonObject)(root["layout"] as JsonObject ?? root).DeepClone();
            if (layout["provenance"]?.GetValue<string>() != "synthetic" || layout["cad_context"] is not null)
                throw new InvalidOperationException("MARITIMEDEMO accepts labeled synthetic layouts only. Use MARITIMECAD for source drawings.");
            if (layout["proposal"]?["mode"]?.GetValue<string>() != "rounded_pipe")
                throw new InvalidOperationException("Choose a demo with proposal.mode = rounded_pipe.");
            editor.WriteMessage("\nSYNTHETIC TEST: the generated scene is an invented test layout, not an organizer drawing. Output coordinates use millimetres.");
            using var client = CreateClient();
            CheckService(client);
            RunAndReview(document, client, layout, 1, document.Database.FingerprintGuid.ToString(), demo: true);
        }
        catch (System.Exception exception) { editor.WriteMessage($"\nDemo stopped: {exception.Message}"); }
    }

    [CommandMethod("MARITIMEVIEW", CommandFlags.Modal)]
    public static void CompareSavedProposal()
    {
        var document = AcApplication.DocumentManager.MdiActiveDocument;
        if (document is null) return;
        try
        {
            var choice = AskKeyword(document.Editor, "Show saved review geometry", ["Original", "Proposal", "Both"], "Both");
            if (choice is null) return;
            using var transaction = document.Database.TransactionManager.StartTransaction();
            var layers = (LayerTable)transaction.GetObject(document.Database.LayerTableId, OpenMode.ForRead);
            if (!layers.Has(ProposalMeshLayer) || !layers.Has(ReferenceMeshLayer))
                throw new InvalidOperationException("No saved 3D comparison exists in this drawing. Save a 3D proposal first.");
            foreach (var name in new[] { ProposalMeshLayer, ReferenceMeshLayer, SuggestionLayer })
            {
                if (!layers.Has(name)) continue;
                var layer = (LayerTableRecord)transaction.GetObject(layers[name], OpenMode.ForWrite);
                if (layer.IsLocked || layer.IsFrozen) throw new InvalidOperationException($"Make {name} unlocked and thawed first.");
                layer.IsOff = name == ReferenceMeshLayer ? choice == "Proposal" : choice == "Original";
            }
            transaction.Commit();
            document.Editor.Regen();
            document.Editor.WriteMessage("\nChanged review layers only. Source CAD objects remain visible and unchanged. Cyan is the original envelope; green is the proposal.");
        }
        catch (System.Exception exception) { document.Editor.WriteMessage($"\nComparison stopped: {exception.Message}"); }
    }

    private static void RequireEmptyDemoDrawing(Document document)
    {
        if (!document.Database.TileMode) throw new InvalidOperationException("Run MARITIMEDEMO in Model space in a new empty drawing.");
        using var transaction = document.Database.TransactionManager.StartTransaction();
        var table = (BlockTable)transaction.GetObject(document.Database.BlockTableId, OpenMode.ForRead);
        var model = (BlockTableRecord)transaction.GetObject(table[BlockTableRecord.ModelSpace], OpenMode.ForRead);
        if (model.Cast<ObjectId>().Any(id => !id.IsErased))
            throw new InvalidOperationException("Open a new empty drawing before MARITIMEDEMO. Existing drawing objects will not be moved or replaced.");
    }

    private static bool ShowAssemblyReview(Document document, JsonObject layout, JsonObject result,
        IReadOnlyList<Point3d> candidate, double drawingScale, JsonObject metadata, bool demo)
    {
        if (result["assembly"] is not JsonObject assembly || assembly["status"]?.GetValue<string>() != "ready") return false;
        if (Text(assembly, "units") != "mm") throw new InvalidOperationException("The physical proposal must use millimetres.");
        var diameter = Number(assembly, "outer_diameter_mm");
        if (Math.Abs(diameter - Number(layout["pipe"]!, "diameter")) > 0.0001)
            throw new InvalidOperationException("The physical proposal changed the pipe diameter.");
        var centerline = ReadPoints(assembly["centerline_points"], "rounded centerline");
        if (centerline[0].DistanceTo(candidate[0]) > 0.0001 || centerline[^1].DistanceTo(candidate[^1]) > 0.0001)
            throw new InvalidOperationException("The physical proposal moved a fixed endpoint.");
        var proposed = ReadMeshParts(assembly["meshes"], "proposal");
        var reference = ReadMeshParts(assembly["reference_meshes"], "original reference");
        var scene = demo ? ReadDemoBoxes(layout) : new List<MeshPart>();
        if (proposed.Count == 0) throw new InvalidOperationException("The ready physical proposal contains no mesh geometry.");
        metadata["representation"] = "outer_envelope_mesh";
        metadata["catalog_verified"] = false;
        metadata["outer_diameter_mm"] = diameter;
        metadata["bend_radius_mm"] = assembly["bend_radius_mm"]?.DeepClone();
        metadata["assembly_metrics"] = assembly["metrics"]?.DeepClone();
        metadata["synthetic_demo"] = demo;
        var editor = document.Editor;
        editor.WriteMessage($"\n3D outer-envelope proposal: diameter {diameter:0.###} mm; bend radius {Number(assembly, "bend_radius_mm"):0.###} mm.");
        if (assembly["metrics"] is JsonObject metrics)
        {
            var labels = new Dictionary<string, string>
            {
                ["centerline_length_mm"] = "Rounded pipe centerline length in mm",
                ["straight_piece_count"] = "Proposed straight pieces", ["bend_count"] = "Rounded bends",
                ["proposed_internal_joint_count"] = "Proposed internal joints"
            };
            foreach (var (field, label) in labels)
                if (metrics[field] is not null) editor.WriteMessage($"\n{label}: {metrics[field]}");
        }
        editor.WriteMessage("\nGreen shows the rounded proposal at actual outside diameter. Cyan is the original reference for comparison; its representation is described in the report. These meshes are review geometry; catalog selection and fabrication approval remain open.");
        if (demo) editor.WriteMessage("\nGrey boxes are the synthetic obstacle envelopes used by the search.");
        var manager = TransientManager.CurrentTransientManager;
        var viewports = new IntegerCollection();
        var transient = new List<SubDMesh>();
        var allPoints = proposed.Concat(reference).Concat(scene).SelectMany(part => part.Vertices).Select(p => Scale(p, drawingScale)).ToArray();
        using var previousView = editor.GetCurrentView();
        var saved = false;
        try
        {
            FrameMeshReview(document, allPoints, demo);
            var mode = "Both";
            while (true)
            {
                ClearTransientMeshes(manager, viewports, transient);
                Add(scene, 8);
                if (mode != "Proposal") Add(reference, 4);
                if (mode != "Original") Add(proposed, 3);
                editor.Regen();
                var selected = AskKeyword(editor, "Compare 3D review; Save keeps proposal, original reference and any demo context",
                    ["Original", "Proposal", "Both", "Save", "Discard"], "Save");
                if (selected is null || selected == "Discard") return true;
                if (selected == "Save")
                {
                    SaveAssembly(document, proposed, reference, scene, centerline, drawingScale, metadata, demo);
                    saved = true;
                    return true;
                }
                mode = selected;
            }
        }
        finally
        {
            ClearTransientMeshes(manager, viewports, transient);
            if (!saved) editor.SetCurrentView(previousView);
            editor.Regen();
        }
        void Add(IEnumerable<MeshPart> parts, short color)
        {
            foreach (var part in parts)
            {
                var mesh = BuildMesh(part, drawingScale, color);
                if (!manager.AddTransient(mesh, TransientDrawingMode.Main, 128, viewports))
                {
                    mesh.Dispose();
                    throw new InvalidOperationException("AutoCAD could not display a 3D review mesh.");
                }
                transient.Add(mesh);
            }
        }
    }

    private static bool ShowRejectedDemo(Document document, JsonObject layout, JsonObject result)
    {
        if (result["assembly"]?["reference_meshes"] is not JsonArray) return false;
        var reference = ReadMeshParts(result["assembly"]!["reference_meshes"], "original reference");
        var scene = ReadDemoBoxes(layout);
        var manager = TransientManager.CurrentTransientManager;
        var viewports = new IntegerCollection();
        var meshes = new List<SubDMesh>();
        using var previousView = document.Editor.GetCurrentView();
        try
        {
            FrameMeshReview(document, reference.Concat(scene).SelectMany(p => p.Vertices).ToArray(), true);
            foreach (var part in scene.Concat(reference))
            {
                var mesh = BuildMesh(part, 1, scene.Contains(part) ? (short)8 : (short)4);
                if (!manager.AddTransient(mesh, TransientDrawingMode.Main, 128, viewports))
                {
                    mesh.Dispose();
                    throw new InvalidOperationException("AutoCAD could not display the rejected synthetic scene.");
                }
                meshes.Add(mesh);
            }
            document.Editor.Regen();
            document.Editor.WriteMessage("\nSYNTHETIC TEST: cyan shows the supplied original route. No green physical proposal was accepted; no geometry will be saved.");
            document.Editor.GetString(new PromptStringOptions("\nPress Enter to finish the rejected-case review: "));
            return true;
        }
        finally
        {
            ClearTransientMeshes(manager, viewports, meshes);
            document.Editor.SetCurrentView(previousView);
            document.Editor.Regen();
        }
    }

    private static List<MeshPart> ReadMeshParts(JsonNode? node, string label)
    {
        if (node is not JsonArray parts || parts.Count > 2048)
            throw new InvalidOperationException($"The {label} mesh list is missing or exceeds 2048 parts.");
        var result = new List<MeshPart>();
        var totalVertices = 0;
        var totalFaces = 0;
        foreach (var part in parts)
        {
            if (part is not JsonObject item || item["vertices"] is not JsonArray rawVertices || item["faces"] is not JsonArray rawFaces)
                throw new InvalidOperationException($"An invalid {label} mesh was returned.");
            totalVertices += rawVertices.Count; totalFaces += rawFaces.Count;
            if (rawVertices.Count < 3 || rawFaces.Count < 1 || totalVertices > MaximumMeshVertices || totalFaces > MaximumMeshFaces)
                throw new InvalidOperationException($"The {label} mesh size exceeds the review limit.");
            var vertices = rawVertices.Select(point => ReadCoordinate(point, label)).ToArray();
            var indices = new List<int>();
            foreach (var rawFace in rawFaces)
            {
                if (rawFace is not JsonArray face || face.Count is < 3 or > 4)
                    throw new InvalidOperationException($"The {label} mesh must contain triangle or quadrilateral faces.");
                indices.Add(face.Count);
                var faceIndices = face.Select(value => value?.GetValue<int>() ?? -1).ToArray();
                if (faceIndices.Any(value => value < 0 || value >= vertices.Length) || faceIndices.Distinct().Count() != faceIndices.Length)
                    throw new InvalidOperationException($"The {label} mesh contains invalid face indices.");
                indices.AddRange(faceIndices);
            }
            result.Add(new MeshPart(Text(item, "id"), Text(item, "kind"), vertices, indices.ToArray()));
        }
        return result;
    }

    private static Point3d ReadCoordinate(JsonNode? node, string label)
    {
        if (node is not JsonArray array || array.Count != 3) throw new InvalidOperationException($"Invalid {label} coordinate.");
        var v = array.Select(value => value?.GetValue<double>() ?? double.NaN).ToArray();
        if (v.Any(value => !double.IsFinite(value) || Math.Abs(value) > 1e12)) throw new InvalidOperationException($"Invalid {label} coordinate range.");
        return new Point3d(v[0], v[1], v[2]);
    }

    private static List<MeshPart> ReadDemoBoxes(JsonObject layout)
    {
        var result = new List<MeshPart>();
        if (layout["obstacles"] is not JsonArray obstacles || obstacles.Count > 512)
            throw new InvalidOperationException("The synthetic demo needs at most 512 obstacles.");
        foreach (var obstacle in obstacles)
        {
            if (obstacle is null) throw new InvalidOperationException("Empty synthetic obstacle.");
            // A box remains labeled as an envelope even when the backend refines collision using capsules.
            var min = ReadCoordinate(obstacle["min"], "obstacle minimum");
            var max = ReadCoordinate(obstacle["max"], "obstacle maximum");
            var vertices = new[] { new Point3d(min.X,min.Y,min.Z),new Point3d(max.X,min.Y,min.Z),new Point3d(max.X,max.Y,min.Z),new Point3d(min.X,max.Y,min.Z),new Point3d(min.X,min.Y,max.Z),new Point3d(max.X,min.Y,max.Z),new Point3d(max.X,max.Y,max.Z),new Point3d(min.X,max.Y,max.Z) };
            result.Add(new MeshPart(Text(obstacle, "id"), "synthetic_obstacle_envelope", vertices,
                [4,0,3,2,1,4,4,5,6,7,4,0,1,5,4,4,1,2,6,5,4,2,3,7,6,4,3,0,4,7]));
        }
        return result;
    }

    private static SubDMesh BuildMesh(MeshPart part, double drawingScale, short color)
    {
        // Autodesk SetSubDMesh: each face starts with its vertex count, followed by zero-based indices.
        // Smoothing stays zero so AutoCAD does not alter the checked backend geometry.
        var mesh = new SubDMesh();
        try
        {
            mesh.SetSubDMesh(new Point3dCollection(part.Vertices.Select(p => Scale(p, drawingScale)).ToArray()), new Int32Collection(part.Indices), 0);
            mesh.Color = Color.FromColorIndex(ColorMethod.ByAci, color);
            return mesh;
        }
        catch { mesh.Dispose(); throw; }
    }

    private static void ClearTransientMeshes(TransientManager manager, IntegerCollection viewports, List<SubDMesh> meshes)
    {
        foreach (var mesh in meshes)
        {
            try { manager.EraseTransient(mesh, viewports); } catch (System.Exception) { }
            try { mesh.Dispose(); } catch (System.Exception) { }
        }
        meshes.Clear();
    }

    private static void SaveAssembly(Document document, IReadOnlyList<MeshPart> proposal,
        IReadOnlyList<MeshPart> reference, IReadOnlyList<MeshPart> scene, IReadOnlyList<Point3d> centerline,
        double drawingScale, JsonObject metadata, bool demo)
    {
        if (demo) RequireEmptyDemoDrawing(document);
        using var transaction = document.Database.TransactionManager.StartTransaction();
        var blocks = (BlockTable)transaction.GetObject(document.Database.BlockTableId, OpenMode.ForRead);
        var model = (BlockTableRecord)transaction.GetObject(blocks[BlockTableRecord.ModelSpace], OpenMode.ForWrite);
        AddParts(proposal, ProposalMeshLayer, 3, "proposal");
        AddParts(reference, ReferenceMeshLayer, 4, "original_reference");
        if (scene.Count > 0) AddParts(scene, DemoSceneLayer, 8, "synthetic_context");
        var layerId = GetReviewLayer(document.Database, transaction, SuggestionLayer, 3);
        using var route = new Polyline3d(Poly3dType.SimplePoly, new Point3dCollection(centerline.Select(p => Scale(p,drawingScale)).ToArray()), false);
        route.SetDatabaseDefaults(document.Database); route.LayerId = layerId;
        model.AppendEntity(route); transaction.AddNewlyCreatedDBObject(route, true);
        WriteReviewMetadata(route, transaction, metadata);
        if (demo)
        {
            document.Database.Insunits = UnitsValue.Millimeters;
            var minX = scene.Concat(proposal).SelectMany(p => p.Vertices).Min(p => p.X);
            var minY = scene.Concat(proposal).SelectMany(p => p.Vertices).Min(p => p.Y);
            using var label = new DBText { TextString = "SYNTHETIC TEST - cyan original / green proposal - review meshes", Position = new Point3d(minX, minY - 300, 0), Height = 80, LayerId = GetReviewLayer(document.Database, transaction, DemoSceneLayer, 8) };
            model.AppendEntity(label); transaction.AddNewlyCreatedDBObject(label, true);
        }
        transaction.Commit();
        document.Editor.WriteMessage($"\nSaved {proposal.Count} green review meshes on {ProposalMeshLayer}, with cyan original reference on {ReferenceMeshLayer}. Use MARITIMEVIEW to switch Original/Proposal/Both. Source objects were not replaced. One UNDO removes this saved comparison.");
        void AddParts(IEnumerable<MeshPart> parts, string layerName, short color, string role)
        {
            var partLayer = GetReviewLayer(document.Database, transaction, layerName, color);
            foreach (var part in parts)
            {
                using var mesh = BuildMesh(part, drawingScale, color);
                mesh.SetDatabaseDefaults(document.Database); mesh.LayerId = partLayer;
                mesh.Color = Color.FromColorIndex(ColorMethod.ByAci, color);
                model.AppendEntity(mesh); transaction.AddNewlyCreatedDBObject(mesh, true);
                var record = (JsonObject)metadata.DeepClone(); record["part_id"] = part.Id; record["part_kind"] = part.Kind; record["role"] = role;
                WriteReviewMetadata(mesh, transaction, record);
            }
        }
    }

    private static ObjectId GetReviewLayer(Database database, Transaction transaction, string name, short color)
    {
        var layers = (LayerTable)transaction.GetObject(database.LayerTableId, OpenMode.ForRead);
        if (layers.Has(name))
        {
            var layer = (LayerTableRecord)transaction.GetObject(layers[name], OpenMode.ForRead);
            if (layer.IsLocked || layer.IsFrozen || layer.IsOff) throw new InvalidOperationException($"Make {name} unlocked, thawed and visible before saving.");
            return layer.ObjectId;
        }
        layers.UpgradeOpen();
        using var created = new LayerTableRecord { Name = name, Color = Color.FromColorIndex(ColorMethod.ByAci, color) };
        var id = layers.Add(created); transaction.AddNewlyCreatedDBObject(created, true); return id;
    }

    private static void WriteReviewMetadata(Entity entity, Transaction transaction, JsonObject metadata)
    {
        entity.CreateExtensionDictionary();
        var dictionary = (DBDictionary)transaction.GetObject(entity.ExtensionDictionary, OpenMode.ForWrite);
        using var record = new Xrecord();
        var json = metadata.ToJsonString();
        record.Data = new ResultBuffer(Enumerable.Range(0, (json.Length + 239) / 240)
            .Select(index => new TypedValue((int)DxfCode.Text, json.Substring(index * 240, Math.Min(240, json.Length - index * 240)))).ToArray());
        dictionary.SetAt("MARITIME_SOURCE", record); transaction.AddNewlyCreatedDBObject(record, true);
    }

    private static void FrameMeshReview(Document document, IReadOnlyList<Point3d> points, bool demo)
    {
        using var view = document.Editor.GetCurrentView();
        using var transaction = document.Database.TransactionManager.StartTransaction();
        var styles = (DBDictionary)transaction.GetObject(document.Database.VisualStyleDictionaryId, OpenMode.ForRead);
        if (styles.Contains("Conceptual")) view.VisualStyleId = styles.GetAt("Conceptual");
        if (demo && points.Count > 0)
        {
            var min = new Point3d(points.Min(p => p.X), points.Min(p => p.Y), points.Min(p => p.Z));
            var max = new Point3d(points.Max(p => p.X), points.Max(p => p.Y), points.Max(p => p.Z));
            var aspect = Math.Max(0.5, view.Width / Math.Max(1, view.Height));
            view.Target = new Point3d((min.X+max.X)/2,(min.Y+max.Y)/2,(min.Z+max.Z)/2);
            view.ViewDirection = new Vector3d(-1,-1,1); view.ViewTwist = 0; view.CenterPoint = Point2d.Origin; view.PerspectiveEnabled = false;
            view.Height = Math.Max(1000, min.DistanceTo(max)*1.25); view.Width = view.Height * aspect;
        }
        document.Editor.SetCurrentView(view);
    }
}
