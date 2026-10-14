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

public class OrganizedDeviceAldb
{
    public string Address { get; set; } = string.Empty;
    public string FriendlyName { get; set; } = string.Empty;
    public string DeviceType { get; set; } = string.Empty;
    public bool IsModem { get; set; }
    public bool IsBattery { get; set; }
    public int TotalRecords => ControllerRecords.Count + ResponderRecords.Count + OtherRecords.Count;
    public int ControllerCount => ControllerRecords.Count;
    public int ResponderCount => ResponderRecords.Count;

    public ObservableCollection<OrganizedAldbEntry> ControllerRecords { get; set; } = new();
    public ObservableCollection<OrganizedAldbEntry> ResponderRecords { get; set; } = new();
    public ObservableCollection<OrganizedAldbEntry> OtherRecords { get; set; } = new();

    public string DisplayTitle => string.IsNullOrWhiteSpace(FriendlyName) || FriendlyName.Equals($"Device {Address}", System.StringComparison.OrdinalIgnoreCase)
        ? Address
        : $"{FriendlyName} ({Address})";
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
        var clean = raw.Trim().Replace(":", "").Replace(".", "").Replace("-", "").Replace(" ", "").ToUpperInvariant();
        if (clean.Length == 6 && clean.All(c => "0123456789ABCDEF".Contains(c)))
        {
            return $"{clean.Substring(0, 2)}.{clean.Substring(2, 2)}.{clean.Substring(4, 2)}";
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
