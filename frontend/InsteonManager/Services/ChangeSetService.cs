using InsteonManager.Models;

namespace InsteonManager.Services;

public class PreflightSummary
{
    public int TotalStagedCount { get; init; }
    public int SelectedCount { get; init; }
    public int HighRiskCount { get; init; }
    public int MediumRiskCount { get; init; }
    public int BatteryDeviceCount { get; init; }
    public string SummaryHeadline { get; init; } = string.Empty;
    public string DetailSubtext { get; init; } = string.Empty;
    public string BannerBackground { get; init; } = "#1E293B";
    public string BannerBorder { get; init; } = "#45475A";
    public string BannerForeground { get; init; } = "#89B4FA";
}

public class UndoEntry
{
    public string Description { get; init; } = string.Empty;
    public DateTime RecordedAt { get; init; } = DateTime.Now;
    public StagedChangeModel UndoChange { get; init; } = new();
}

/// <summary>
/// Centralized service for analyzing staged hardware changes (preflight safety checks)
/// and maintaining an undo stack for reversible EEPROM modifications.
/// </summary>
public class ChangeSetService
{
    private readonly Stack<UndoEntry> _undoStack = new();

    public bool CanUndo => _undoStack.Count > 0;

    public string LastUndoDescription => _undoStack.TryPeek(out var top)
        ? $"↩ Undo: {top.Description}"
        : "↩ Nothing to Undo";

    public void RecordUndo(string description, StagedChangeModel inverseStagedChange)
    {
        _undoStack.Push(new UndoEntry
        {
            Description = description,
            RecordedAt = DateTime.Now,
            UndoChange = inverseStagedChange
        });
    }

    public UndoEntry? PopUndo()
    {
        return _undoStack.TryPop(out var entry) ? entry : null;
    }

    public static bool InvolvesBatteryDevice(StagedChangeModel change)
    {
        return (change.TargetAddress?.Contains("1C.2C.40", StringComparison.OrdinalIgnoreCase) ?? false) ||
               (change.TargetLabel?.Contains("Battery", StringComparison.OrdinalIgnoreCase) ?? false) ||
               (change.TargetLabel?.Contains("Remote", StringComparison.OrdinalIgnoreCase) ?? false) ||
               (change.TargetLabel?.Contains("Sensor", StringComparison.OrdinalIgnoreCase) ?? false) ||
               (change.TargetLabel?.Contains("2342", StringComparison.OrdinalIgnoreCase) ?? false);
    }

    public PreflightSummary EvaluatePreflight(IEnumerable<StagedChangeModel> changes)
    {
        var list = changes.ToList();
        var selected = list.Where(c => c.IsSelected).ToList();

        if (list.Count == 0)
        {
            return new PreflightSummary
            {
                TotalStagedCount = 0,
                SelectedCount = 0,
                SummaryHeadline = "✓ Change Tray Empty — No Pending EEPROM Writes",
                DetailSubtext = "Stage link creations, deletions, device replacements, or health repairs from any workspace to review their exact hardware diff here before committing.",
                BannerBackground = "#181825",
                BannerBorder = "#313244",
                BannerForeground = "#A6ADC8"
            };
        }

        int highRisk = selected.Count(c => c.Risk == ChangeRisk.High);
        int mediumRisk = selected.Count(c => c.Risk == ChangeRisk.Medium);
        int batteryCount = selected.Count(InvolvesBatteryDevice);
        int estSeconds = Math.Max(2, selected.Count * 2);

        if (batteryCount > 0)
        {
            return new PreflightSummary
            {
                TotalStagedCount = list.Count,
                SelectedCount = selected.Count,
                HighRiskCount = highRisk,
                MediumRiskCount = mediumRisk,
                BatteryDeviceCount = batteryCount,
                SummaryHeadline = $"🔋 Preflight Notice: {batteryCount} Staged Change(s) Target a Sleeping Battery Device (~{estSeconds}s est.)",
                DetailSubtext = "Before clicking 'Commit Changes to Hardware', walk to the battery-powered remote/sensor and hold its SET button (~3s) until its LED blinks.",
                BannerBackground = "#3B2A20",
                BannerBorder = "#F9E2AF",
                BannerForeground = "#F9E2AF"
            };
        }

        if (highRisk > 0)
        {
            return new PreflightSummary
            {
                TotalStagedCount = list.Count,
                SelectedCount = selected.Count,
                HighRiskCount = highRisk,
                MediumRiskCount = mediumRisk,
                BatteryDeviceCount = 0,
                SummaryHeadline = $"⚠️ High-Impact Preflight: {highRisk} Network-Wide Operation(s) Selected ({selected.Count} total, ~{estSeconds}s est.)",
                DetailSubtext = "High-risk operations rewrite ALDB records across multiple peer switches. Verify the Current vs. Proposed state columns below before committing.",
                BannerBackground = "#3D2930",
                BannerBorder = "#F38BA8",
                BannerForeground = "#F38BA8"
            };
        }

        return new PreflightSummary
        {
            TotalStagedCount = list.Count,
            SelectedCount = selected.Count,
            HighRiskCount = 0,
            MediumRiskCount = mediumRisk,
            BatteryDeviceCount = 0,
            SummaryHeadline = $"🛡️ Preflight Ready: {selected.Count} of {list.Count} Staged Change(s) Selected for Atomic 2PC Commit (~{estSeconds}s est.)",
            DetailSubtext = "Every staged change will be written over the powerline and verified via physical EEPROM read-back before updating the local snapshot.",
            BannerBackground = "#1E293B",
            BannerBorder = "#89B4FA",
            BannerForeground = "#89B4FA"
        };
    }
}
