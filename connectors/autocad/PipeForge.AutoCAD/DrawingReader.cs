using System.Globalization;
using System.Text.Json.Nodes;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;

namespace PipeForge.AutoCAD;

internal sealed record Metadata(JsonObject Model, Dictionary<string, string> ObjectMap)
{
    public static Metadata Load(string path)
    {
        var root = JsonNode.Parse(File.ReadAllText(path)) as JsonObject
            ?? throw new InvalidDataException("Expected a metadata JSON object.");
        var model = root["model"] as JsonObject
            ?? throw new InvalidDataException("Use { model: <PipeForge model>, object_map: { engineering_id: CAD_handle } }.");
        if (model["units"]?.GetValue<string>() != "m")
            throw new InvalidDataException("Metadata must use canonical meters, with units='m'.");
        if (model["pipes"] is not JsonArray || model["obstacles"] is not JsonArray)
            throw new InvalidDataException("Model must explicitly contain pipes and obstacles arrays.");
        var map = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var item in root["object_map"] as JsonObject ?? new JsonObject())
        {
            // Server snapshots normalize handles to arrays. This adapter supports one native
            // centerline/entity per engineering object, so singleton arrays are accepted.
            var node = item.Value is JsonArray values
                ? values.Count == 1 ? values[0] : throw new InvalidDataException("Native adapter requires one CAD handle per engineering object.")
                : item.Value;
            var value = node?.GetValue<string>() ?? throw new InvalidDataException("Handles must be strings or singleton arrays.");
            map.Add(item.Key, DrawingReader.CanonicalHandle(value));
        }
        if (map.Values.Distinct(StringComparer.OrdinalIgnoreCase).Count() != map.Count)
            throw new InvalidDataException("One CAD entity may map to only one engineering object.");
        var metadata = new Metadata((JsonObject)model.DeepClone(), map);
        foreach (var id in map.Keys) _ = metadata.Find(id);
        return metadata;
    }

    public (JsonObject Object, bool IsPipe) Find(string id)
    {
        foreach (var name in new[] { "pipes", "obstacles" })
            foreach (var item in (JsonArray)Model[name]!)
                if (item is JsonObject obj && obj["id"]?.GetValue<string>() == id)
                    return (obj, name == "pipes");
        throw new InvalidDataException($"Engineering ID '{id}' has no supplied metadata. Add its explicit semantics to the model first.");
    }
}

/// <summary>Call exclusively on the AutoCAD UI thread, in a document lock and read transaction.</summary>
internal static class DrawingReader
{
    public static string SourceUnits(Database database) => (int)database.Insunits switch
    {
        1 => "in", 2 => "ft", 4 => "mm", 5 => "cm", 6 => "m", 7 => "km",
        _ => throw new InvalidDataException("INSUNITS does not identify a supported explicit source unit.")
    };

    public static double MetersPerUnit(Database database) => (int)database.Insunits switch
    {
        1 => 0.0254, // Inches
        2 => 0.3048, // Feet (international)
        4 => 0.001,  // Millimeters
        5 => 0.01,   // Centimeters
        6 => 1.0,    // Meters
        7 => 1000.0, // Kilometers
        _ => throw new InvalidDataException($"INSUNITS={(int)database.Insunits} is unsupported or unitless. Set correct drawing units explicitly; no guessed scaling is allowed.")
    };

    public static string CanonicalHandle(string handle) =>
        new Handle(long.Parse(handle, NumberStyles.HexNumber, CultureInfo.InvariantCulture)).ToString();

    public static ObjectId Resolve(Database database, string handle)
    {
        // An unresolved binding is an error, never evidence that source geometry was erased.
        ObjectId id;
        try { id = database.GetObjectId(false, new Handle(long.Parse(handle, NumberStyles.HexNumber, CultureInfo.InvariantCulture)), 0); }
        catch (Autodesk.AutoCAD.Runtime.Exception error)
        {
            throw new InvalidDataException($"CAD handle {handle} could not be resolved in this drawing. Correct the binding and reconnect; source geometry was not removed.", error);
        }
        if (id.IsNull) throw new InvalidDataException($"CAD handle {handle} does not resolve in this drawing. Correct the mapping and reconnect.");
        return id;
    }

    public static JsonObject ReadObject(Database database, Transaction transaction, JsonObject metadata, string handle, bool isPipe, double scale)
    {
        var id = Resolve(database, handle);
        if (id.IsNull || id.IsErased) throw new InvalidDataException($"CAD handle {handle} does not identify a live entity.");
        if (transaction.GetObject(id, OpenMode.ForRead) is not Entity entity)
            throw new InvalidDataException($"CAD handle {handle} is not an entity.");
        if (entity is BlockReference)
            throw new InvalidDataException($"CAD handle {handle} is a block/xref reference. Live bindings are unsupported until definition changes can be tracked; use separately reviewed static metadata or supported standalone geometry.");
        var table = (BlockTable)transaction.GetObject(database.BlockTableId, OpenMode.ForRead);
        if (entity.OwnerId != table[BlockTableRecord.ModelSpace])
            throw new InvalidDataException($"CAD handle {handle} is not a standalone ModelSpace entity. Nested and paper-space bindings are unsupported.");
        var item = (JsonObject)metadata.DeepClone();
        if (isPipe)
        {
            if (entity is not Polyline3d polyline || polyline.Closed || polyline.PolyType != Poly3dType.SimplePoly)
                throw new InvalidDataException($"Pipe {item["id"]} must map to an open, simple 3D polyline; splines, solids and 2D polylines are unsupported.");
            var points = new JsonArray();
            foreach (ObjectId vertexId in polyline)
            {
                if (points.Count >= 1000) throw new InvalidDataException("A native centerline exceeds the supported 1000-vertex model limit.");
                var vertex = (PolylineVertex3d)transaction.GetObject(vertexId, OpenMode.ForRead);
                points.Add(Point(vertex.Position, scale));
            }
            if (points.Count < 2) throw new InvalidDataException("A pipe needs at least two centerline vertices.");
            item["points"] = points;
        }
        else
        {
            // WCS extents are a conservative axis-aligned envelope, never an exact solid.
            var extents = entity.GeometricExtents;
            if (extents.MinPoint.X >= extents.MaxPoint.X || extents.MinPoint.Y >= extents.MaxPoint.Y || extents.MinPoint.Z >= extents.MaxPoint.Z)
                throw new InvalidDataException("Obstacle extents need positive size on all three axes; use a 3D entity or explicit static metadata.");
            item["min"] = Point(extents.MinPoint, scale);
            item["max"] = Point(extents.MaxPoint, scale);
        }
        return item;
    }

    private static JsonArray Point(Point3d point, double scale) => new(point.X * scale, point.Y * scale, point.Z * scale);
}
