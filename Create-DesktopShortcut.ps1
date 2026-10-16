$WshShell = New-Object -ComObject WScript.Shell
$DesktopPath = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
$ShortcutPath = Join-Path $DesktopPath "Insteon Manager.lnk"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TargetPath = Join-Path $ScriptDir "InsteonManager.exe"
$IconPath = Join-Path $ScriptDir "icon.ico"

$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $TargetPath
$Shortcut.WorkingDirectory = $ScriptDir
$Shortcut.Description = "Insteon Smart Home Network Topology and ALDB Inspector"
$Shortcut.IconLocation = "$IconPath,0"
$Shortcut.Save()

Write-Host "Created Insteon Manager shortcut on your Desktop: $ShortcutPath" -ForegroundColor Green
