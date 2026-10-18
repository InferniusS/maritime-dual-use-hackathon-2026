using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.Colors;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;
using System.Globalization;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Application;

namespace Maritime.AutoCAD;

public sealed partial class PreviewCommand
{
    private const string ReviewDimensionLayer = "MARITIME_REVIEW_DIMENSIONS";
    private sealed record ReviewDimension(string Id, string Kind, Point3d Start, Point3d End,
        Point3d Anchor, double Length, double Diameter, double? Radius, double? Angle);

    [CommandMethod("MARITIMEDIMENSIONS", CommandFlags.Modal)]
    public static void ToggleReviewDimensions()
    {
        var document = AcApplication.DocumentManager.MdiActiveDocument;
        if (document is null) return;
        try
        {
            var choice = AskKeyword(document.Editor, "Measured review callouts", ["On", "Off"], "On");
            if (choice is null) return;
            using var transaction = document.Database.TransactionManager.StartTransaction();
            var layers = (LayerTable)transaction.GetObject(document.Database.LayerTableId, OpenMode.ForRead);
            if (!layers.Has(ReviewDimensionLayer)) throw new InvalidOperationException("No measured proposal callouts are saved in this drawing. Save an accepted 3D proposal with the current plugin first.");
            var layer = (LayerTableRecord)transaction.GetObject(layers[ReviewDimensionLayer], OpenMode.ForWrite);
            if (layer.IsLocked || layer.IsFrozen) throw new InvalidOperationException($"Unlock and thaw {ReviewDimensionLayer} first.");
            layer.IsOff = choice == "Off";
            transaction.Commit();
            document.Editor.Regen();
            if (choice == "On") FrameDimensionedReview(document);
            document.Editor.WriteMessage($"\nReview dimensions {choice.ToLowerInvariant()}. Labels use millimetres and describe the saved proposal geometry; they are not fabrication approval.");
        }
        catch (System.Exception exception) { document.Editor.WriteMessage($"\nDimensions stopped: {exception.Message}"); }
    }

    private static void FrameDimensionedReview(Document document)
    {
        var points = new List<Point3d>();
        using var transaction = document.Database.TransactionManager.StartOpenCloseTransaction();
        var blocks = (BlockTable)transaction.GetObject(document.Database.BlockTableId, OpenMode.ForRead);
        var model = (BlockTableRecord)transaction.GetObject(blocks[BlockTableRecord.ModelSpace], OpenMode.ForRead);
        foreach (ObjectId id in model)
        {
            if (id.IsErased || transaction.GetObject(id, OpenMode.ForRead) is not Entity entity) continue;
            if (entity.Layer != ReviewDimensionLayer && entity.Layer != ProposalMeshLayer && entity.Layer != ReferenceMeshLayer) continue;
            var layer = (LayerTableRecord)transaction.GetObject(entity.LayerId, OpenMode.ForRead);
            if (layer.IsOff || layer.IsFrozen) continue;
            try { points.Add(entity.GeometricExtents.MinPoint); points.Add(entity.GeometricExtents.MaxPoint); }
            catch (Autodesk.AutoCAD.Runtime.Exception) { }
        }
        if (points.Count > 0) FrameMeshReview(document, points);
    }

    private static List<ReviewDimension> ReadAssemblyDimensions(JsonObject assembly)
    {
        if (Text(assembly, "status") != "ready" || Text(assembly, "units") != "mm")
            throw new InvalidOperationException("Dimensions require an accepted millimetre assembly.");
        var diameter = PositiveDimension(assembly, "outer_diameter_mm");
        if (assembly["components"] is not JsonArray { Count: > 0 } components || components.Count > 2048)
            throw new InvalidOperationException("The accepted assembly has no bounded component list for measured callouts.");
        var dimensions = new List<ReviewDimension>();
        var ids = new HashSet<string>();
        Point3d? previousEnd = null;
        foreach (var item in components)
        {
            if (item is null) throw new InvalidOperationException("An assembly component is empty.");
            var id = Text(item, "id");
            if (!ids.Add(id)) throw new InvalidOperationException("Assembly components must have unique identities.");
            var kind = Text(item, "kind");
            var start = ReadCoordinate(item["start"], "piece start");
            var end = ReadCoordinate(item["end"], "piece end");
            if (previousEnd is not null && previousEnd.Value.DistanceTo(start) > .001)
                throw new InvalidOperationException("The dimensioned assembly contains disconnected components.");
            previousEnd = end;
            var length = PositiveDimension(item, "length_mm");
            if (Math.Abs(PositiveDimension(item, "outer_diameter_mm") - diameter) > .0001)
                throw new InvalidOperationException("The dimensioned component changed outside diameter.");
            var samples = ReadPoints(item["centerline_points"], "piece centerline");
            if (samples[0].DistanceTo(start) > .001 || samples[^1].DistanceTo(end) > .001)
                throw new InvalidOperationException("Piece dimension endpoints do not match its centerline.");
            if (kind == "straight")
            {
                if (Math.Abs(start.DistanceTo(end) - length) > Math.Max(.001, length * 1e-8))
                    throw new InvalidOperationException("A straight piece's stated length does not match its endpoints.");
                dimensions.Add(new(id, kind, start, end, start + (end - start) * .5, length, diameter, null, null));
            }
            else if (kind == "bend")
            {
                var radius = PositiveDimension(item, "radius_mm");
                var angle = PositiveDimension(item, "angle_deg");
                if (angle >= 180) throw new InvalidOperationException("The returned bend angle cannot be dimensioned by this review workflow.");
                var center = ReadCoordinate(item["center"], "bend center");
                var tolerance = Math.Max(.001, radius * 1e-8);
                if (Math.Abs(start.DistanceTo(center) - radius) > tolerance || Math.Abs(end.DistanceTo(center) - radius) > tolerance ||
                    Math.Abs(start.DistanceTo(end) - 2 * radius * Math.Sin(angle * Math.PI / 360)) > tolerance ||
                    Math.Abs(length - radius * angle * Math.PI / 180) > Math.Max(.001, length * 1e-8))
                    throw new InvalidOperationException("A bend's radius, angle or arc length does not match its returned geometry.");
                dimensions.Add(new(id, kind, start, end, samples[samples.Count / 2], length, diameter, radius, angle));
            }
            else throw new InvalidOperationException("An unsupported piece kind cannot receive measured callouts.");
        }
        var expectedLength = PositiveDimension(assembly["metrics"] ?? throw new InvalidOperationException("Assembly metrics are missing."), "centerline_length_mm");
        if (Math.Abs(dimensions.Sum(item => item.Length) - expectedLength) > Math.Max(.001, expectedLength * 1e-8))
            throw new InvalidOperationException("Piece dimensions do not add up to the returned assembly length.");
        return dimensions;
    }

    private static double PositiveDimension(JsonNode node, string key)
    {
        var value = Number(node, key);
        if (value <= 0 || value > 1e12) throw new InvalidOperationException($"The measured {key} is out of range.");
        return value;
    }

    private static string DimensionText(string value, int maximum = 180)
    {
        // DBText is deliberately plain; strip control characters and formatting
        // metacharacters even if a source CAD name or handle is unexpected.
        var cleaned = Regex.Replace(value, "[^A-Za-z0-9 .,:;=+/#()_-]", "_");
        return cleaned.Length > maximum ? cleaned[..maximum] : cleaned;
    }

    private static string DimensionNumber(double value) => value.ToString("0.###", CultureInfo.InvariantCulture);

    private static void SaveReviewDimensions(Document document, Transaction transaction, BlockTableRecord model,
        IReadOnlyList<ReviewDimension> dimensions, double drawingScale, JsonObject sourceMetadata)
    {
        if (dimensions.Count == 0) return;
        if (!double.IsFinite(drawingScale) || drawingScale <= 0) throw new InvalidOperationException("Review dimensions need a positive drawing unit conversion.");
        var layers = (LayerTable)transaction.GetObject(document.Database.LayerTableId, OpenMode.ForRead);
        ObjectId layerId;
        if (layers.Has(ReviewDimensionLayer))
        {
            var layer = (LayerTableRecord)transaction.GetObject(layers[ReviewDimensionLayer], OpenMode.ForRead);
            if (layer.IsLocked || layer.IsFrozen) throw new InvalidOperationException($"Unlock and thaw {ReviewDimensionLayer} before saving measured callouts.");
            layerId = layer.ObjectId; // Keep the user's On/Off choice for later saves.
        }
        else layerId = GetReviewLayer(document.Database, transaction, ReviewDimensionLayer, 2);
        using var view = document.Editor.GetCurrentView();
        var normal = view.ViewDirection.GetNormal();
        var right = Math.Abs(normal.DotProduct(Vector3d.ZAxis)) > .99
            ? Vector3d.XAxis : Vector3d.ZAxis.CrossProduct(normal).GetNormal();
        var up = normal.CrossProduct(right).GetNormal();
        var planeRight = right.TransformBy(Matrix3d.WorldToPlane(normal));
        var rotation = Math.Atan2(planeRight.Y, planeRight.X);
        var height = Math.Max(15, dimensions[0].Diameter * .24) / drawingScale;
        var routeIdentity = sourceMetadata["global_route_number"] is JsonValue global
            ? "Route " + global.ToJsonString()
            : sourceMetadata["selection_number"] is JsonValue selection ? "Selection " + selection.ToJsonString()
            : sourceMetadata["synthetic_demo"]?.GetValue<bool>() == true ? "Synthetic test route"
            : sourceMetadata["candidate_id"]?.GetValue<string>() ?? "Selected route";
        var handles = sourceMetadata["source_handles"] is JsonArray sourceHandles
            ? string.Join(",", sourceHandles.Select(item => item?.GetValue<string>() ?? "?")) : "synthetic / see report";
        var header = Scale(dimensions[0].Start, drawingScale) + up * height * 9;
        var headerMetadata = (JsonObject)sourceMetadata.DeepClone();
        headerMetadata["role"] = "review_dimension_header";
        headerMetadata["dimension_units"] = "mm";
        headerMetadata["fabrication_approved"] = false;
        AddText(routeIdentity + " | source " + handles, header, height, headerMetadata);
        AddText("REVIEW GEOMETRY DIMENSIONS (mm) - fabrication approval pending", header - up * height * 1.5, height * .8, headerMetadata);
        for (var index = 0; index < dimensions.Count; index++)
        {
            var part = dimensions[index];
            var anchor = Scale(part.Anchor, drawingScale);
            var location = anchor + up * (part.Diameter / drawingScale * .7 + height * (3 + index % 3));
            var record = (JsonObject)sourceMetadata.DeepClone();
            record["role"] = "review_piece_dimension"; record["part_id"] = part.Id; record["part_kind"] = part.Kind;
            record["dimension_units"] = "mm"; record["centerline_length_mm"] = part.Length;
            record["outer_diameter_mm"] = part.Diameter; record["radius_mm"] = part.Radius; record["angle_deg"] = part.Angle;
            record["start_world_mm"] = new JsonArray(part.Start.X, part.Start.Y, part.Start.Z);
            record["end_world_mm"] = new JsonArray(part.End.X, part.End.Y, part.End.Z);
            record["fabrication_approved"] = false;
            using var leader = new Line(anchor, location - up * height * .3);
            leader.SetDatabaseDefaults(document.Database); leader.LayerId = layerId;
            leader.Color = Color.FromColorIndex(ColorMethod.ByAci, 2);
            model.AppendEntity(leader); transaction.AddNewlyCreatedDBObject(leader, true);
            WriteReviewMetadata(leader, transaction, record);
            AddText(routeIdentity + " / " + part.Id, location + up * height * 1.4, height * .8, record);
            var measurement = part.Kind == "straight"
                ? $"STRAIGHT CL {DimensionNumber(part.Length)} mm | OD {DimensionNumber(part.Diameter)} mm"
                : $"BEND R {DimensionNumber(part.Radius!.Value)} mm / {DimensionNumber(part.Angle!.Value)} deg | ARC {DimensionNumber(part.Length)} mm | OD {DimensionNumber(part.Diameter)} mm";
            AddText(measurement, location, height, record);
        }
        void AddText(string value, Point3d position, double textHeight, JsonObject metadata)
        {
            using var label = new DBText();
            label.SetDatabaseDefaults(document.Database);
            label.LayerId = layerId; label.Color = Color.FromColorIndex(ColorMethod.ByAci, 2);
            label.Normal = normal; label.Rotation = rotation;
            label.Position = position; label.Height = textHeight;
            label.TextString = DimensionText(value);
            model.AppendEntity(label); transaction.AddNewlyCreatedDBObject(label, true);
            WriteReviewMetadata(label, transaction, metadata);
        }
    }
}
