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
            }
        }
    }

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
