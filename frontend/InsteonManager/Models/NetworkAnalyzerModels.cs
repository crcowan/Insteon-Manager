using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Text.Json.Serialization;
using System.Windows.Media;

namespace InsteonManager.Models;

public class NetworkIssueModel : INotifyPropertyChanged
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = string.Empty;

    [JsonPropertyName("issue_type")]
    public string IssueType { get; set; } = string.Empty;

    [JsonPropertyName("severity")]
    public string Severity { get; set; } = "warning";

    [JsonPropertyName("owner_address")]
    public string OwnerAddress { get; set; } = string.Empty;

    [JsonPropertyName("owner_name")]
    public string OwnerName { get; set; } = string.Empty;

    [JsonPropertyName("mem_addr")]
    public string? MemAddr { get; set; }

    [JsonPropertyName("target_address")]
    public string TargetAddress { get; set; } = string.Empty;

    [JsonPropertyName("target_name")]
    public string TargetName { get; set; } = string.Empty;

    [JsonPropertyName("group")]
    public int Group { get; set; }

    [JsonPropertyName("role")]
    public string Role { get; set; } = string.Empty;

    [JsonPropertyName("controller_address")]
    public string? ExplicitControllerAddress { get; set; }

    [JsonPropertyName("responder_address")]
    public string? ExplicitResponderAddress { get; set; }

    public string ControllerAddress => !string.IsNullOrEmpty(ExplicitControllerAddress)
        ? ExplicitControllerAddress
        : (Role.Equals("Responder", StringComparison.OrdinalIgnoreCase) ? TargetAddress : OwnerAddress);

    public string ResponderAddress => !string.IsNullOrEmpty(ExplicitResponderAddress)
        ? ExplicitResponderAddress
        : (Role.Equals("Responder", StringComparison.OrdinalIgnoreCase) ? OwnerAddress : TargetAddress);

    public string ControllerName => Role.Equals("Responder", StringComparison.OrdinalIgnoreCase) ? TargetName : OwnerName;
    public string ResponderName => Role.Equals("Responder", StringComparison.OrdinalIgnoreCase) ? OwnerName : TargetName;

    [JsonPropertyName("summary")]
    public string Summary { get; set; } = string.Empty;

    [JsonPropertyName("explanation")]
    public string Explanation { get; set; } = string.Empty;

    [JsonPropertyName("recommended_action")]
    public string RecommendedAction { get; set; } = "create_reciprocal";

    [JsonPropertyName("fix_label")]
    public string FixLabel { get; set; } = "⚡ Fix Link";

    [JsonPropertyName("secondary_action")]
    public string? SecondaryAction { get; set; } = "delete_record";

    [JsonPropertyName("secondary_label")]
    public string? SecondaryLabel { get; set; } = "🗑️ Delete Broken Slot";

    [JsonPropertyName("can_auto_fix")]
    public bool CanAutoFix { get; set; } = true;

    [JsonPropertyName("data1")]
    public int Data1 { get; set; } = 255;

    [JsonPropertyName("data2")]
    public int Data2 { get; set; } = 28;

    [JsonPropertyName("data3")]
    public int Data3 { get; set; } = 1;

    // Helper UI Properties
    public string SeverityBadge => Severity.ToLowerInvariant() switch
    {
        "error" => "⛔ Error",
        "warning" => "⚠️ Warning",
        _ => "ℹ️ Notice"
    };

    public string SeverityBadgeBackground => Severity.ToLowerInvariant() switch
    {
        "error" => "#441D24",
        "warning" => "#3B2A20",
        _ => "#1E2A3A"
    };

    public string SeverityBadgeForeground => Severity.ToLowerInvariant() switch
    {
        "error" => "#F38BA8",
        "warning" => "#F9E2AF",
        _ => "#89B4FA"
    };

    public string IssueTypeBadge => IssueType switch
    {
        "half_link" => "Broken Half-Link",
        "loopback" => "Self-Referential Loopback",
        "missing_default" => "Missing Default Link",
        "ghost" => "Dead Device Ghost",
        "duplicate" => "Redundant Duplicate",
        "group_zero" => "Corrupted Group 0",
        "incomplete_read" => "Incomplete Hardware Read",
        "diagnostic_group" => "Obsolete Diagnostic Group",
        "empty_scene" => "Empty Zombie Scene",
        "scene_advisory" => "Scene Advisory",
        _ => "Network Anomaly"
    };

    public string DirectionDisplay => Role == "Controller"
        ? $"{OwnerName} ({OwnerAddress}) ➔ {TargetName} ({TargetAddress})"
        : $"{OwnerName} ({OwnerAddress}) 🠔 {TargetName} ({TargetAddress})";

    public bool HasMemAddr => !string.IsNullOrEmpty(MemAddr);
    public bool HasSecondaryAction => !string.IsNullOrEmpty(SecondaryAction);

    public event PropertyChangedEventHandler? PropertyChanged;
    protected void OnPropertyChanged([CallerMemberName] string? propName = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propName));
}

public class NetworkAnalysisReportModel
{
    [JsonPropertyName("health_score")]
    public int HealthScore { get; set; }

    [JsonPropertyName("total_devices")]
    public int TotalDevices { get; set; }

    [JsonPropertyName("total_active_records")]
    public int TotalActiveRecords { get; set; }

    [JsonPropertyName("total_issues")]
    public int TotalIssues { get; set; }

    [JsonPropertyName("half_links_count")]
    public int HalfLinksCount { get; set; }

    [JsonPropertyName("loopbacks_count")]
    public int LoopbacksCount { get; set; }

    [JsonPropertyName("missing_defaults_count")]
    public int MissingDefaultsCount { get; set; }

    [JsonPropertyName("ghosts_count")]
    public int GhostsCount { get; set; }

    [JsonPropertyName("duplicates_count")]
    public int DuplicatesCount { get; set; }

    [JsonPropertyName("group_zero_count")]
    public int GroupZeroCount { get; set; }

    [JsonPropertyName("issues")]
    public List<NetworkIssueItemModel> Issues { get; set; } = new();
}

// Alias for deserialization
public class NetworkIssueItemModel : NetworkIssueModel { }
