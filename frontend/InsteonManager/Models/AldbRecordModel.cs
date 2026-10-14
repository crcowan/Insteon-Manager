using System.Text.Json.Serialization;

namespace InsteonManager.Models;

public class AldbRecordModel
{
    [JsonPropertyName("mem_addr")]
    public string MemAddr { get; set; } = string.Empty;

    [JsonPropertyName("source_address")]
    public string? SourceAddress { get; set; }

    [JsonPropertyName("target_address")]
    public string TargetAddress { get; set; } = string.Empty;

    [JsonPropertyName("group")]
    public int Group { get; set; }

    [JsonPropertyName("is_controller")]
    public bool IsController { get; set; }

    [JsonPropertyName("is_responder")]
    public bool IsResponder { get; set; }

    [JsonPropertyName("in_use")]
    public bool InUse { get; set; }

    [JsonPropertyName("high_water_mark")]
    public bool HighWaterMark { get; set; }

    [JsonPropertyName("data1")]
    public int Data1 { get; set; }

    [JsonPropertyName("data2")]
    public int Data2 { get; set; }

    [JsonPropertyName("data3")]
    public int Data3 { get; set; }

    [JsonPropertyName("raw_mode")]
    public string RawMode { get; set; } = string.Empty;

    public string ModeDescription => IsController ? "Controller" : (IsResponder ? "Responder" : "Unknown");
    public string StatusDescription => InUse ? "Active" : "Deleted / Inactive";
}
