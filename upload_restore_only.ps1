# PowerShell script to upload restoration script and pristine snapshots to Raspberry Pi
$ErrorActionPreference = "Stop"

$repoRoot = "d:\Additional Documents\Antigravity Projects\Insteon GUI"
$piUser = "openhabian"
$piHost = "openhabian5"
$destDir = "/opt/insteon-bridge"

Write-Host "==> Uploading restore script to ${piHost}:${destDir}/scripts/ and ${destDir}/ ..." -ForegroundColor Cyan
scp "$repoRoot\backend\scripts\restore_pristine_links.py" "$repoRoot\backend\scripts\__init__.py" "${piUser}@${piHost}:${destDir}/scripts/"
scp "$repoRoot\backend\scripts\restore_pristine_links.py" "${piUser}@${piHost}:${destDir}/"

Write-Host "==> Ensuring snapshots directory exists on ${piHost}..." -ForegroundColor Cyan
ssh "${piUser}@${piHost}" "mkdir -p ${destDir}/snapshots ~/golden_snapshots"

Write-Host "==> Uploading golden 100% clean snapshots to ${piHost}:${destDir}/snapshots/ and ~/golden_snapshots/ ..." -ForegroundColor Cyan
scp "$repoRoot\backend\snapshots\network_snapshot.100_clean_a5f7e47.json" "${piUser}@${piHost}:${destDir}/snapshots/"
scp "$repoRoot\backend\snapshots\network_snapshot.100_clean_8fca33b.json" "${piUser}@${piHost}:${destDir}/snapshots/"
scp "$repoRoot\backend\network_snapshot.clean_100_golden.json" "${piUser}@${piHost}:${destDir}/snapshots/"
scp "$repoRoot\backend\network_snapshot.clean_100_golden.json" "${piUser}@${piHost}:~/golden_snapshots/"

Write-Host "==> Upload complete!" -ForegroundColor Green
