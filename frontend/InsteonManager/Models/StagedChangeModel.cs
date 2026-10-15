using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace InsteonManager.Models;

public enum ChangeRisk
{
    Low,
    Medium,
    High
}

public class StagedChangeModel : INotifyPropertyChanged
{
    private bool _isSelected = true;

    public string Id { get; set; } = string.Empty;
    public string TargetAddress { get; set; } = string.Empty;
    public string TargetLabel { get; set; } = string.Empty;
    public string Operation { get; set; } = string.Empty;
    public string CurrentState { get; set; } = string.Empty;
    public string ProposedState { get; set; } = string.Empty;
    public string Description { get; set; } = string.Empty;
    public ChangeRisk Risk { get; set; } = ChangeRisk.Low;

    public bool IsSelected
    {
        get => _isSelected;
        set
        {
            if (_isSelected != value)
            {
                _isSelected = value;
                OnPropertyChanged();
            }
        }
    }

    public DateTime StagedAt { get; set; } = DateTime.Now;

    public Func<Task<(bool Success, string Message)>>? ExecutionHandler { get; set; }

    public string RiskBadge => Risk.ToString().ToUpper();

    public string RiskColorHex => Risk switch
    {
        ChangeRisk.Low => "#A6E3A1",     // Green
        ChangeRisk.Medium => "#F9E2AF",  // Amber
        ChangeRisk.High => "#F38BA8",    // Red
        _ => "#89B4FA"
    };

    public string FormattedTime => StagedAt.ToString("HH:mm:ss");

    public event PropertyChangedEventHandler? PropertyChanged;
    protected void OnPropertyChanged([CallerMemberName] string? propertyName = null)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}
