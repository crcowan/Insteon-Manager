<#
.SYNOPSIS
    Creates a full zipped backup of the Insteon Manager repository,
    guaranteeing that the hidden .git directory, branches, and all files are preserved.
.DESCRIPTION
    Uses .NET System.IO.Compression.ZipFile to ensure hidden files/directories (like .git)
    and deep paths are accurately captured without PowerShell 5.1 Compress-Archive limitations.
#>

[CmdletBinding()]
param(
    [string]$DestinationDirectory = ".."
)

$ErrorActionPreference = "Stop"

# Load .NET Compression assembly
Add-Type -AssemblyName System.IO.Compression.FileSystem

$sourcePath = $PSScriptRoot
if (-not $sourcePath) {
    $sourcePath = (Get-Location).Path
}

$repoName = Split-Path $sourcePath -Leaf
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$destResolved = [System.IO.Path]::GetFullPath((Join-Path $sourcePath $DestinationDirectory))

if (-not (Test-Path $destResolved)) {
    New-Item -ItemType Directory -Path $destResolved -Force | Out-Null
}

$zipFileName = "${repoName}_FullBackup_${timestamp}.zip"
$zipFullPath = Join-Path $destResolved $zipFileName

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " Insteon Manager - Full Repository Backup" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Source Directory:      $sourcePath" -ForegroundColor White
Write-Host "Target Archive:        $zipFullPath" -ForegroundColor White
Write-Host "Including:             All source files, binaries, and hidden .git directory" -ForegroundColor Yellow
Write-Host "Creating archive, please wait..." -ForegroundColor Cyan

# Create the zip archive from directory (includes hidden files and .git)
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $sourcePath,
    $zipFullPath,
    [System.IO.Compression.CompressionLevel]::Optimal,
    $false
)

# Verification
if (Test-Path $zipFullPath) {
    $fileInfo = Get-Item $zipFullPath
    $sizeMB = [math]::Round($fileInfo.Length / 1MB, 2)
    
    # Inspect zip to confirm .git is inside
    $archive = [System.IO.Compression.ZipFile]::OpenRead($zipFullPath)
    $gitEntry = $archive.Entries | Where-Object { $_.FullName -like ".git/HEAD" -or $_.FullName -like ".git\HEAD" } | Select-Object -First 1
    $archive.Dispose()
    
    Write-Host "`n==> Backup created successfully!" -ForegroundColor Green
    Write-Host "Archive Size: $sizeMB MB ($($fileInfo.Length) bytes)" -ForegroundColor Green
    
    if ($gitEntry) {
        Write-Host "Verification: .git repository metadata confirmed inside archive." -ForegroundColor Green
    } else {
        Write-Host "Warning: .git directory was not detected in archive." -ForegroundColor Yellow
    }
    
    Write-Host "Backup location: $zipFullPath" -ForegroundColor Cyan
} else {
    Write-Error "Failed to generate backup archive."
}
