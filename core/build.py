"""Aide 独立分发构建脚本。

用法:
    uv run python core/build.py  # PyInstaller 构建 + 产物验证 + 启动器/安装脚本
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 脚本位于 core/ 包内：把脚本目录从 sys.path 移除，避免 core/locale.py
# 遮蔽标准库 locale（subprocess/argparse 等 stdlib 依赖 locale，会导致 ImportError）。
# 必须在任何会间接 import locale 的 stdlib 模块之前执行。
# 比较用 os.path.normcase：Windows 上 sys.path[0] 保留启动传参大小写（Git Bash 小写），
# 而 Path.resolve() 返回磁盘实际大小写，直接字符串比较会失配导致 core/ 未被移除。
if not getattr(sys, "frozen", False):
    _here = Path(__file__).resolve().parent
    _here_norm = os.path.normcase(str(_here))
    sys.path[:] = [p for p in sys.path if os.path.normcase(str(p)) != _here_norm]

import argparse
import subprocess

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DIST_DIR = PROJECT_ROOT / "dist"


def run_pyinstaller() -> None:
    """运行 PyInstaller 构建。"""
    spec = PROJECT_ROOT / "Aide.spec"

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", str(spec)]
    print(f"运行: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=True)


def verify_output() -> None:
    """验证构建产物完整性。"""
    dist_dir = DIST_DIR / "Aide"
    exe = dist_dir / "Aide.exe" if sys.platform == "win32" else dist_dir / "Aide"

    if not exe.exists():
        print(f"错误: 未找到可执行文件: {exe}")
        sys.exit(1)

    print(f"可执行文件: {exe} ({exe.stat().st_size / 1024 / 1024:.0f} MB)")

    # 检查内部数据文件
    internal = dist_dir / "_internal"
    checks = {
        "CSS": internal / "ui" / "textual_app" / "app.tcss",
        "插件模板": internal / "core" / "plugins" / "templates" / "hello-plugin",
        "MCP 配置": internal / "mcp" / "servers.json",
    }

    for label, path in checks.items():
        status = "OK" if path.exists() else "缺失"
        print(f"  {label}: {status}")

    # 总大小
    total_mb = sum(f.stat().st_size for f in dist_dir.rglob("*") if f.is_file()) / 1024 / 1024
    print(f"总大小: {total_mb:.0f} MB")
    print(f"\n构建完成: {dist_dir}")


def copy_launchers() -> None:
    """Copy launcher scripts to dist directory.

    注意：macOS 文件系统大小写不敏感，"aide" shell 脚本会覆盖 PyInstaller
    生成的 "Aide" 二进制。因此 macOS 下跳过 shell 脚本复制。
    """
    import shutil
    import platform

    dist_dir = DIST_DIR / "Aide"

    # Windows: copy powershell launcher + create .bat wrapper
    ps1 = PROJECT_ROOT / "aide.ps1"
    if ps1.exists():
        shutil.copy2(ps1, dist_dir / "aide.ps1")
        print("  Copied launcher: aide.ps1")

    if platform.system() == "Windows":
        bat = dist_dir / "aide.bat"
        bat.write_text(
            '@echo off\r\n'
            'powershell -ExecutionPolicy Bypass -File "%~dp0aide.ps1" %*\r\n',
            encoding="ascii",
        )
        print("  Created launcher: aide.bat")

    # macOS/Linux: rename shell launcher to avoid collision with PyInstaller binary
    shell_launcher = PROJECT_ROOT / "aide"
    if shell_launcher.exists():
        dest = dist_dir / "aide.sh"
        shutil.copy2(shell_launcher, dest)
        print("  Copied launcher: aide.sh")

    # 二进制分发安装脚本（跨平台）
    _write_install_scripts(dist_dir)


def _write_install_scripts(dist_dir: Path) -> None:
    """生成二进制分发的安装/卸载脚本。"""
    # Windows: PowerShell 安装脚本
    ps1 = dist_dir / "install.ps1"
    ps1.write_text(r'''# Aide Agent 安装脚本（二进制分发版）
# 用法: powershell -ExecutionPolicy Bypass -File install.ps1
#       powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall

param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$InstallDir = "$env:LOCALAPPDATA\Aide"

if ($Uninstall) {
    Write-Host "Uninstalling Aide..."
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -like "*$InstallDir*") {
        $newPath = ($userPath -split ";" | Where-Object { $_ -ne $InstallDir }) -join ";"
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        Write-Host "  Removed from PATH: $InstallDir"
    }
    if (Test-Path $InstallDir) {
        Remove-Item -Recurse -Force $InstallDir
        Write-Host "  Deleted: $InstallDir"
    }
    Write-Host "Done. Restart terminal to apply."
    exit 0
}

# 复制文件
Write-Host "Installing Aide to $InstallDir..."
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Recurse -Force "$PSScriptRoot\*" -Destination $InstallDir

# 添加 PATH
$userPath = [Environment]::GetEnvironmentVariable("Path", "User") ?? ""
if ($userPath -notlike "*$InstallDir*") {
    $newPath = if ($userPath) { "$userPath;$InstallDir" } else { $InstallDir }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "  Added to PATH: $InstallDir"
}

Write-Host ""
Write-Host "Aide installed! Restart terminal and type 'Aide' to start."
Write-Host "Uninstall: powershell -File $InstallDir\install.ps1 -Uninstall"
''', encoding='utf-8')
    print("  Created install.ps1")

    # Linux/macOS: shell 安装脚本
    sh = dist_dir / "install.sh"
    sh.write_text(r'''#!/usr/bin/env bash
# Aide Agent 安装脚本（二进制分发版）
set -euo pipefail

INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/aide"
BIN_DIR="${HOME}/.local/bin"

if [ "${1:-}" = "--uninstall" ]; then
    echo "Uninstalling Aide..."
    rm -f "$BIN_DIR/aide"
    rm -rf "$INSTALL_DIR"
    echo "Done."
    exit 0
fi

echo "Installing Aide to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR" "$BIN_DIR"
cp -r "$(dirname "$0")"/* "$INSTALL_DIR/"

# symlink to ~/.local/bin
ln -sf "$INSTALL_DIR/Aide" "$BIN_DIR/aide"
echo "  Linked: $BIN_DIR/aide -> $INSTALL_DIR/Aide"

echo ""
echo "Aide installed! Make sure ~/.local/bin is in your PATH, then type 'aide'."
echo "Uninstall: $INSTALL_DIR/install.sh --uninstall"
''', encoding='utf-8')
    sh.chmod(0o755)
    print("  Created install.sh")


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 Aide 独立分发包")
    parser.parse_args()

    # 检查 PyInstaller 是否安装
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller 未安装。运行: uv sync (已包含 pyinstaller 依赖)")
        sys.exit(1)

    run_pyinstaller()
    verify_output()
    copy_launchers()


if __name__ == "__main__":
    main()
