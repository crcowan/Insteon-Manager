# Package Insteon Manager WPF Application for distribution / desktop launch
$ErrorActionPreference = "Stop"

$repoRoot = "d:\Additional Documents\Antigravity Projects\Insteon GUI"
$projectPath = Join-Path $repoRoot "frontend\InsteonManager\InsteonManager.csproj"
$outputDir = Join-Path $repoRoot "dist\InsteonManager"

Write-Host "==> Packaging Insteon Manager (Release build)..." -ForegroundColor Cyan
dotnet publish $projectPath -c Release -o $outputDir

Write-Host "==> Application packaged successfully to: $outputDir" -ForegroundColor Green
Write-Host "==> Ready to launch from Desktop shortcut or Launch_Insteon_Manager.bat!" -ForegroundColor Green
