<#
Aide Agent 安装脚本 — 将 aide 命令添加到 PATH
用法: powershell -ExecutionPolicy Bypass -File scripts/install.ps1
#>
param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Join-Path $ScriptDir ".."
$BinDir = Join-Path $ProjectRoot "bin"

# 目标: ~/.aide/bin/
$AideHome = if ($env:AIDE_HOME) { $env:AIDE_HOME } else { Join-Path $env:USERPROFILE ".aide" }
$AideBin = Join-Path $AideHome "bin"

if ($Uninstall) {
    Write-Host "卸载 Aide 命令..."
    # 从 PATH 移除
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -like "*$AideBin*") {
        $newPath = ($userPath -split ";" | Where-Object { $_ -ne $AideBin }) -join ";"
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        Write-Host "  已从 PATH 移除: $AideBin"
    }
    # 删除脚本
    if (Test-Path $AideBin) {
        Remove-Item -Recurse -Force $AideBin
        Write-Host "  已删除: $AideBin"
    }
    Write-Host "卸载完成。重新打开终端生效。"
    exit 0
}

Write-Host "安装 Aide 命令..."
Write-Host "  项目: $ProjectRoot"
Write-Host "  目标: $AideBin"

# 复制启动脚本
New-Item -ItemType Directory -Force -Path $AideBin | Out-Null
Copy-Item -Force (Join-Path $BinDir "aide.ps1") $AideBin
Write-Host "  已复制 aide.ps1 → $AideBin"

# 加入用户 PATH
$userPath = [Environment]::GetEnvironmentVariable("Path", "User") ?? ""
if ($userPath -notlike "*$AideBin*") {
    $newPath = if ($userPath) { "$userPath;$AideBin" } else { $AideBin }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "  已加入 PATH: $AideBin"
}

# 同时创建 aide.bat 在项目根（不需要 PATH 配置，直接双击）
$batContent = @"
@echo off
powershell -ExecutionPolicy Bypass -File "$AideBin\aide.ps1" %*
"@
$batContent | Out-File -Encoding ASCII (Join-Path $AideBin "aide.bat")
Write-Host "  已创建 aide.bat → $AideBin"

Write-Host ""
Write-Host "安装完成！重新打开终端后输入 'aide' 即可启动。"
Write-Host "或直接运行: $AideBin\aide.bat"
Write-Host ""
Write-Host "后台模式: aide --background"
Write-Host "卸载: powershell -ExecutionPolicy Bypass -File scripts/install.ps1 -Uninstall"
