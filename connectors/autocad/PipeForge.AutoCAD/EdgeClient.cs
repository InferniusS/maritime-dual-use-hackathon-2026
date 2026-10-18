using System.Net.Http;
using System.Text;
using System.Text.Json.Nodes;

namespace PipeForge.AutoCAD;

/// <summary>HTTP only: this class must never access the AutoCAD database or Editor.</summary>
internal sealed class EdgeClient : IDisposable
{
    private readonly HttpClient _http;

    public EdgeClient(Uri endpoint)
    {
        if ((endpoint.Scheme != "http" && endpoint.Scheme != "https") ||
            !string.IsNullOrEmpty(endpoint.UserInfo) || !string.IsNullOrEmpty(endpoint.Query) ||
            !string.IsNullOrEmpty(endpoint.Fragment))
            throw new ArgumentException("Use an HTTP(S) server URL without credentials, query or fragment.");
        _http = new HttpClient { BaseAddress = new Uri(endpoint.AbsoluteUri.TrimEnd('/') + "/"), Timeout = TimeSpan.FromSeconds(45) };
    }

    public async Task<JsonObject> JsonAsync(HttpMethod method, string path, JsonObject? body, CancellationToken token)
    {
        using var request = new HttpRequestMessage(method, path);
        if (body != null) request.Content = new StringContent(body.ToJsonString(), Encoding.UTF8, "application/json");
        using var response = await _http.SendAsync(request, token).ConfigureAwait(false);
        var text = await response.Content.ReadAsStringAsync(token).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
            throw new HttpRequestException($"Edge API {(int)response.StatusCode}: {text[..Math.Min(text.Length, 1200)]}");
        return JsonNode.Parse(text) as JsonObject ?? throw new InvalidDataException("Expected an Edge JSON object.");
    }

    public async Task<byte[]> DownloadAsync(string path, CancellationToken token)
    {
        using var response = await _http.GetAsync(path, token).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadAsByteArrayAsync(token).ConfigureAwait(false);
    }

    public void Dispose() => _http.Dispose();
}
