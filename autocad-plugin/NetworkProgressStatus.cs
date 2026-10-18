using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Maritime.AutoCAD;

/// <summary>Display measured server counters; absent data never becomes a percentage.</summary>
internal static class NetworkProgressStatus
{
    public static string Format(JsonObject job, double localElapsedSeconds)
    {
        var state = job["status"]?.GetValue<string>() ?? "waiting";
        var lines = new List<string> { $"{state}: {Math.Max(0, localElapsedSeconds):0.0} s elapsed locally." };
        if (job["progress"] is not JsonObject progress)
        {
            lines.Add("Waiting for measured neighborhood and evaluation updates from the local engine.");
            return string.Join(Environment.NewLine, lines);
        }
        if (progress["phase"] is JsonValue phase) lines.Add("Phase: " + phase.GetValue<string>().Replace('_', ' '));
        var selected = Counter(progress, "selected_span_count");
        if (selected is not null) lines.Add($"Selected spans: {selected}; complete supplied environment remains in validation.");
        var completed = Counter(progress, "completed_neighborhoods");
        var total = Counter(progress, "total_neighborhoods");
        if (completed is not null) lines.Add($"Neighborhoods completed: {completed}" + (total is not null ? $" / {total}." : "."));
        var evaluations = Counter(progress, "evaluations");
        var limit = Counter(progress, "max_evaluations");
        if (evaluations is not null) lines.Add($"Candidates evaluated: {evaluations}" + (limit is not null ? $" / {limit}." : "."));
        var searches = new[] { ("local_solves", "Local solves"), ("expanded_states", "expanded states"), ("grid_nodes_peak", "peak grid nodes") }
            .Select(pair => (Label: pair.Item2, Value: Counter(progress, pair.Item1))).Where(pair => pair.Value is not null)
            .Select(pair => $"{pair.Label}: {pair.Value}");
        var searchText = string.Join("; ", searches);
        if (searchText.Length > 0) lines.Add(searchText + ".");
        if (progress["active_span_ids"] is JsonArray ids && ids.Count > 0)
        {
            var active = ids.Take(5).Select(value => value?.GetValue<string>() ?? "unknown");
            lines.Add("Active spans: " + string.Join(", ", active) + (ids.Count > 5 ? $" (+{ids.Count - 5} more)." : "."));
        }
        return string.Join(Environment.NewLine, lines);
    }

    private static string? Counter(JsonObject progress, string key)
    {
        try
        {
            if (progress[key] is null) return null;
            var value = progress[key]!.Deserialize<double>();
            return double.IsFinite(value) && value >= 0 && Math.Truncate(value) == value ? value.ToString("0", CultureInfo.InvariantCulture) : null;
        }
        catch (JsonException) { return null; }
    }
}
