# Build & Package Official GitHub Release Bundles for Insteon Network Studio (PC + Raspberry Pi)
param(
    [string]$Version = "v2.0.0"
)

$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
if (-not $repoRoot) {
    $repoRoot = "d:\Additional Documents\Antigravity Projects\Insteon GUI"
}

$stagingRoot = Join-Path $repoRoot "dist\release_staging\$Version"
$releaseOutDir = Join-Path $repoRoot "dist\releases\$Version"

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host " Building Insteon Network Studio Release Packages ($Version)" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

# 1. Build self-contained Windows Desktop executable via package_frontend.ps1
& (Join-Path $repoRoot "package_frontend.ps1")

# 2. Prepare clean staging & output directories
if (Test-Path $stagingRoot) {
    Remove-Item $stagingRoot -Recurse -Force
}
if (Test-Path $releaseOutDir) {
    Remove-Item $releaseOutDir -Recurse -Force
}
New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
New-Item -ItemType Directory -Path $releaseOutDir -Force | Out-Null

# ------------------------------------------------------------------
# PART A: Windows PC Self-Contained Release Bundle
# ------------------------------------------------------------------
Write-Host "`n==> [1/2] Assembling Windows PC Release Bundle (InsteonManager-Windows-x64)..." -ForegroundColor Cyan
$winStageDir = Join-Path $stagingRoot "InsteonManager-Windows-x64"
New-Item -ItemType Directory -Path $winStageDir -Force | Out-Null

$distAppDir = Join-Path $repoRoot "dist\InsteonManager"
Copy-Item (Join-Path $distAppDir "InsteonManager.exe") $winStageDir -Force
Copy-Item (Join-Path $distAppDir "icon.ico") $winStageDir -Force
Copy-Item (Join-Path $distAppDir "icon.png") $winStageDir -Force
Copy-Item (Join-Path $distAppDir "Create-DesktopShortcut.ps1") $winStageDir -Force
Copy-Item (Join-Path $distAppDir "Install-DesktopShortcut.cmd") $winStageDir -Force

$winQuickStart = @"
================================================================================
 Insteon Network Studio ($Version) - Windows 10/11 (x64) Quick Start
================================================================================

NO COMPILING OR .NET INSTALLATION REQUIRED!
InsteonManager.exe is a self-contained single-file Windows executable with all
required runtime libraries bundled inside.

INSTALLATION STEPS:
1. Extract this entire ZIP archive to a permanent folder on your PC
   (for example: C:\InsteonManager or %LOCALAPPDATA%\InsteonManager).
2. Double-click "Install-DesktopShortcut.cmd" to create the "Insteon Manager"
   shortcut (with icon) on your Windows Desktop.
3. Launch "Insteon Manager" from your Desktop (or double-click InsteonManager.exe).
4. In the top-right "Bridge" box, enter your Raspberry Pi Bridge URL:
     http://<your-raspberry-pi-ip>:8000
   and click "Test Link".

Full documentation: https://github.com/crcowan/Insteon-Manager
================================================================================
"@
Set-Content -Path (Join-Path $winStageDir "QUICKSTART-WINDOWS.txt") -Value $winQuickStart -Encoding UTF8

$winZipVersioned = Join-Path $releaseOutDir "InsteonManager-Windows-x64-$Version.zip"
$winZipLatest = Join-Path $releaseOutDir "InsteonManager-Windows-x64.zip"
Compress-Archive -Path "$winStageDir\*" -DestinationPath $winZipVersioned -Force
Copy-Item $winZipVersioned $winZipLatest -Force
Write-Host "    Created: $winZipVersioned" -ForegroundColor Green
Write-Host "    Created: $winZipLatest" -ForegroundColor Green

# ------------------------------------------------------------------
# PART B: Raspberry Pi Bridge Release Bundle
# ------------------------------------------------------------------
Write-Host "`n==> [2/2] Assembling Raspberry Pi Bridge Release Bundle (InsteonBridge-RaspberryPi)..." -ForegroundColor Cyan
$piStageDir = Join-Path $stagingRoot "InsteonBridge-RaspberryPi"
New-Item -ItemType Directory -Path (Join-Path $piStageDir "backend\app\services") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $piStageDir "backend\scripts") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $piStageDir "deployment") -Force | Out-Null

# Copy backend Python modules (excluding __pycache__, .venv, .env, and personal snapshots)
Get-ChildItem -Path (Join-Path $repoRoot "backend\app") -Filter "*.py" -File | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $piStageDir "backend\app") -Force
}
Get-ChildItem -Path (Join-Path $repoRoot "backend\app\services") -Filter "*.py" -File | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $piStageDir "backend\app\services") -Force
}
Get-ChildItem -Path (Join-Path $repoRoot "backend\scripts") -Filter "*.py" -File | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $piStageDir "backend\scripts") -Force
}

# Helper to write files with strict Unix LF (\n) line endings for Linux compatibility
function Copy-WithUnixLF([string]$SourcePath, [string]$DestPath) {
    $rawText = [System.IO.File]::ReadAllText($SourcePath)
    $lfText = $rawText -replace "`r`n", "`n"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($DestPath, $lfText, $utf8NoBom)
}

Copy-WithUnixLF (Join-Path $repoRoot "backend\requirements.txt") (Join-Path $piStageDir "backend\requirements.txt")
Copy-WithUnixLF (Join-Path $repoRoot "backend\.env.example") (Join-Path $piStageDir "backend\.env.example")
Copy-WithUnixLF (Join-Path $repoRoot "deployment\insteon-bridge.service") (Join-Path $piStageDir "deployment\insteon-bridge.service")
Copy-WithUnixLF (Join-Path $repoRoot "deployment\setup_pi.sh") (Join-Path $piStageDir "deployment\setup_pi.sh")
Copy-WithUnixLF (Join-Path $repoRoot "deployment\setup_pi.sh") (Join-Path $piStageDir "install_pi.sh")

# Include Windows-to-Pi remote SSH installer scripts
Copy-Item (Join-Path $repoRoot "deployment\Install-PiBridge-From-PC.ps1") $piStageDir -Force
Copy-Item (Join-Path $repoRoot "deployment\Install-PiBridge-From-PC.cmd") $piStageDir -Force

$piQuickStart = @"
================================================================================
 Insteon Bridge ($Version) - Raspberry Pi / Linux Host Quick Start
================================================================================

OPTION A: Direct Installation on the Raspberry Pi (via SSH or Terminal)
-----------------------------------------------------------------------
1. Extract this archive and run the turnkey installer:
     tar -xzf InsteonBridge-RaspberryPi.tar.gz
     cd InsteonBridge-RaspberryPi
     sudo bash install_pi.sh

   (Or run the 1-line command directly on your Raspberry Pi):
     curl -sSL https://github.com/crcowan/Insteon-Manager/releases/latest/download/InsteonBridge-RaspberryPi.tar.gz | tar -xz && cd InsteonBridge-RaspberryPi && sudo bash install_pi.sh

OPTION B: Remote 1-Click Installation from your Windows PC (via SSH)
--------------------------------------------------------------------
1. Extract InsteonBridge-RaspberryPi.zip on your Windows PC.
2. Double-click "Install-PiBridge-From-PC.cmd".
3. Enter your Raspberry Pi hostname/IP (e.g., openhabian5) and SSH username
   (e.g., openhabian). The script automatically uploads the bridge files and
   configures the systemd service on your Pi!

CONFIGURATION (/opt/insteon-bridge/.env):
-----------------------------------------
Edit /opt/insteon-bridge/.env if needed:
  sudo nano /opt/insteon-bridge/.env
  sudo systemctl restart insteon-bridge

Full documentation: https://github.com/crcowan/Insteon-Manager
================================================================================
"@
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $piStageDir "QUICKSTART-RASPBERRY-PI.txt"), ($piQuickStart -replace "`r`n", "`n"), $utf8NoBom)

# Create .tar.gz and .zip archives for Raspberry Pi
$piTarVersioned = Join-Path $releaseOutDir "InsteonBridge-RaspberryPi-$Version.tar.gz"
$piTarLatest = Join-Path $releaseOutDir "InsteonBridge-RaspberryPi.tar.gz"
$piZipVersioned = Join-Path $releaseOutDir "InsteonBridge-RaspberryPi-$Version.zip"
$piZipLatest = Join-Path $releaseOutDir "InsteonBridge-RaspberryPi.zip"

Push-Location $stagingRoot
try {
    tar -czf $piTarVersioned "InsteonBridge-RaspberryPi"
} finally {
    Pop-Location
}
Copy-Item $piTarVersioned $piTarLatest -Force

Compress-Archive -Path "$piStageDir\*" -DestinationPath $piZipVersioned -Force
Copy-Item $piZipVersioned $piZipLatest -Force

Write-Host "    Created: $piTarVersioned" -ForegroundColor Green
Write-Host "    Created: $piTarLatest" -ForegroundColor Green
Write-Host "    Created: $piZipVersioned" -ForegroundColor Green
Write-Host "    Created: $piZipLatest" -ForegroundColor Green

Write-Host "`n==================================================================" -ForegroundColor Green
Write-Host " All Release Packages Built Successfully in:" -ForegroundColor Green
Write-Host "   $releaseOutDir" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Green
Get-ChildItem $releaseOutDir | Select-Object Name, @{Name="Size (MB)";Expression={[math]::Round($_.Length / 1MB, 2)}} | Format-Table -AutoSize
