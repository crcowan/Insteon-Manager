# PowerShell script to upload restoration script and pristine snapshots to Raspberry Pi
$ErrorActionPreference = "Stop"

$repoRoot = "d:\Additional Documents\Antigravity Projects\Insteon GUI"
$piUser = "openhabian"
$piHost = "openhabian5"
$destDir = "/opt/insteon-bridge"

Write-Host "==> Uploading restore script to ${piHost}:${destDir}/scripts/ and ${destDir}/ ..." -ForegroundColor Cyan
scp "$repoRoot\backend\scripts\restore_pristine_links.py" "$repoRoot\backend\scripts\__init__.py" "${piUser}@${piHost}:${destDir}/scripts/"
scp "$repoRoot\backend\scripts\restore_pristine_links.py" "${piUser}@${piHost}:${destDir}/"

Write-Host "==> Uploading golden pristine snapshots to ${piHost}:${destDir}/ ..." -ForegroundColor Cyan
scp "$repoRoot\backend\network_snapshot.pristine.json" "$repoRoot\backend\network_snapshot.json" "${piUser}@${piHost}:${destDir}/"
scp "$repoRoot\backend\network_snapshot.pristine.json" "$repoRoot\backend\network_snapshot.json" "${piUser}@${piHost}:${destDir}/backend/"

Write-Host "==> Upload complete!" -ForegroundColor Green
