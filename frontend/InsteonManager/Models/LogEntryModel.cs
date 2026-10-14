namespace InsteonManager.Models;

public enum LogLevel
{
    Info,
    Success,
    Warning,
    Error
}

public class LogEntryModel
{
    public DateTime Timestamp { get; set; } = DateTime.Now;
    public LogLevel Level { get; set; } = LogLevel.Info;
    public string Source { get; set; } = "System";
    public string Message { get; set; } = string.Empty;

    public string TimeString => Timestamp.ToString("HH:mm:ss.fff");

    public string LevelBadge => Level switch
    {
        LogLevel.Info => "INFO",
        LogLevel.Success => "SUCCESS",
        LogLevel.Warning => "WARN",
        LogLevel.Error => "ERROR",
        _ => "INFO"
    };

    public string LevelColorHex => Level switch
    {
        LogLevel.Info => "#89B4FA",       // Sky Blue
        LogLevel.Success => "#A6E3A1",    // Pastel Green
        LogLevel.Warning => "#F9E2AF",    // Amber / Gold
        LogLevel.Error => "#F38BA8",      // Red
        _ => "#CDD6F4"
    };
}
