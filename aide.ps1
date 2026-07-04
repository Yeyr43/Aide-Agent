<#
Aide Agent Launcher
Usage: aide   # current terminal becomes Aide TUI, daemon stays in tray
#>
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Find project root
$projectRoot = $null
if ((Test-Path (Join-Path $ScriptDir "shell\tray_daemon.py")) -and
    (Test-Path (Join-Path $ScriptDir "pyproject.toml"))) {
    $projectRoot = $ScriptDir
}
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

# 1) Start daemon in background (if not already running)
$daemonRunning = Get-Process -Name "pythonw" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*tray_daemon*" }
if (-not $daemonRunning) {
    $pythonHome = Split-Path -Parent (Get-Command python).Source
    $pythonw = Join-Path $pythonHome "pythonw.exe"
    if (-not (Test-Path $pythonw)) { $pythonw = (Get-Command python).Source }
    Start-Process -WindowStyle Hidden -FilePath $pythonw -ArgumentList "shell/tray_daemon.py" -WorkingDirectory $projectRoot
}

# 2) Run TUI in current terminal
$host.ui.RawUI.WindowTitle = "Aide Agent"
Push-Location $projectRoot
try {
    & uv run python shell/main.py
} finally {
    Pop-Location
}
