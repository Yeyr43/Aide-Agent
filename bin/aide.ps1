<#
Aide Agent 启动脚本 (PowerShell)
用法: aide [--background] [--help]

安装: 将此脚本所在目录加入 PATH，或运行 scripts/install.ps1
#>
param(
    [switch]$Background,
    [switch]$Help
)

if ($Help) {
    Write-Host "Aide Agent — 本地个人 AI 管家"
    Write-Host ""
    Write-Host "用法: aide [选项]"
    Write-Host ""
    Write-Host "选项:"
    Write-Host "  --background   启动后最小化到系统托盘"
    Write-Host "  --help         显示此帮助"
    exit 0
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# 1) PyInstaller 构建产物: aide.exe 在同一目录或 ../dist/Aide/
$exePaths = @(
    Join-Path $ScriptDir "Aide.exe"
    (Join-Path $ScriptDir "..\dist\Aide\Aide.exe")
)
foreach ($exe in $exePaths) {
    if (Test-Path $exe) {
        $args = @()
        if ($Background) { $args += "--background" }
        & $exe @args
        exit $LASTEXITCODE
    }
}

# 2) 源码运行: 项目根有 shell/main.py 和 pyproject.toml
$projectRoot = Join-Path $ScriptDir ".."
if ((Test-Path (Join-Path $projectRoot "shell\main.py")) -and
    (Test-Path (Join-Path $projectRoot "pyproject.toml"))) {
    $args = @("run", "python", "shell/main.py")
    if ($Background) { $args += "--background" }
    & uv @args
    exit $LASTEXITCODE
}

# 3) 回退: 尝试全局 uv
try {
    $args = @("run", "python", "-m", "shell.main")
    if ($Background) { $args += "--background" }
    & uv @args
    exit $LASTEXITCODE
} catch {
    Write-Error "找不到 Aide 安装。请确保已运行 uv sync，或下载了预编译版本。"
    exit 1
}
