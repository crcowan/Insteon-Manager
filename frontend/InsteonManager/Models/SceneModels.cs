using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Text.Json.Serialization;

namespace InsteonManager.Models;

public class SceneModel : INotifyPropertyChanged
{
    private int _group;
    private string _name = string.Empty;
    private bool _isPlmControlled;
    private ObservableCollection<SceneControllerModel> _controllers = new();
    private ObservableCollection<SceneMemberModel> _members = new();

    [JsonPropertyName("group")]
    public int Group
    {
        get => _group;
        set
        {
            if (SetProperty(ref _group, value))
            {
                OnPropertyChanged(nameof(DisplayTitle));
            }
        }
    }

    [JsonPropertyName("name")]
    public string Name
    {
        get => _name;
        set
        {
            if (SetProperty(ref _name, value))
            {
                OnPropertyChanged(nameof(DisplayTitle));
            }
        }
    }

    [JsonPropertyName("is_plm_controlled")]
    public bool IsPlmControlled
    {
        get => _isPlmControlled;
        set => SetProperty(ref _isPlmControlled, value);
    }

    [JsonPropertyName("controllers")]
    public ObservableCollection<SceneControllerModel> Controllers
    {
        get => _controllers;
        set => SetProperty(ref _controllers, value);
    }

    [JsonPropertyName("members")]
    public ObservableCollection<SceneMemberModel> Members
    {
        get => _members;
        set
        {
            if (SetProperty(ref _members, value))
            {
                OnPropertyChanged(nameof(MemberCountText));
            }
        }
    }

    public string DisplayTitle => string.IsNullOrWhiteSpace(Name) || Name.Equals($"Insteon Scene {Group}", System.StringComparison.OrdinalIgnoreCase)
        ? $"Scene {Group}"
        : $"Scene {Group} - {Name}";

    public string MemberCountText => $"{Members?.Count ?? 0} Members";

    public event PropertyChangedEventHandler? PropertyChanged;

    protected bool SetProperty<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (Equals(field, value)) return false;
        field = value;
        OnPropertyChanged(propertyName);
        return true;
    }

    protected void OnPropertyChanged([CallerMemberName] string? propertyName = null)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}

public class SceneControllerModel : INotifyPropertyChanged
{
    private string _address = string.Empty;
    private string _label = string.Empty;
    private int _buttonGroup;
    private bool _isPlm;

    [JsonPropertyName("address")]
    public string Address
    {
        get => _address;
        set => SetProperty(ref _address, value);
    }

    [JsonPropertyName("label")]
    public string Label
    {
        get => _label;
        set => SetProperty(ref _label, value);
    }

    [JsonPropertyName("button_group")]
    public int ButtonGroup
    {
        get => _buttonGroup;
        set => SetProperty(ref _buttonGroup, value);
    }

    [JsonPropertyName("is_plm")]
    public bool IsPlm
    {
        get => _isPlm;
        set => SetProperty(ref _isPlm, value);
    }

    public string DisplayName => IsPlm ? "PLM Modem" : $"{Label} (Btn {ButtonGroup})";

    public event PropertyChangedEventHandler? PropertyChanged;

    protected bool SetProperty<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (Equals(field, value)) return false;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
        return true;
    }
}

public class SceneMemberModel : INotifyPropertyChanged
{
    private string _address = string.Empty;
    private string _label = string.Empty;
    private int _onLevel = 255;
    private int _rampRate = 28;
    private int _buttonGroup = 1;
    private string? _memAddr;
    private bool _isDirty;

    [JsonPropertyName("address")]
    public string Address
    {
        get => _address;
        set => SetProperty(ref _address, value);
    }

    [JsonPropertyName("label")]
    public string Label
    {
        get => _label;
        set => SetProperty(ref _label, value);
    }

    [JsonPropertyName("on_level")]
    public int OnLevel
    {
        get => _onLevel;
        set
        {
            if (SetProperty(ref _onLevel, Math.Clamp(value, 0, 255)))
            {
                OnPropertyChanged(nameof(BrightnessPercent));
                OnPropertyChanged(nameof(BrightnessPercentText));
                IsDirty = true;
            }
        }
    }

    [JsonPropertyName("ramp_rate")]
    public int RampRate
    {
        get => _rampRate;
        set
        {
            if (SetProperty(ref _rampRate, Math.Clamp(value, 0, 255)))
            {
                OnPropertyChanged(nameof(RampRateSecondsText));
                IsDirty = true;
            }
        }
    }

    [JsonPropertyName("button_group")]
    public int ButtonGroup
    {
        get => _buttonGroup;
        set => SetProperty(ref _buttonGroup, value);
    }

    [JsonPropertyName("mem_addr")]
    public string? MemAddr
    {
        get => _memAddr;
        set => SetProperty(ref _memAddr, value);
    }

    [JsonIgnore]
    public bool IsDirty
    {
        get => _isDirty;
        set => SetProperty(ref _isDirty, value);
    }

    [JsonIgnore]
    public double BrightnessPercent
    {
        get => Math.Round((_onLevel / 255.0) * 100.0, 0);
        set => OnLevel = (int)Math.Round((value / 100.0) * 255.0, 0);
    }

    [JsonIgnore]
    public string BrightnessPercentText => $"{BrightnessPercent:F0}% ({OnLevel}/255)";

    [JsonIgnore]
    public string RampRateSecondsText => FormatRampRate(RampRate);

    private static string FormatRampRate(int rate)
    {
        // Standard Insteon ramp rate table (rates 0-31)
        return rate switch
        {
            31 => "0.1s (Instant)",
            30 => "0.2s",
            29 => "0.3s",
            28 => "0.5s",
            27 => "2.0s",
            26 => "4.5s",
            25 => "6.5s",
            24 => "8.5s",
            23 => "19s",
            22 => "21.5s",
            21 => "23.5s",
            20 => "26s",
            19 => "28s",
            18 => "30s",
            17 => "32s",
            16 => "34s",
            15 => "38.5s",
            14 => "43s",
            13 => "47s",
            12 => "60s (1m)",
            255 => "Instant / Relay",
            _ => $"{rate} steps"
        };
    }

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

public class ReciprocalLinkRequestModel
{
    [JsonPropertyName("controller_address")]
    public string ControllerAddress { get; set; } = string.Empty;

    [JsonPropertyName("responder_address")]
    public string ResponderAddress { get; set; } = string.Empty;

    [JsonPropertyName("group")]
    public int Group { get; set; } = 1;

    [JsonPropertyName("data1")]
    public int Data1 { get; set; } = 255;

    [JsonPropertyName("data2")]
    public int Data2 { get; set; } = 28;

    [JsonPropertyName("data3")]
    public int Data3 { get; set; } = 1;
}

public class DeviceReplaceRequestModel
{
    [JsonPropertyName("old_address")]
    public string OldAddress { get; set; } = string.Empty;

    [JsonPropertyName("new_address")]
    public string NewAddress { get; set; } = string.Empty;

    [JsonPropertyName("copy_links")]
    public bool CopyLinks { get; set; } = true;

    [JsonPropertyName("sanitize_old")]
    public bool SanitizeOld { get; set; } = false;
}
