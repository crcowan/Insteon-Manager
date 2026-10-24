using System.Text.Json.Serialization;

namespace InsteonManager.Models;

public class SessionStatusModel
{
    [JsonPropertyName("is_active")]
    public bool IsActive { get; set; }

    [JsonPropertyName("locked_by")]
    public string? LockedBy { get; set; }

    [JsonPropertyName("lease_expires_in_seconds")]
    public int? LeaseExpiresInSeconds { get; set; }

    [JsonPropertyName("openhab_thing_enabled")]
    public bool OpenhabThingEnabled { get; set; }

    [JsonPropertyName("pyinsteon_connected")]
    public bool PyinsteonConnected { get; set; }

    [JsonPropertyName("last_heartbeat")]
    public string? LastHeartbeat { get; set; }
}

public class DeviceSummaryModel
{
    [JsonPropertyName("address")]
    public string Address { get; set; } = string.Empty;

    [JsonPropertyName("cat")]
    public int Cat { get; set; }

    [JsonPropertyName("subcat")]
    public int Subcat { get; set; }

    [JsonPropertyName("firmware")]
    public int Firmware { get; set; }

    [JsonPropertyName("description")]
    public string Description { get; set; } = string.Empty;

    [JsonPropertyName("is_battery")]
    public bool IsBattery { get; set; }

    [JsonPropertyName("aldb_record_count")]
    public int AldbRecordCount { get; set; }

    [JsonPropertyName("model_name")]
    public string ModelName { get; set; } = string.Empty;

    [JsonPropertyName("engine_version")]
    public string EngineVersion { get; set; } = string.Empty;

    [JsonPropertyName("read_strategy")]
    public string ReadStrategy { get; set; } = string.Empty;

    [JsonPropertyName("write_strategy")]
    public string WriteStrategy { get; set; } = string.Empty;

    [JsonPropertyName("is_keypad")]
    public bool IsKeypad { get; set; }

    [JsonPropertyName("button_count")]
    public int ButtonCount { get; set; } = 1;

    [JsonPropertyName("is_dimmable")]
    public bool IsDimmable { get; set; } = true;
}

public class GenericApiResponse
{
    [JsonPropertyName("success")]
    public bool Success { get; set; }

    [JsonPropertyName("message")]
    public string Message { get; set; } = string.Empty;
}
