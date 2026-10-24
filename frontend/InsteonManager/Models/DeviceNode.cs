using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace InsteonManager.Models;

public class DeviceNode : INotifyPropertyChanged
{
    private string _address = string.Empty;
    private string _label = string.Empty;
    private double _x;
    private double _y;
    private double _width = 160;
    private double _height = 90;
    private bool _isModem;
    private bool _isSelected;
    private int _linkCount;
    private string _deviceType = "Unknown";
    private string _friendlyName = string.Empty;
    private bool _isBattery;
    private bool _isFilterChecked = true;
    private bool _isVisible = true;
    private string _modelName = string.Empty;
    private string _engineVersion = string.Empty;
    private string _readStrategy = string.Empty;
    private string _writeStrategy = string.Empty;
    private int _cat;
    private int _subcat;
    private int _firmware;
    private bool _isKeypad;
    private int _buttonCount = 1;
    private bool _isDimmable = true;

    public string Address
    {
        get => _address;
        set => SetProperty(ref _address, value);
    }

    public string Label
    {
        get => _label;
        set => SetProperty(ref _label, value);
    }

    public string FriendlyName
    {
        get => _friendlyName;
        set => SetProperty(ref _friendlyName, value);
    }

    public bool IsBattery
    {
        get => _isBattery;
        set
        {
            if (SetProperty(ref _isBattery, value))
            {
                OnPropertyChanged(nameof(CategoryBadge));
            }
        }
    }

    public bool IsFilterChecked
    {
        get => _isFilterChecked;
        set => SetProperty(ref _isFilterChecked, value);
    }

    public bool IsVisible
    {
        get => _isVisible;
        set => SetProperty(ref _isVisible, value);
    }

    public string CategoryBadge => IsModem
        ? "HUB / PLM"
        : (IsBattery ? $"🔋 {DeviceType}" : $"⚡ {DeviceType}");

    public double X
    {
        get => _x;
        set
        {
            if (SetProperty(ref _x, value))
            {
                OnPropertyChanged(nameof(CenterX));
            }
        }
    }

    public double Y
    {
        get => _y;
        set
        {
            if (SetProperty(ref _y, value))
            {
                OnPropertyChanged(nameof(CenterY));
            }
        }
    }

    public double Width
    {
        get => _width;
        set
        {
            if (SetProperty(ref _width, value))
            {
                OnPropertyChanged(nameof(CenterX));
            }
        }
    }

    public double Height
    {
        get => _height;
        set
        {
            if (SetProperty(ref _height, value))
            {
                OnPropertyChanged(nameof(CenterY));
            }
        }
    }

    public double CenterX => X + (Width / 2.0);
    public double CenterY => Y + (Height / 2.0);

    public bool IsModem
    {
        get => _isModem;
        set
        {
            if (SetProperty(ref _isModem, value))
            {
                OnPropertyChanged(nameof(CategoryBadge));
            }
        }
    }

    public bool IsSelected
    {
        get => _isSelected;
        set => SetProperty(ref _isSelected, value);
    }

    public int LinkCount
    {
        get => _linkCount;
        set => SetProperty(ref _linkCount, value);
    }

    public string DeviceType
    {
        get => _deviceType;
        set
        {
            if (SetProperty(ref _deviceType, value))
            {
                OnPropertyChanged(nameof(CategoryBadge));
                OnPropertyChanged(nameof(DetailedHardwareTooltip));
            }
        }
    }

    public string ModelName
    {
        get => _modelName;
        set
        {
            if (SetProperty(ref _modelName, value))
            {
                OnPropertyChanged(nameof(HardwareSummary));
                OnPropertyChanged(nameof(DetailedHardwareTooltip));
            }
        }
    }

    public string EngineVersion
    {
        get => _engineVersion;
        set
        {
            if (SetProperty(ref _engineVersion, value))
            {
                OnPropertyChanged(nameof(HardwareSummary));
                OnPropertyChanged(nameof(EngineBadgeShort));
                OnPropertyChanged(nameof(DetailedHardwareTooltip));
            }
        }
    }

    public string ReadStrategy
    {
        get => _readStrategy;
        set { if (SetProperty(ref _readStrategy, value)) OnPropertyChanged(nameof(DetailedHardwareTooltip)); }
    }

    public string WriteStrategy
    {
        get => _writeStrategy;
        set { if (SetProperty(ref _writeStrategy, value)) OnPropertyChanged(nameof(DetailedHardwareTooltip)); }
    }

    public int Cat
    {
        get => _cat;
        set { if (SetProperty(ref _cat, value)) OnPropertyChanged(nameof(DetailedHardwareTooltip)); }
    }

    public int Subcat
    {
        get => _subcat;
        set { if (SetProperty(ref _subcat, value)) OnPropertyChanged(nameof(DetailedHardwareTooltip)); }
    }

    public int Firmware
    {
        get => _firmware;
        set { if (SetProperty(ref _firmware, value)) OnPropertyChanged(nameof(DetailedHardwareTooltip)); }
    }

    public bool IsKeypad
    {
        get => _isKeypad;
        set => SetProperty(ref _isKeypad, value);
    }

    public int ButtonCount
    {
        get => _buttonCount;
        set => SetProperty(ref _buttonCount, value);
    }

    public bool IsDimmable
    {
        get => _isDimmable;
        set => SetProperty(ref _isDimmable, value);
    }

    public string EngineBadgeShort => EngineVersion?.ToLowerInvariant() switch
    {
        "i1" => "I1",
        "i2" => "I2",
        "i2cs" => "I2CS",
        _ => EngineVersion?.ToUpperInvariant() ?? ""
    };

    public string HardwareSummary => !string.IsNullOrWhiteSpace(ModelName)
        ? (string.IsNullOrWhiteSpace(EngineBadgeShort) ? ModelName : $"{ModelName} • {EngineBadgeShort}")
        : DeviceType;

    public string DetailedHardwareTooltip =>
        $"📍 Device: {FriendlyName} ({Address})\n" +
        $"🏷️ Model: {(string.IsNullOrWhiteSpace(ModelName) ? DeviceType : ModelName)}\n" +
        $"🔬 Hardware Spec: Cat 0x{Cat:X2}, Subcat 0x{Subcat:X2}" + (Firmware > 0 ? $", Firmware v{Firmware}" : "") + "\n" +
        $"⚙️ Engine: {(string.IsNullOrWhiteSpace(EngineVersion) ? "Standard" : EngineVersion.ToUpperInvariant())}\n" +
        $"🔌 Silicon I/O: {ReadStrategy} / {WriteStrategy}\n" +
        $"⚡ Power: {(IsBattery ? "🔋 Battery Remote" : "⚡ AC Line Powered")}" +
        (IsKeypad ? $"\n🎛️ Keypad: {ButtonCount} Buttons" : "");

    public event PropertyChangedEventHandler? PropertyChanged;

    protected void OnPropertyChanged([CallerMemberName] string? propertyName = null)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }

    protected bool SetProperty<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return false;
        field = value;
        OnPropertyChanged(propertyName);
        return true;
    }
}
