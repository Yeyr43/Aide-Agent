<#
Aide Agent Launcher (PowerShell)
Usage: .\aide.ps1   # from project root (before install)
       aide          # after running install.ps1
#>

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Find project root
$projectRoot = $null

# Case 1: script is in project root
if ((Test-Path (Join-Path $ScriptDir "shell\tray_daemon.py")) -and
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

Push-Location $projectRoot
try {
    # Use pythonw.exe (no console) for the tray daemon
    $pythonHome = Split-Path -Parent (Get-Command python).Source
    $pythonw = Join-Path $pythonHome "pythonw.exe"
    if (-not (Test-Path $pythonw)) {
        # Fallback: use regular python
        $pythonw = (Get-Command python).Source
    }

    Start-Process -WindowStyle Hidden -FilePath $pythonw -ArgumentList "shell/tray_daemon.py"
} finally {
    Pop-Location
}
