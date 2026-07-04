<#
Aide Agent Launcher (PowerShell)
Usage: aide [--background] [--help]

Install: Run scripts/install.ps1 to add aide to PATH.
#>
param(
    [switch]$Background,
    [switch]$Help
)

if ($Help) {
    Write-Host "Aide Agent - Local AI Assistant"
    Write-Host ""
    Write-Host "Usage: aide [options]"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  --background   Start minimized to system tray"
    Write-Host "  --help         Show this help"
    exit 0
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# 1) PyInstaller build: Aide.exe in same dir or ../dist/Aide/
$exePaths = @(
    (Join-Path $ScriptDir "Aide.exe"),
    (Join-Path $ScriptDir "..\dist\Aide\Aide.exe")
)
foreach ($exe in $exePaths) {
    if (Test-Path $exe) {
        $exeArgs = @()
        if ($Background) { $exeArgs += "--background" }
        & $exe @exeArgs
        exit $LASTEXITCODE
    }
}

# 2) Source install: project root with shell/main.py + pyproject.toml
$projectRoot = Join-Path $ScriptDir ".."
if ((Test-Path (Join-Path $projectRoot "shell\main.py")) -and
    (Test-Path (Join-Path $projectRoot "pyproject.toml"))) {
    $uvArgs = @("run", "python", "shell/main.py")
    if ($Background) { $uvArgs += "--background" }
    & uv @uvArgs
    exit $LASTEXITCODE
}

# 3) Try global uv as last resort
try {
    $uvArgs = @("run", "python", "-m", "shell.main")
    if ($Background) { $uvArgs += "--background" }
    & uv @uvArgs
    exit $LASTEXITCODE
} catch {
    Write-Error "Cannot find Aide installation. Run 'uv sync' in the project directory, or download a pre-built release."
    exit 1
}
