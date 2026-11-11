# Package Insteon Manager WPF Application as a Self-Contained Single-File Release
$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
if (-not $repoRoot) {
    $repoRoot = "d:\Additional Documents\Antigravity Projects\Insteon GUI"
}
$projectPath = Join-Path $repoRoot "frontend\InsteonManager\InsteonManager.csproj"
$outputDir = Join-Path $repoRoot "dist\InsteonManager"

Write-Host "==> Checking for running InsteonManager instances..." -ForegroundColor Cyan
Stop-Process -Name "InsteonManager" -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500

Write-Host "==> Publishing self-contained single-file Windows x64 executable..." -ForegroundColor Cyan
dotnet publish $projectPath `
    -c Release `
    -r win-x64 `
    --self-contained true `
    -p:PublishSingleFile=true `
    -p:IncludeNativeLibrariesForSelfExtract=true `
    -p:EnableCompressionInSingleFile=true `
    -p:DebugType=None `
    -p:DebugSymbols=false `
    -o $outputDir

# Remove any leftover framework-dependent DLLs/PDBs from older builds in dist\InsteonManager
Get-ChildItem -Path $outputDir -File | Where-Object {
    $_.Extension -in @(".dll", ".pdb", ".deps.json", ".runtimeconfig.json")
} | Remove-Item -Force -ErrorAction SilentlyContinue

# Copy shortcut helpers and icons into the output folder
Copy-Item (Join-Path $repoRoot "frontend\InsteonManager\icon.ico") $outputDir -Force
Copy-Item (Join-Path $repoRoot "frontend\InsteonManager\icon.png") $outputDir -Force
Copy-Item (Join-Path $repoRoot "Create-DesktopShortcut.ps1") $outputDir -Force
Copy-Item (Join-Path $repoRoot "Install-DesktopShortcut.cmd") $outputDir -Force

Write-Host "==> Self-contained application packaged successfully to: $outputDir" -ForegroundColor Green
Write-Host "==> Ready to launch from Desktop shortcut or Launch_Insteon_Manager.bat!" -ForegroundColor Green
