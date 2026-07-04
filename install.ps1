# Aide Agent Installer
# Usage: powershell -ExecutionPolicy Bypass -File install.ps1
param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Get-Item $ScriptDir).FullName
$AideHome = if ($env:AIDE_HOME) { $env:AIDE_HOME } else { Join-Path $env:USERPROFILE ".aide" }
$AideBin = Join-Path $AideHome "bin"

if ($Uninstall) {
    Write-Host "Uninstalling aide command..."
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -like "*$AideBin*") {
        $newPath = ($userPath -split ";" | Where-Object { $_ -ne $AideBin }) -join ";"
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        Write-Host "  Removed from PATH: $AideBin"
    }
    if (Test-Path $AideBin) {
        Remove-Item -Recurse -Force $AideBin
        Write-Host "  Deleted: $AideBin"
    }
    $pathFile = Join-Path $AideHome ".project_path"
    if (Test-Path $pathFile) { Remove-Item $pathFile }
    Write-Host "Done. Restart terminal to apply."
    exit 0
}

Write-Host "Installing aide command..."
Write-Host "  Project: $ProjectRoot"
Write-Host "  Target: $AideBin"

# Save project location
$ProjectRoot | Out-File -Encoding ASCII (Join-Path $AideHome ".project_path")

# Create bin directory and copy launcher
New-Item -ItemType Directory -Force -Path $AideBin | Out-Null
Copy-Item -Force (Join-Path $ProjectRoot "aide.ps1") $AideBin
Write-Host "  Copied aide.ps1 to $AideBin"

# Create aide.bat wrapper (sets window title before launching)
$batContent = "@echo off`r`ntitle Aide Agent`r`npowershell -ExecutionPolicy Bypass -File `"$AideBin\aide.ps1`" %*`r`n"
[System.IO.File]::WriteAllText((Join-Path $AideBin "aide.bat"), $batContent, [System.Text.Encoding]::ASCII)
Write-Host "  Created aide.bat"

# Add to user PATH
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
if ($userPath -notlike "*$AideBin*") {
    $newPath = if ($userPath) { "$userPath;$AideBin" } else { $AideBin }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "  Added to PATH: $AideBin"
}

Write-Host ""
Write-Host "Install complete! Restart terminal and type 'aide' to start."
Write-Host ""
Write-Host "Uninstall: powershell -ExecutionPolicy Bypass -File $ProjectRoot\install.ps1 -Uninstall"
