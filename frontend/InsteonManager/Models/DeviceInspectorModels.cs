using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace InsteonManager.Models;

public class SelectableDeviceItem : INotifyPropertyChanged
{
    private bool _isSelected;
    private string _address = string.Empty;
    private string _friendlyName = string.Empty;
    private string _deviceType = string.Empty;
    private int _recordCount;
    private bool _isBattery;

    public string Address
    {
        get => _address;
        set => SetProperty(ref _address, value);
    }

    public string FriendlyName
    {
        get => _friendlyName;
        set
        {
            if (SetProperty(ref _friendlyName, value))
            {
                OnPropertyChanged(nameof(DisplayTitle));
            }
        }
    }

    public string DeviceType
    {
        get => _deviceType;
        set => SetProperty(ref _deviceType, value);
    }

    public int RecordCount
    {
        get => _recordCount;
        set => SetProperty(ref _recordCount, value);
    }

    public bool IsBattery
    {
        get => _isBattery;
        set => SetProperty(ref _isBattery, value);
    }

    public bool IsSelected
    {
        get => _isSelected;
        set => SetProperty(ref _isSelected, value);
    }

    public string DisplayTitle => string.IsNullOrWhiteSpace(FriendlyName) || FriendlyName.Equals($"Device {Address}", System.StringComparison.OrdinalIgnoreCase)
        ? Address
        : $"{FriendlyName} ({Address})";

    public string CategoryBadge => IsBattery ? "Battery Sensor" : (string.IsNullOrWhiteSpace(DeviceType) ? "Line Device" : DeviceType);

    public event PropertyChangedEventHandler? PropertyChanged;

    protected bool SetProperty<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (Equals(field, value)) return false;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
        return true;
    }

    protected void OnPropertyChanged([CallerMemberName] string? propertyName = null)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}

public class OrganizedDeviceAldb : INotifyPropertyChanged
{
    private string _address = string.Empty;
    private string _friendlyName = string.Empty;
    private string _deviceType = string.Empty;
    private bool _isModem;
    private bool _isBattery;
    private bool _hasMissingDefaultLink;
    private string _defaultLinkIssue = string.Empty;
    private string _defaultLinkRecommendation = string.Empty;

    public string Address
    {
        get => _address;
        set => SetProperty(ref _address, value);
    }

    public string FriendlyName
    {
        get => _friendlyName;
        set
        {
            if (SetProperty(ref _friendlyName, value))
            {
                OnPropertyChanged(nameof(DisplayTitle));
            }
        }
    }

    public string DeviceType
    {
        get => _deviceType;
        set => SetProperty(ref _deviceType, value);
    }

    public bool IsModem
    {
        get => _isModem;
        set => SetProperty(ref _isModem, value);
    }

    public bool IsBattery
    {
        get => _isBattery;
        set => SetProperty(ref _isBattery, value);
    }

    public bool HasMissingDefaultLink
    {
        get => _hasMissingDefaultLink;
        set => SetProperty(ref _hasMissingDefaultLink, value);
    }

    public string DefaultLinkIssue
    {
        get => _defaultLinkIssue;
        set => SetProperty(ref _defaultLinkIssue, value);
    }

    public string DefaultLinkRecommendation
    {
        get => _defaultLinkRecommendation;
        set => SetProperty(ref _defaultLinkRecommendation, value);
    }

    public int TotalRecords => ControllerRecords.Count + ResponderRecords.Count + OtherRecords.Count;
    public int ControllerCount => ControllerRecords.Count;
    public int ResponderCount => ResponderRecords.Count;

    public ObservableCollection<OrganizedAldbEntry> ControllerRecords { get; set; } = new();
    public ObservableCollection<OrganizedAldbEntry> ResponderRecords { get; set; } = new();
    public ObservableCollection<OrganizedAldbEntry> OtherRecords { get; set; } = new();

    public string DisplayTitle => string.IsNullOrWhiteSpace(FriendlyName) || FriendlyName.Equals($"Device {Address}", System.StringComparison.OrdinalIgnoreCase)
        ? Address
        : $"{FriendlyName} ({Address})";

    public event PropertyChangedEventHandler? PropertyChanged;

    protected bool SetProperty<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (Equals(field, value)) return false;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
        return true;
    }

    protected void OnPropertyChanged([CallerMemberName] string? propertyName = null)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}

public class MissingDefaultLinkItemModel
{
    [System.Text.Json.Serialization.JsonPropertyName("device_address")]
    public string DeviceAddress { get; set; } = string.Empty;

    [System.Text.Json.Serialization.JsonPropertyName("device_name")]
    public string DeviceName { get; set; } = string.Empty;

    [System.Text.Json.Serialization.JsonPropertyName("device_type")]
    public string DeviceType { get; set; } = string.Empty;

    [System.Text.Json.Serialization.JsonPropertyName("missing_device_to_plm")]
    public bool MissingDeviceToPlm { get; set; }

    [System.Text.Json.Serialization.JsonPropertyName("missing_plm_to_device")]
    public bool MissingPlmToDevice { get; set; }

    [System.Text.Json.Serialization.JsonPropertyName("issue_summary")]
    public string IssueSummary { get; set; } = string.Empty;

    [System.Text.Json.Serialization.JsonPropertyName("recommendation")]
    public string Recommendation { get; set; } = string.Empty;

    public string DirectionBadge => (MissingDeviceToPlm && MissingPlmToDevice) 
        ? "🚨 Both Directions Missing" 
        : (MissingDeviceToPlm ? "⚠️ Missing Dev ➔ PLM (Status Updates)" : "⚠️ Missing PLM ➔ Dev (Control)");

    public string DisplayTitle => $"{DeviceName} ({DeviceAddress})";
}

public class DefaultLinkAuditResponseModel
{
    [System.Text.Json.Serialization.JsonPropertyName("plm_address")]
    public string PlmAddress { get; set; } = string.Empty;

    [System.Text.Json.Serialization.JsonPropertyName("total_devices")]
    public int TotalDevices { get; set; }

    [System.Text.Json.Serialization.JsonPropertyName("healthy_count")]
    public int HealthyCount { get; set; }

    [System.Text.Json.Serialization.JsonPropertyName("missing_count")]
    public int MissingCount { get; set; }

    [System.Text.Json.Serialization.JsonPropertyName("items")]
    public List<MissingDefaultLinkItemModel> Items { get; set; } = new();
}

public class DeviceAddRequestModel
{
    [System.Text.Json.Serialization.JsonPropertyName("address")]
    public string Address { get; set; } = string.Empty;

    [System.Text.Json.Serialization.JsonPropertyName("description")]
    public string Description { get; set; } = "Insteon Device";

    [System.Text.Json.Serialization.JsonPropertyName("cat")]
    public int Cat { get; set; } = 1;

    [System.Text.Json.Serialization.JsonPropertyName("subcat")]
    public int Subcat { get; set; } = 0;

    [System.Text.Json.Serialization.JsonPropertyName("firmware")]
    public int Firmware { get; set; } = 0;

    [System.Text.Json.Serialization.JsonPropertyName("create_default_links")]
    public bool CreateDefaultLinks { get; set; } = true;
}

public class DeviceCategoryOption
{
    public string Name { get; set; } = string.Empty;
    public int Cat { get; set; }
    public int Subcat { get; set; }
    public string Description { get; set; } = string.Empty;
    public override string ToString() => Name;
}

public class OrganizedAldbEntry
{
    public string OwnerAddress { get; set; } = string.Empty;
    public string OwnerFriendlyName { get; set; } = string.Empty;
    public string MemAddr { get; set; } = string.Empty;
    public string TargetAddress { get; set; } = string.Empty;
    public string TargetFriendlyName { get; set; } = string.Empty;
    public int Group { get; set; }
    public string Role { get; set; } = string.Empty;
    public bool IsController { get; set; }
    public bool IsResponder { get; set; }
    public bool InUse { get; set; }
    public bool HighWaterMark { get; set; }
    public int Data1 { get; set; }
    public int Data2 { get; set; }
    public int Data3 { get; set; }
    public string DecodedSummary { get; set; } = string.Empty;
    public string StatusBadge { get; set; } = "Active";

    public bool IsDuplicate { get; set; }
    public bool IsGroupZero { get; set; }
    public bool HasIssue => IsDuplicate || IsGroupZero;
    public string IssueBadge => (IsDuplicate && IsGroupZero) 
        ? "⚠️ Duplicate Grp 0" 
        : (IsDuplicate ? "⚠️ Duplicate Link" : (IsGroupZero ? "⚠️ Orphan Grp 0" : string.Empty));
    public string IssueTooltip => IsDuplicate 
        ? "Redundant duplicate record: An active link already exists for this same target device and group. Cleaning this up frees EEPROM space and eliminates repetitive All-Link retry traffic."
        : (IsGroupZero ? "Group 0 is an invalid Insteon scene (scenes are Group 1-255). This is an orphaned/corrupted link or aborted handshake leftover from older software." : string.Empty);

    public string DataHex => $"D1: 0x{Data1:X2}  D2: 0x{Data2:X2}  D3: 0x{Data3:X2}";
    public string TargetDisplay => string.IsNullOrWhiteSpace(TargetFriendlyName) ? TargetAddress : $"{TargetFriendlyName} ({TargetAddress})";
}

public class ActiveLinkPairModel
{
    public string ControllerAddress { get; set; } = string.Empty;
    public string ControllerFriendlyName { get; set; } = string.Empty;
    public string ResponderAddress { get; set; } = string.Empty;
    public string ResponderFriendlyName { get; set; } = string.Empty;
    public int Group { get; set; }
    public string MemAddrController { get; set; } = string.Empty;
    public string MemAddrResponder { get; set; } = string.Empty;

    public string DisplayText => $"{ControllerFriendlyName} ({ControllerAddress}) ➔ {ResponderFriendlyName} ({ResponderAddress}) [Group {Group}]";
}

public class DeviceLookupItem
{
    public string Address { get; set; } = string.Empty;
    public string FriendlyName { get; set; } = string.Empty;
    public string DeviceType { get; set; } = string.Empty;
    public int RecordCount { get; set; }
    public bool IsModem { get; set; }
    public bool IsBattery { get; set; }

    public string DisplayText => string.IsNullOrWhiteSpace(FriendlyName) || FriendlyName.Equals($"Device {Address}", StringComparison.OrdinalIgnoreCase)
        ? $"{Address} [{(string.IsNullOrWhiteSpace(DeviceType) ? "Device" : DeviceType)}]"
        : $"{FriendlyName} ({Address}) [{(string.IsNullOrWhiteSpace(DeviceType) ? "Device" : DeviceType)}]";

    public override string ToString() => DisplayText;
}

public static class InsteonAddressHelper
{
    private static readonly System.Text.RegularExpressions.Regex AddressRegex =
        new(@"^[0-9A-Fa-f]{2}\.[0-9A-Fa-f]{2}\.[0-9A-Fa-f]{2}$", System.Text.RegularExpressions.RegexOptions.Compiled);

    public static string Normalize(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw)) return string.Empty;

        // 1. Check if raw contains an embedded Insteon address pattern e.g. "Device (3C.6E.04) [Dimmer]"
        var match = System.Text.RegularExpressions.Regex.Match(raw, @"\b([0-9A-Fa-f]{2}[.: -]?[0-9A-Fa-f]{2}[.: -]?[0-9A-Fa-f]{2})\b");
        if (match.Success)
        {
            var clean = match.Value.Replace(":", "").Replace(".", "").Replace("-", "").Replace(" ", "").ToUpperInvariant();
            if (clean.Length == 6 && clean.All(c => "0123456789ABCDEF".Contains(c)))
            {
                return $"{clean.Substring(0, 2)}.{clean.Substring(2, 2)}.{clean.Substring(4, 2)}";
            }
        }

        // 2. Direct clean of bare address
        var directClean = raw.Trim().Replace(":", "").Replace(".", "").Replace("-", "").Replace(" ", "").ToUpperInvariant();
        if (directClean.Length == 6 && directClean.All(c => "0123456789ABCDEF".Contains(c)))
        {
            return $"{directClean.Substring(0, 2)}.{directClean.Substring(2, 2)}.{directClean.Substring(4, 2)}";
        }
        return raw.Trim().ToUpperInvariant();
    }

    public static bool IsValid(string? address)
    {
        if (string.IsNullOrWhiteSpace(address)) return false;
        var norm = Normalize(address);
        return AddressRegex.IsMatch(norm);
    }
}
