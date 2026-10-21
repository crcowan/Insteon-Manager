# PowerShell script to deploy updated backend application code to Raspberry Pi
param(
    [string]$Password = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = "d:\Additional Documents\Antigravity Projects\Insteon GUI"
$piUser = "openhabian"
$piHost = "openhabian5"
$destDir = "/opt/insteon-bridge"

Write-Host "==> Uploading application Python files to ${piHost}:${destDir}/app/ ..." -ForegroundColor Cyan
scp "$repoRoot\backend\app\config.py" "$repoRoot\backend\app\insteon.py" "$repoRoot\backend\app\main.py" "$repoRoot\backend\app\models.py" "$repoRoot\backend\app\session.py" "${piUser}@${piHost}:${destDir}/app/"
scp "$repoRoot\backend\app\services\transaction_service.py" "$repoRoot\backend\app\services\scene_service.py" "${piUser}@${piHost}:${destDir}/app/services/"

Write-Host "==> Restarting insteon-bridge service on ${piHost}..." -ForegroundColor Cyan
if (-not $Password) {
    $Password = Read-Host "Enter openhabian sudo password (leave blank to attempt sudo directly)"
}

if ($Password) {
    ssh "${piUser}@${piHost}" "echo '$Password' | sudo -S systemctl restart insteon-bridge && sleep 2 && systemctl status insteon-bridge --no-pager"
} else {
    ssh -tt "${piUser}@${piHost}" "sudo systemctl restart insteon-bridge && sleep 2 && systemctl status insteon-bridge --no-pager"
}

Write-Host "==> Deployment complete!" -ForegroundColor Green
