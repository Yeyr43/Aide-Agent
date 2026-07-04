"""跨平台工具 — 平台检测、窗口隐藏、系统能力查询。

集中所有平台特定逻辑，其他地方通过本模块的常量和函数来判断平台。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# ── 平台常量 ────────────────────────────────────────────────────────────

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
CURRENT = sys.platform


def platform_name() -> str:
    """返回当前平台的人类可读名称。"""
    if IS_MACOS:
        return "macOS"
    if IS_LINUX:
        return "Linux"
    if IS_WINDOWS:
        return "Windows"
    return sys.platform


def hide_console() -> bool:
    """隐藏控制台/终端窗口。各平台尽力而为，失败不抛异常。

    Returns:
        bool: 操作是否执行（不代表窗口一定隐藏）
    """
    try:
        if IS_WINDOWS:
            import ctypes
            ctypes.windll.user32.ShowWindow(
                ctypes.windll.kernel32.GetConsoleWindow(), 0)
            return True
        elif IS_MACOS:
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to set visible of '
                 'first process where frontmost is true to false'],
                capture_output=True, timeout=2)
            return True
        elif IS_LINUX:
            # xdotool — 大多数桌面环境可用
            r = subprocess.run(
                ["xdotool", "getactivewindow", "windowminimize"],
                capture_output=True, timeout=2)
            return r.returncode == 0
        return False
    except Exception:
        return False


def user_download_dir() -> Path:
    """返回平台默认下载/桌面目录（用于 /export 等）。

    优先级：XDG_DOWNLOAD_DIR > ~/Downloads > ~/Desktop > ~
    """
    import os

    # Linux: 尝试 XDG
    xdg = os.environ.get("XDG_DOWNLOAD_DIR", "")
    if xdg:
        return Path(xdg)

    # macOS / Linux 通用
    for candidate in ["Downloads", "Desktop"]:
        p = Path.home() / candidate
        if p.is_dir():
            return p

    return Path.home()


def can_use_tray() -> bool:
    """Check if system tray is usable on the current platform.

    On Linux, pystray needs GTK/AppIndicator at runtime.
    Returns False if dependencies are missing, True otherwise.
    Never raises.
    """
    try:
        import pystray  # noqa: F401
        if IS_LINUX:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk  # noqa: F401
        return True
    except (ImportError, ValueError):
        return False
