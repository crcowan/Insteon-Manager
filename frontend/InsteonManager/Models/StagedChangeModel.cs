namespace InsteonManager.Models;

public enum ChangeRisk
{
    Low,
    Medium,
    High
}

public class StagedChangeModel
{
    public string Id { get; set; } = string.Empty;
    public string TargetAddress { get; set; } = string.Empty;
    public string TargetLabel { get; set; } = string.Empty;
    public string Operation { get; set; } = string.Empty;
    public string CurrentState { get; set; } = string.Empty;
    public string ProposedState { get; set; } = string.Empty;
    public string Description { get; set; } = string.Empty;
    public ChangeRisk Risk { get; set; } = ChangeRisk.Low;
    public bool IsSelected { get; set; } = true;
    public DateTime StagedAt { get; set; } = DateTime.Now;

    public string RiskBadge => Risk.ToString().ToUpper();

    public string RiskColorHex => Risk switch
    {
        ChangeRisk.Low => "#A6E3A1",     // Green
        ChangeRisk.Medium => "#F9E2AF",  // Amber
        ChangeRisk.High => "#F38BA8",    // Red
        _ => "#89B4FA"
    };

    public string FormattedTime => StagedAt.ToString("HH:mm:ss");
}
