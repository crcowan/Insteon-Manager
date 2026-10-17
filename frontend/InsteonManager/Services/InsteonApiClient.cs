using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using InsteonManager.Models;

namespace InsteonManager.Services;

public class InsteonApiClient
{
    private readonly HttpClient _httpClient;
    private string _baseUrl = "http://localhost:8000";

    public InsteonApiClient()
    {
        _httpClient = new HttpClient
        {
            Timeout = TimeSpan.FromMinutes(10)
        };
    }

    public void SetBaseUrl(string baseUrl)
    {
        _baseUrl = baseUrl.TrimEnd('/');
    }

    public string BaseUrl => _baseUrl;

    public async Task<SessionStatusModel?> GetSessionStatusAsync()
    {
        try
        {
            return await _httpClient.GetFromJsonAsync<SessionStatusModel>($"{_baseUrl}/session/status");
        }
        catch
        {
            return null;
        }
    }

    public async Task<(bool Success, string Message)> AcquireSessionAsync(string clientId, int? leaseSeconds = null)
    {
        try
        {
            var payload = new { client_id = clientId, lease_seconds = leaseSeconds };
            var response = await _httpClient.PostAsJsonAsync($"{_baseUrl}/session/acquire", payload);
            var content = await response.Content.ReadAsStringAsync();

            if (response.IsSuccessStatusCode)
            {
                var result = JsonSerializer.Deserialize<GenericApiResponse>(content);
                return (true, result?.Message ?? "Session acquired successfully");
            }

            return (false, $"HTTP {(int)response.StatusCode}: {content}");
        }
        catch (Exception ex)
        {
            return (false, $"Connection error: {ex.Message}");
        }
    }

    public async Task<bool> SendHeartbeatAsync(string clientId)
    {
        try
        {
            var payload = new { client_id = clientId };
            var response = await _httpClient.PostAsJsonAsync($"{_baseUrl}/session/heartbeat", payload);
            return response.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    public async Task<(bool Success, string Message)> ReleaseSessionAsync(string clientId)
    {
        try
        {
            var payload = new { client_id = clientId };
            var response = await _httpClient.PostAsJsonAsync($"{_baseUrl}/session/release", payload);
            var content = await response.Content.ReadAsStringAsync();

            if (response.IsSuccessStatusCode)
            {
                var result = JsonSerializer.Deserialize<GenericApiResponse>(content);
                return (true, result?.Message ?? "Session released");
            }

            return (false, $"HTTP {(int)response.StatusCode}: {content}");
        }
        catch (Exception ex)
        {
            return (false, $"Connection error: {ex.Message}");
        }
    }

    public async Task<List<AldbRecordModel>> GetPlmAldbAsync(bool forceRefresh = false)
    {
        try
        {
            var url = $"{_baseUrl}/insteon/network/aldb?force_refresh={forceRefresh.ToString().ToLower()}";
            var records = await _httpClient.GetFromJsonAsync<List<AldbRecordModel>>(url);
            if (records != null && records.Count > 0)
            {
                return records;
            }
        }
        catch
        {
            // Fall through to PLM endpoint if network endpoint is not supported
        }

        try
        {
            var fallbackUrl = $"{_baseUrl}/insteon/plm/aldb?force_refresh={forceRefresh.ToString().ToLower()}";
            var records = await _httpClient.GetFromJsonAsync<List<AldbRecordModel>>(fallbackUrl);
            return records ?? new List<AldbRecordModel>();
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException($"Failed to query ALDB: {ex.Message}", ex);
        }
    }

    public async Task<List<DeviceSummaryModel>> GetDevicesAsync()
    {
        try
        {
            var devices = await _httpClient.GetFromJsonAsync<List<DeviceSummaryModel>>($"{_baseUrl}/insteon/devices");
            return devices ?? new List<DeviceSummaryModel>();
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException($"Failed to query devices: {ex.Message}", ex);
        }
    }

    public async Task<bool> RestoreOpenhabAsync()
    {
        try
        {
            var response = await _httpClient.PostAsync($"{_baseUrl}/openhab/enable", null);
            return response.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    public async Task<List<SceneModel>> GetScenesAsync()
    {
        try
        {
            var scenes = await _httpClient.GetFromJsonAsync<List<SceneModel>>($"{_baseUrl}/insteon/scenes");
            return scenes ?? new List<SceneModel>();
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException($"Failed to query scenes: {ex.Message}", ex);
        }
    }

    public async Task<SceneMemberModel?> UpdateSceneMemberPresetAsync(int group, string address, int? onLevel, int? rampRate)
    {
        try
        {
            var payload = new { on_level = onLevel, ramp_rate = rampRate };
            var response = await _httpClient.PatchAsJsonAsync($"{_baseUrl}/insteon/scenes/{group}/members/{address}", payload);
            if (response.IsSuccessStatusCode)
            {
                return await response.Content.ReadFromJsonAsync<SceneMemberModel>();
            }
            var err = await response.Content.ReadAsStringAsync();
            throw new InvalidOperationException($"HTTP {(int)response.StatusCode}: {err}");
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException($"Failed to update preset for {address} in Scene {group}: {ex.Message}", ex);
        }
    }

    public async Task<(bool Success, string Message)> TriggerSceneAsync(int group, string action = "on")
    {
        try
        {
            var payload = new { action };
            var response = await _httpClient.PostAsJsonAsync($"{_baseUrl}/insteon/scenes/{group}/trigger", payload);
            var content = await response.Content.ReadAsStringAsync();
            if (response.IsSuccessStatusCode)
            {
                var res = JsonSerializer.Deserialize<GenericApiResponse>(content);
                return (res?.Success ?? true, res?.Message ?? $"Scene {group} triggered with action '{action}'");
            }
            return (false, $"HTTP {(int)response.StatusCode}: {content}");
        }
        catch (Exception ex)
        {
            return (false, $"Trigger error: {ex.Message}");
        }
    }

    public async Task<(bool Success, string Message)> CreateReciprocalLinkAsync(ReciprocalLinkRequestModel req)
    {
        try
        {
            var response = await _httpClient.PostAsJsonAsync($"{_baseUrl}/insteon/links", req);
            var content = await response.Content.ReadAsStringAsync();
            if (response.IsSuccessStatusCode)
            {
                var res = JsonSerializer.Deserialize<GenericApiResponse>(content);
                return (res?.Success ?? true, res?.Message ?? "Link created successfully");
            }
            return (false, $"HTTP {(int)response.StatusCode}: {content}");
        }
        catch (Exception ex)
        {
            return (false, $"Link creation error: {ex.Message}");
        }
    }

    public async Task<(bool Success, string Message)> DeleteReciprocalLinkAsync(string controllerAddr, string responderAddr, int group)
    {
        try
        {
            var url = $"{_baseUrl}/insteon/links?controller_address={Uri.EscapeDataString(controllerAddr)}&responder_address={Uri.EscapeDataString(responderAddr)}&group={group}";
            var response = await _httpClient.DeleteAsync(url);
            var content = await response.Content.ReadAsStringAsync();
            if (response.IsSuccessStatusCode)
            {
                var res = JsonSerializer.Deserialize<GenericApiResponse>(content);
                return (res?.Success ?? true, res?.Message ?? "Link deleted successfully");
            }
            return (false, $"HTTP {(int)response.StatusCode}: {content}");
        }
        catch (Exception ex)
        {
            return (false, $"Link deletion error: {ex.Message}");
        }
    }

    public async Task<(bool Success, string Message)> DeleteDeviceAldbRecordAsync(string address, string memAddr)
    {
        try
        {
            var url = $"{_baseUrl}/insteon/devices/{Uri.EscapeDataString(address)}/aldb/{Uri.EscapeDataString(memAddr)}";
            var response = await _httpClient.DeleteAsync(url);
            var content = await response.Content.ReadAsStringAsync();
            if (response.IsSuccessStatusCode)
            {
                var res = JsonSerializer.Deserialize<GenericApiResponse>(content);
                return (res?.Success ?? true, res?.Message ?? $"Slot {memAddr} deleted successfully");
            }
            return (false, $"HTTP {(int)response.StatusCode}: {content}");
        }
        catch (Exception ex)
        {
            return (false, $"Slot deletion error: {ex.Message}");
        }
    }

    public async Task<(bool Success, string Message)> ReplaceDeviceAsync(DeviceReplaceRequestModel req)
    {
        try
        {
            var response = await _httpClient.PostAsJsonAsync($"{_baseUrl}/insteon/devices/replace", req);
            var content = await response.Content.ReadAsStringAsync();
            if (response.IsSuccessStatusCode)
            {
                var res = JsonSerializer.Deserialize<GenericApiResponse>(content);
                return (res?.Success ?? true, res?.Message ?? "Device replaced successfully");
            }
            return (false, $"HTTP {(int)response.StatusCode}: {content}");
        }
        catch (Exception ex)
        {
            return (false, $"Device replacement error: {ex.Message}");
        }
    }

    public async Task<(bool Success, string Message)> PurgeGhostLinksAsync(string targetAddress)
    {
        try
        {
            var response = await _httpClient.PostAsync($"{_baseUrl}/insteon/purge-ghosts/{Uri.EscapeDataString(targetAddress)}", null);
            var content = await response.Content.ReadAsStringAsync();
            if (response.IsSuccessStatusCode)
            {
                var res = JsonSerializer.Deserialize<GenericApiResponse>(content);
                return (res?.Success ?? true, res?.Message ?? "Ghost links purged successfully");
            }
            return (false, $"HTTP {(int)response.StatusCode}: {content}");
        }
        catch (Exception ex)
        {
            return (false, $"Ghost purge error: {ex.Message}");
        }
    }

    public async Task<(bool Success, string Message)> ControlDeviceAsync(string address, string action, int level = 255, int? rampRate = null, int button = 1)
    {
        try
        {
            var payload = new
            {
                action = action.ToLower().Trim(),
                level = level,
                ramp_rate = rampRate,
                button = button
            };
            var response = await _httpClient.PostAsJsonAsync($"{_baseUrl}/insteon/devices/{Uri.EscapeDataString(address)}/control", payload);
            var content = await response.Content.ReadAsStringAsync();
            if (response.IsSuccessStatusCode)
            {
                var res = JsonSerializer.Deserialize<GenericApiResponse>(content);
                return (res?.Success ?? true, res?.Message ?? $"Device {address} {action} sent");
            }
            return (false, $"HTTP {(int)response.StatusCode}: {content}");
        }
        catch (Exception ex)
        {
            return (false, $"Device control error: {ex.Message}");
        }
    }

    public async Task<DefaultLinkAuditResponseModel?> AuditDefaultLinksAsync()
    {
        try
        {
            return await _httpClient.GetFromJsonAsync<DefaultLinkAuditResponseModel>($"{_baseUrl}/insteon/audit/default-links");
        }
        catch
        {
            return null;
        }
    }

    public async Task<(bool Success, string Message, string? Address)> AddDeviceAsync(DeviceAddRequestModel req)
    {
        try
        {
            var response = await _httpClient.PostAsJsonAsync($"{_baseUrl}/insteon/devices/add", req);
            var content = await response.Content.ReadAsStringAsync();
            if (response.IsSuccessStatusCode)
            {
                var res = JsonSerializer.Deserialize<GenericApiResponse>(content);
                return (res?.Success ?? true, res?.Message ?? $"Device {req.Address} added successfully", req.Address);
            }
            return (false, $"HTTP {(int)response.StatusCode}: {content}", null);
        }
        catch (Exception ex)
        {
            return (false, $"Error adding device: {ex.Message}", null);
        }
    }

    public async Task<NetworkAnalysisReportModel?> AnalyzeNetworkAsync()
    {
        try
        {
            return await _httpClient.GetFromJsonAsync<NetworkAnalysisReportModel>($"{_baseUrl}/insteon/network/analyze");
        }
        catch
        {
            return null;
        }
    }
}



