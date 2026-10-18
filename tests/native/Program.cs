using Maritime.AutoCAD;
using System.Text.Json.Nodes;

// Actual production helper, compiled without Autodesk or Windows UI dependencies.
var original = JsonNode.Parse("""
{
  "drawing":{"id":"synthetic-fingerprint","revision":"pipeforge-session:test:4","source_units":"mm"},
  "source_snapshot_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "bounds":{"min":[-100,-100,-100],"max":[3000,1000,1000]},"tolerance_mm":1,
  "nodes":[{"id":"a","position":[0,0,0]},{"id":"b","position":[1000,0,0]},
           {"id":"c","position":[1000.5,0,0]},{"id":"d","position":[2000,0,0]}],
  "spans":[
    {"id":"ab","from_node":"a","to_node":"b","cad_handle":"A1","instance_path":[],"source_instances":[{"source_handle":"A1","instance_path":[]}],"outer_diameter_mm":60,"points":[[0,0,0],[1000,0,0]]},
    {"id":"cd","from_node":"c","to_node":"d","cad_handle":"B2","instance_path":[],"source_instances":[{"source_handle":"B2","instance_path":[]}],"outer_diameter_mm":60,"points":[[1000.5,0,0],[2000,0,0]]}
  ],
  "obstacles":[{"id":"equipment","min":[200,300,0],"max":[500,600,700]}],
  "unresolved_connections":[{"id":"near-b-c","endpoint_ids":["b","c"]}]
}
""")!.AsObject();
var binding = new NetworkSourceBinding(original);
var checks = 0;
void Accept(string name, Action<JsonObject>? change = null)
{
    var network = (JsonObject)original.DeepClone();
    change?.Invoke(network);
    binding.Verify(network);
    checks++;
    Console.WriteLine($"PASS {name}");
}
void Reject(string name, Action<JsonObject> change)
{
    var network = (JsonObject)original.DeepClone();
    change(network);
    try { binding.Verify(network); }
    catch (InvalidOperationException) { checks++; Console.WriteLine($"PASS {name}"); return; }
    throw new InvalidOperationException($"Unsafe handoff accepted: {name}");
}
Accept("unchanged extraction");
Accept("equivalent JSON number formatting", n =>
{
    n["spans"]![0]!["outer_diameter_mm"] = 60.0;
    n["bounds"]!["min"]![0] = -100.0;
});
Accept("engineering metadata can be reviewed", n =>
{
    n["spans"]![0]!["nominal_diameter_mm"] = 50;
    n["spans"]![0]!["system_id"] = "reviewed-cooling";
    n["spans"]![0]!["fitting_catalog"] = new JsonObject { ["status"] = "provisional", ["radius_mm"] = 200 };
    n["coordinate_system"] = new JsonObject { ["frame"] = "WCS" };
});
Accept("explicit in-tolerance topology confirmation", n =>
{
    n["spans"]![0]!["to_node"] = "confirmed-junction";
    n["spans"]![0]!["points"]![1]![0] = 1000.25;
    n["spans"]![1]!["from_node"] = "confirmed-junction";
    n["spans"]![1]!["points"]![0]![0] = 1000.25;
    n["connection_decisions"] = JsonNode.Parse("""[{"connection_id":"near-b-c","node_id":"confirmed-junction","members":["b","c"],"position":[1000.25,0,0]}]""");
});
Accept("additional conservative obstacle", n => n["obstacles"]!.AsArray().Add(new JsonObject { ["id"] = "reviewed-extra", ["min"] = new JsonArray(0, 800, 0), ["max"] = new JsonArray(100, 900, 100) }));
Reject("baseline geometry altered with copied hash and revision", n => n["spans"]![0]!["points"]![1]![1] = 400);
Reject("point inserted", n => n["spans"]![0]!["points"]!.AsArray().Insert(1, new JsonArray(500, 200, 0)));
Reject("unconfirmed sub-tolerance endpoint movement", n => n["spans"]![0]!["points"]![1]![0] = 1000.25);
Reject("diameter changed", n => n["spans"]![0]!["outer_diameter_mm"] = 40);
Reject("source handle replaced", n => n["spans"]![0]!["cad_handle"] = "C3");
Reject("source instance replaced", n => n["spans"]![0]!["source_instances"]![0]!["source_handle"] = "C3");
Reject("source span removed", n => n["spans"]!.AsArray().RemoveAt(1));
Reject("obstacle omitted", n => n["obstacles"]!.AsArray().Clear());
Reject("obstacle resized", n => n["obstacles"]![0]!["max"]![0] = 400);
Reject("routing permission enlarged", n => n["bounds"]!["max"]![0] = 9000);
Reject("endpoint tolerance enlarged", n => n["tolerance_mm"] = 25);
Reject("snapshot identity replaced", n => n["source_snapshot_sha256"] = new string('b', 64));
Reject("source drawing units replaced", n => n["drawing"]!["source_units"] = "in");
Reject("revision replaced", n => n["drawing"]!["revision"] = "pipeforge-session:test:5");
// Mutating the caller-owned object after enrollment must not mutate evidence.
original["spans"]![0]!["points"]![1]![1] = 100;
try { binding.Verify(original); throw new InvalidOperationException("Binding did not copy extraction evidence"); }
catch (InvalidOperationException exception) when (exception.Message.Contains("Reinspect")) { checks++; Console.WriteLine("PASS extraction evidence is immutable"); }
Console.WriteLine($"{checks} native source-binding checks passed; Autodesk runtime was not executed.");
