<#
Aide Agent Launcher (PowerShell)
Usage: .\aide.ps1           # from project root (before install)
       aide                 # after running install.ps1
       aide --background    # start minimized to tray
       aide --help
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

# Find project root
$projectRoot = $null

# Case 1: script is in project root (e.g., .\aide.ps1 from repo)
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

# Case 3: script is in ~/.aide/bin/, look up two levels
if (-not $projectRoot) {
    $candidate = Join-Path $ScriptDir "..\.."
    if ((Test-Path (Join-Path $candidate "shell\main.py")) -and
        (Test-Path (Join-Path $candidate "pyproject.toml"))) {
        $projectRoot = $candidate
    }
}

if (-not $projectRoot) {
    Write-Error "Cannot find Aide project. Run install.ps1 from the project directory."
    exit 1
}

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
