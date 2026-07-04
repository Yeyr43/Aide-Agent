<#
Aide Agent Launcher (PowerShell)
Usage: aide [--background] [--help]
#>
param(
    [switch]$Background,
    [switch]$Help
)

if ($Help) {
    Write-Host "Aide Agent - Local AI Assistant"
    Write-Host ""
    Write-Host "Usage: aide [options]"
    Write-Host "  --background   Start minimized to system tray"
    Write-Host "  --help         Show this help"
    exit 0
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Try to find project root from .project_path file (written by install script)
$projectRoot = $null
$pathFile = Join-Path $ScriptDir "..\.project_path"
if (Test-Path $pathFile) {
    $projectRoot = (Get-Content $pathFile -Raw).Trim()
}

# If no .project_path, try relative to script location
if (-not $projectRoot) {
    $candidate = Join-Path $ScriptDir "..\.."
    if ((Test-Path (Join-Path $candidate "shell\main.py")) -and
        (Test-Path (Join-Path $candidate "pyproject.toml"))) {
        $projectRoot = $candidate
    }
}

if ($projectRoot) {
    # Check for pre-built binary first
    $exePath = Join-Path $projectRoot "dist\Aide\Aide.exe"
    if (Test-Path $exePath) {
        $exeArgs = @()
        if ($Background) { $exeArgs += "--background" }
        & $exePath @exeArgs
        exit $LASTEXITCODE
    }

    # Source mode: uv run from project root
    Push-Location $projectRoot
    try {
        $uvArgs = @("run", "python", "shell/main.py")
        if ($Background) { $uvArgs += "--background" }
        & uv @uvArgs
        exit $LASTEXITCODE
    } finally {
        Pop-Location
    }
}

Write-Error "Cannot find Aide project. Run scripts/install.ps1 from the project directory."
exit 1
