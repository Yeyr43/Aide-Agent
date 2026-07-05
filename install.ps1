<#
Aide Agent — 一键安装脚本（源码版）
用法:
  # 本地（已 clone 的项目目录内）
  powershell -ExecutionPolicy Bypass -File install.ps1

  # 远程一键安装
  irm https://raw.githubusercontent.com/Yeyr43/Aide-Agent/main/install.ps1 | iex

  # 卸载
  powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall
#>
param(
    [switch]$Uninstall,
    [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/Yeyr43/Aide-Agent.git"
$InstallDir = "$env:LOCALAPPDATA\Aide-Agent"
$AideBin = "$env:USERPROFILE\.aide\bin"

# ═══════════════════════════════════════════════════════════════
# 卸载
# ═══════════════════════════════════════════════════════════════
if ($Uninstall) {
    Write-Host "Uninstalling Aide Agent..."
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User"); if (-not $userPath) { $userPath = "" }
    if ($userPath -like "*$AideBin*") {
        $newPath = ($userPath -split ";" | Where-Object { $_ -ne $AideBin }) -join ";"
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        Write-Host "  Removed from PATH: $AideBin"
    }
    if (Test-Path $AideBin) {
        Remove-Item -Recurse -Force $AideBin
        Write-Host "  Deleted: $AideBin"
    }
    if (Test-Path $InstallDir) {
        Remove-Item -Recurse -Force $InstallDir
        Write-Host "  Deleted: $InstallDir"
    }
    Write-Host "Done. Restart terminal to apply."
    exit 0
}

# ═══════════════════════════════════════════════════════════════
# 前置检查
# ═══════════════════════════════════════════════════════════════
Write-Host "=== Aide Agent Installer ==="

# 检查 git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error "git not found. Install Git: https://git-scm.com/download/win"
    exit 1
}
Write-Host "[OK] git: $(git --version)"

# 检查 uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..."
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
}
Write-Host "[OK] uv: $(uv --version)"

# ═══════════════════════════════════════════════════════════════
# Clone / Update
# ═══════════════════════════════════════════════════════════════
if (Test-Path (Join-Path $InstallDir ".git")) {
    Write-Host "Updating existing install at $InstallDir..."
    Push-Location $InstallDir
    git fetch origin
    git checkout $Branch
    git pull origin $Branch
    Pop-Location
} else {
    Write-Host "Cloning to $InstallDir..."
    git clone --branch $Branch $RepoUrl $InstallDir
}

# ═══════════════════════════════════════════════════════════════
# Install dependencies
# ═══════════════════════════════════════════════════════════════
Push-Location $InstallDir
Write-Host "Installing dependencies (uv sync)..."
uv sync
Pop-Location

# ═══════════════════════════════════════════════════════════════
# Create launcher + PATH
# ═══════════════════════════════════════════════════════════════
New-Item -ItemType Directory -Force -Path $AideBin | Out-Null

$batContent = @"
@echo off
title Aide Agent
cd /d $InstallDir
uv run python shell\main.py
"@
[System.IO.File]::WriteAllText((Join-Path $AideBin "aide.bat"), $batContent, [System.Text.Encoding]::ASCII)
Write-Host "[OK] Created aide.bat"

$userPath = [Environment]::GetEnvironmentVariable("Path", "User"); if (-not $userPath) { $userPath = "" }
if ($userPath -notlike "*$AideBin*") {
    $newPath = if ($userPath) { "$userPath;$AideBin" } else { $AideBin }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "[OK] Added to PATH: $AideBin"
}

Write-Host ""
Write-Host "Aide Agent installed! Restart terminal and type 'aide' to start."
Write-Host ""
Write-Host "Uninstall: aide -Uninstall  (or rerun this script with -Uninstall)"
