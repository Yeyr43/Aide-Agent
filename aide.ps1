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

# 0) Set console title and icon before starting
$host.ui.RawUI.WindowTitle = "Aide Agent"

# Set console icon via Win32 (Windows only)
# $IsWindows = pwsh 6+; $env:OS = Windows PowerShell 5.1 fallback
if ($IsWindows -or $env:OS) {
    $icoPath = Join-Path $projectRoot "Aide.ico"
    if (Test-Path $icoPath) {
        Add-Type @"
using System;
using System.Runtime.InteropServices;
public class ConsoleIcon {
    [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow();
    [DllImport("user32.dll")] public static extern IntPtr LoadImage(IntPtr hInst, string name, int type, int cx, int cy, uint fuLoad);
    [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, int wParam, IntPtr lParam);
}
"@
        $hwnd = [ConsoleIcon]::GetConsoleWindow()
        if ($hwnd -ne [IntPtr]::Zero) {
            $hIcon = [ConsoleIcon]::LoadImage([IntPtr]::Zero, $icoPath, 1, 32, 32, 0x10)
            if ($hIcon -ne [IntPtr]::Zero) {
                [ConsoleIcon]::SendMessage($hwnd, 0x80, 0, $hIcon) | Out-Null  # ICON_SMALL
                [ConsoleIcon]::SendMessage($hwnd, 0x80, 1, $hIcon) | Out-Null  # ICON_BIG
            }
        }
    }
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
Push-Location $projectRoot
try {
    & uv run python shell/main.py
} finally {
    Pop-Location
}
