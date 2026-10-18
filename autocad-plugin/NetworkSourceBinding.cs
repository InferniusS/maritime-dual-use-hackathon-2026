using System.Text.Json;
using System.Text.Json.Nodes;

namespace Maritime.AutoCAD;

/// <summary>
/// Immutable in-session extraction evidence. A file-provided digest alone is
/// not evidence that its editable baseline still represents the inspected CAD.
/// This helper has no Autodesk dependency and is exercised by a console harness.
/// </summary>
internal sealed class NetworkSourceBinding
{
    private readonly JsonObject inspected;
    private readonly string revision;
    private readonly string snapshot;
    private readonly double tolerance;

    public NetworkSourceBinding(JsonObject network)
    {
        inspected = (JsonObject)network.DeepClone();
        revision = String(network["drawing"], "revision");
        snapshot = String(network, "source_snapshot_sha256");
        if (snapshot.Length != 64 || snapshot.Any(character => !Uri.IsHexDigit(character)))
            throw new InvalidOperationException("Inspection must retain a valid source snapshot identity.");
        tolerance = network["tolerance_mm"] is JsonNode declared ? Number(declared) : 1;
        if (!double.IsFinite(tolerance) || tolerance <= 0 || tolerance > 25)
            throw new InvalidOperationException("Invalid inspected endpoint tolerance.");
    }

    public void Verify(JsonObject network)
    {
        if (String(network["drawing"], "revision") != revision || String(network, "source_snapshot_sha256") != snapshot)
            Reject("The handoff is not the current inspected snapshot");
        foreach (var key in new[] { "id", "source_units" })
            if (!Same(inspected["drawing"]?[key], network["drawing"]?[key])) Reject($"Source drawing {key} changed");
        if (!Same(inspected["bounds"], network["bounds"]) || (network["tolerance_mm"] is JsonNode declared ? Number(declared) : 1) != tolerance)
            Reject("Routing bounds or endpoint tolerance changed");
        var originals = Records(inspected, "spans");
        var supplied = Records(network, "spans");
        if (supplied.Count != originals.Count || originals.Keys.Any(id => !supplied.ContainsKey(id)))
            Reject("The handoff removed or substituted inspected source spans");
        foreach (var (identifier, original) in originals)
        {
            var span = supplied[identifier];
            foreach (var key in new[] { "cad_handle", "instance_path", "source_instances", "outer_diameter_mm" })
                if (!Same(original[key], span[key])) Reject($"Source identity or diameter changed for {identifier}");
            var baseline = Points(original["points"]);
            var points = Points(span["points"]);
            if (baseline.Length != points.Length) Reject($"Original route point count changed for {identifier}");
            for (var index = 0; index < baseline.Length; index++)
            {
                if (Distance(baseline[index], points[index]) <= 1e-7) continue;
                var endpoint = index == 0 ? "from_node" : index == baseline.Length - 1 ? "to_node" : null;
                if (endpoint is null || Distance(baseline[index], points[index]) > tolerance + 1e-9
                    || !ConfirmedEndpoint(network, String(original, endpoint), String(span, endpoint), points[index]))
                    Reject($"Original CAD geometry changed for {identifier}, point {index}");
            }
        }
        var oldObstacles = Records(inspected, "obstacles");
        var newObstacles = Records(network, "obstacles");
        foreach (var (identifier, obstacle) in oldObstacles)
        {
            if (!newObstacles.TryGetValue(identifier, out var current)) Reject($"Inspected obstacle {identifier} was removed");
            foreach (var key in new[] { "min", "max", "capsules" })
                if (!Same(obstacle[key], current![key])) Reject($"Inspected obstacle geometry changed for {identifier}");
        }
    }

    private bool ConfirmedEndpoint(JsonObject network, string originalNode, string currentNode, double[] point)
    {
        if (network["connection_decisions"] is not JsonArray decisions) return false;
        var groups = inspected["unresolved_connections"] as JsonArray ?? [];
        foreach (var decision in decisions.OfType<JsonObject>())
        {
            if (String(decision, "node_id") != currentNode || decision["members"] is not JsonArray members) continue;
            var ids = members.OfType<JsonValue>().Select(value => value.GetValue<string>()).ToArray();
            if (ids.Length < 2 || ids.Distinct(StringComparer.Ordinal).Count() != ids.Length || !ids.Contains(originalNode)) continue;
            var group = groups.OfType<JsonObject>().FirstOrDefault(item => String(item, "id") == String(decision, "connection_id"));
            if (group?["endpoint_ids"] is not JsonArray endpoints) continue;
            var allowed = endpoints.OfType<JsonValue>().Select(value => value.GetValue<string>()).ToHashSet(StringComparer.Ordinal);
            if (ids.Any(id => !allowed.Contains(id))) continue;
            var originalNodes = Records(inspected, "nodes");
            var anchor = Point(decision["position"]);
            if (Distance(anchor, point) > 1e-7 || ids.Any(id => !originalNodes.TryGetValue(id, out var node) || Distance(Point(node["position"]), anchor) > tolerance + 1e-9)) continue;
            return true;
        }
        return false;
    }

    private static Dictionary<string, JsonObject> Records(JsonObject network, string field)
    {
        if (network[field] is not JsonArray array) throw new InvalidOperationException($"Missing {field} in inspected handoff.");
        var records = new Dictionary<string, JsonObject>(StringComparer.Ordinal);
        foreach (var record in array)
            if (record is not JsonObject value || !records.TryAdd(String(value, "id"), value))
                throw new InvalidOperationException($"Invalid or repeated {field} identity.");
        return records;
    }

    private static double[][] Points(JsonNode? value) => value is JsonArray array && array.Count >= 2
        ? array.Select(Point).ToArray() : throw new InvalidOperationException("Original span points are missing.");
    private static double[] Point(JsonNode? value)
    {
        if (value is not JsonArray array || array.Count != 3) throw new InvalidOperationException("Expected a finite global coordinate.");
        var point = array.Select(item => item is null ? double.NaN : Number(item)).ToArray();
        if (point.Any(number => !double.IsFinite(number))) throw new InvalidOperationException("Expected a finite global coordinate.");
        return point;
    }
    private static double Distance(double[] a, double[] b) => Math.Sqrt(a.Zip(b, (x, y) => (x - y) * (x - y)).Sum());
    private static double Number(JsonNode value) => value.Deserialize<double>();
    private static bool Same(JsonNode? a, JsonNode? b) => JsonNode.DeepEquals(a, b);
    private static string String(JsonNode? value, string key) => value?[key]?.GetValue<string>() ?? "";
    private static void Reject(string reason) => throw new InvalidOperationException($"{reason}. Reinspect the drawing; editable JSON cannot replace inspected source geometry.");
}
