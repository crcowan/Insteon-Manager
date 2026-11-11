$ErrorActionPreference = "Stop"

$WshShell = New-Object -ComObject WScript.Shell
$DesktopPath = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
$ShortcutPath = Join-Path $DesktopPath "Insteon Manager.lnk"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TargetPath = Join-Path $ScriptDir "InsteonManager.exe"
$IconPath = Join-Path $ScriptDir "icon.ico"

if (-not (Test-Path $TargetPath)) {
    Write-Host "ERROR: Could not find InsteonManager.exe in $ScriptDir" -ForegroundColor Red
    exit 1
}

$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $TargetPath
$Shortcut.WorkingDirectory = $ScriptDir
$Shortcut.Description = "Insteon Network Studio - Topology, Diagnostics & ALDB Management Suite"
if (Test-Path $IconPath) {
    $Shortcut.IconLocation = "$IconPath,0"
} else {
    $Shortcut.IconLocation = "$TargetPath,0"
}
$Shortcut.Save()

Write-Host "Created 'Insteon Manager' shortcut on your Desktop:" -ForegroundColor Green
Write-Host "  -> $ShortcutPath" -ForegroundColor Cyan
Write-Host "  -> Target: $TargetPath" -ForegroundColor Gray
