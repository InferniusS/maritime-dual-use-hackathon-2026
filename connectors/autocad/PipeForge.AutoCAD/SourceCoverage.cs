using System.Text.Json.Nodes;
using Autodesk.AutoCAD.DatabaseServices;

namespace PipeForge.AutoCAD;

/// <summary>Read-only source inventory. Unknown geometry is never classified or discarded.</summary>
internal static class SourceCoverage
{
    private const int MaximumModelSpaceEntities = 10_000;

    public static JsonObject Scan(Database database, Transaction transaction, JsonObject model,
        IReadOnlyDictionary<string, string> objectMap, long generation)
    {
        var activeIds = new HashSet<string>(((JsonArray)model["pipes"]!).Concat((JsonArray)model["obstacles"]!)
            .OfType<JsonObject>().Select(item => item["id"]!.GetValue<string>()), StringComparer.Ordinal);
        var mappedHandles = new HashSet<string>(objectMap.Where(item => activeIds.Contains(item.Key))
            .Select(item => item.Value), StringComparer.OrdinalIgnoreCase);
        var table = (BlockTable)transaction.GetObject(database.BlockTableId, OpenMode.ForRead);
        var space = (BlockTableRecord)transaction.GetObject(table[BlockTableRecord.ModelSpace], OpenMode.ForRead);
        var unresolved = new JsonArray();
        var unresolvedIds = new JsonArray();
        var mappedCount = 0;
        var entityCount = 0;
        foreach (ObjectId id in space)
        {
            if (id.IsErased) continue;
            if (transaction.GetObject(id, OpenMode.ForRead) is not Entity entity) continue;
            if (++entityCount > MaximumModelSpaceEntities)
                throw new InvalidDataException($"ModelSpace contains more than {MaximumModelSpaceEntities} entities. Source coverage was not truncated; use an explicitly scoped drawing before reconnecting.");
            var handle = entity.Handle.ToString();
            if (mappedHandles.Contains(handle)) { mappedCount++; continue; }
            unresolvedIds.Add(handle);
            unresolved.Add(new JsonObject
            {
                ["handle"] = handle, ["entity_type"] = entity.GetType().Name, ["layer"] = entity.Layer,
                ["reason"] = entity is BlockReference
                    ? "Unclassified block/xref; definition dependencies and engineering semantics are not tracked."
                    : "No explicit engineering binding. Geometry, including annotations, remains unclassified and is excluded from the engineering model."
            });
        }
        var staticIds = new JsonArray();
        foreach (var id in activeIds.Where(id => !objectMap.ContainsKey(id)).Order(StringComparer.Ordinal)) staticIds.Add(id);
        return new JsonObject
        {
            ["schema_version"] = "1.0", ["adapter"] = "autocad", ["scope"] = "modelspace",
            ["status"] = unresolved.Count > 0 ? "incomplete" : staticIds.Count > 0 ? "unverified" : "complete",
            ["source_generation"] = generation, ["mapped_entities"] = mappedCount,
            ["unmapped_entities"] = unresolved.Count, ["unresolved_ids"] = unresolvedIds,
            ["unresolved_entities"] = unresolved, ["ignored_entities"] = new JsonArray(),
            ["static_metadata_ids"] = staticIds,
            ["environment"] = "Read-only native ModelSpace inventory. No entities are implicitly ignored; no pipe or obstacle semantics are inferred. Static metadata has no live CAD binding."
        };
    }
}
