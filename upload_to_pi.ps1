# PowerShell script to upload all backend, restoration, and snapshot files to Raspberry Pi
$ErrorActionPreference = "Stop"

$repoRoot = "d:\Additional Documents\Antigravity Projects\Insteon GUI"
$piUser = "openhabian"
$piHost = "openhabian5"
$destDir = "/opt/insteon-bridge"

Write-Host "==> Uploading Python modules to ${piHost}:${destDir}/app/ ..." -ForegroundColor Cyan
scp "$repoRoot\backend\app\config.py" "$repoRoot\backend\app\insteon.py" "$repoRoot\backend\app\main.py" "$repoRoot\backend\app\session.py" "${piUser}@${piHost}:${destDir}/app/"

Write-Host "==> Uploading restoration script to ${piHost}:${destDir}/scripts/ and ${destDir}/ ..." -ForegroundColor Cyan
scp "$repoRoot\backend\scripts\restore_pristine_links.py" "${piUser}@${piHost}:${destDir}/scripts/"
scp "$repoRoot\backend\scripts\restore_pristine_links.py" "${piUser}@${piHost}:${destDir}/"

Write-Host "==> Uploading golden pristine snapshots to ${piHost}:${destDir}/ ..." -ForegroundColor Cyan
scp "$repoRoot\backend\network_snapshot.pristine.json" "$repoRoot\backend\network_snapshot.json" "${piUser}@${piHost}:${destDir}/"
scp "$repoRoot\backend\network_snapshot.pristine.json" "$repoRoot\backend\network_snapshot.json" "${piUser}@${piHost}:${destDir}/backend/"

Write-Host "==> All files uploaded successfully!" -ForegroundColor Green
