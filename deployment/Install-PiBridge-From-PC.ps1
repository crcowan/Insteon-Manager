# Remote Installer: Deploy & Install Insteon Bridge to a Raspberry Pi from a Windows PC over SSH
param(
    [string]$PiHost = "",
    [string]$PiUser = "",
    [string]$OpenHabUrl = "",
    [string]$OpenHabToken = "",
    [string]$OpenHabThingUid = "",
    [string]$InsteonPort = ""
)

$ErrorActionPreference = "Stop"

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host " Insteon Bridge - Remote Raspberry Pi Installer (from Windows PC)" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host ""

if (-not $PiHost) {
    $inputHost = Read-Host "Enter Raspberry Pi hostname or IP address [default: openhabian5]"
    $PiHost = if ($inputHost) { $inputHost.Trim() } else { "openhabian5" }
}

if (-not $PiUser) {
    $inputUser = Read-Host "Enter Raspberry Pi SSH username [default: openhabian]"
    $PiUser = if ($inputUser) { $inputUser.Trim() } else { "openhabian" }
}

Write-Host ""
Write-Host "--- Optional Initial .env Configuration (Press Enter to skip/keep existing) ---" -ForegroundColor Yellow
if (-not $OpenHabUrl) {
    $OpenHabUrl = (Read-Host "openHAB URL [default: http://localhost:8080]").Trim()
}
if (-not $OpenHabToken) {
    $OpenHabToken = (Read-Host "openHAB API Token (oh.insteonbridge....) [leave blank to keep existing or edit later]").Trim()
}
if (-not $OpenHabThingUid) {
    $OpenHabThingUid = (Read-Host "openHAB Insteon Thing UID [default: insteon:network:local]").Trim()
}
if (-not $InsteonPort) {
    $InsteonPort = (Read-Host "USB PLM Serial Port [default: /dev/ttyUSB0]").Trim()
}

# Locate backend and deployment directories relative to this script
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Test-Path (Join-Path $scriptDir "backend\app\main.py")) {
    $bundleRoot = $scriptDir
} elseif (Test-Path (Join-Path (Split-Path -Parent $scriptDir) "backend\app\main.py")) {
    $bundleRoot = Split-Path -Parent $scriptDir
} else {
    Write-Host "ERROR: Could not locate backend/app/main.py relative to $scriptDir" -ForegroundColor Red
    exit 1
}

$remoteStageDir = "/tmp/insteon-bridge-installer"

Write-Host ""
Write-Host "==> [1/3] Preparing staging directory on ${PiUser}@${PiHost}:${remoteStageDir} ..." -ForegroundColor Cyan
ssh "${PiUser}@${PiHost}" "rm -rf ${remoteStageDir} && mkdir -p ${remoteStageDir}/backend/app/services ${remoteStageDir}/backend/scripts ${remoteStageDir}/deployment"

Write-Host "==> [2/3] Uploading Insteon Bridge package files via SCP..." -ForegroundColor Cyan
scp "$bundleRoot\backend\requirements.txt" "$bundleRoot\backend\.env.example" "${PiUser}@${PiHost}:${remoteStageDir}/backend/"
scp "$bundleRoot\backend\app\__init__.py" "$bundleRoot\backend\app\config.py" "$bundleRoot\backend\app\device_registry.py" "$bundleRoot\backend\app\insteon.py" "$bundleRoot\backend\app\main.py" "$bundleRoot\backend\app\models.py" "$bundleRoot\backend\app\openhab.py" "$bundleRoot\backend\app\session.py" "${PiUser}@${PiHost}:${remoteStageDir}/backend/app/"
scp "$bundleRoot\backend\app\services\scene_service.py" "$bundleRoot\backend\app\services\transaction_service.py" "${PiUser}@${PiHost}:${remoteStageDir}/backend/app/services/"
if (Test-Path "$bundleRoot\backend\scripts") {
    scp "$bundleRoot\backend\scripts\*.py" "${PiUser}@${PiHost}:${remoteStageDir}/backend/scripts/"
}
scp "$bundleRoot\deployment\insteon-bridge.service" "$bundleRoot\deployment\setup_pi.sh" "${PiUser}@${PiHost}:${remoteStageDir}/deployment/"

$installArgs = @("--non-interactive")
if ($OpenHabUrl) { $installArgs += "--openhab-url=$OpenHabUrl" }
if ($OpenHabToken) { $installArgs += "--openhab-token=$OpenHabToken" }
if ($OpenHabThingUid) { $installArgs += "--openhab-thing=$OpenHabThingUid" }
if ($InsteonPort) { $installArgs += "--insteon-port=$InsteonPort" }
$argString = $installArgs -join " "

Write-Host "==> [3/3] Running automated setup_pi.sh installer on ${PiHost}..." -ForegroundColor Cyan
ssh -tt "${PiUser}@${PiHost}" "sed -i 's/\r$//' ${remoteStageDir}/deployment/setup_pi.sh ${remoteStageDir}/deployment/insteon-bridge.service && sudo bash ${remoteStageDir}/deployment/setup_pi.sh ${argString} && rm -rf ${remoteStageDir}"

Write-Host ""
Write-Host "==> Remote Raspberry Pi installation finished!" -ForegroundColor Green
