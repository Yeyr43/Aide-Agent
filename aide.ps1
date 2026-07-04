<#
Aide Agent Launcher (PowerShell)
Usage: .\aide.ps1   # from project root (before install)
       aide          # after running install.ps1
#>
param([switch]$Help)

if ($Help) {
    Write-Host "Aide Agent - Local AI Assistant"
    Write-Host "Usage: aide"
    exit 0
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Find project root
$projectRoot = $null

# Case 1: script is in project root
if ((Test-Path (Join-Path $ScriptDir "shell\main.py")) -and
    (Test-Path (Join-Path $ScriptDir "pyproject.toml"))) {
    $projectRoot = $ScriptDir
}

# Case 2: read from ~/.aide/.project_path (installed mode)
if (-not $projectRoot) {
    $pathFile = Join-Path $ScriptDir "..\.project_path"
    if (Test-Path $pathFile) {
        $projectRoot = (Get-Content $pathFile -Raw).Trim()
    }
}

if (-not $projectRoot) {
    Write-Error "Cannot find Aide project. Run install.ps1 from the project directory."
    exit 1
}

# Check for pre-built binary first
$exePath = Join-Path $projectRoot "dist\Aide\Aide.exe"
if (Test-Path $exePath) {
    & $exePath
    exit $LASTEXITCODE
}

# Source mode: uv run from project root
Push-Location $projectRoot
try {
    & uv run python shell/main.py
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
