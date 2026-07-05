"""跨平台 UI 工具 — 平台检测从 core.platform 重导出，本模块仅 UI 特有逻辑。

平台常量（IS_WINDOWS 等）和通用工具（user_download_dir）已提升至 core/platform.py。
shell/ 和 core/ 层可直接导入 core.platform，无需依赖 ui/。
"""

from __future__ import annotations

import logging
import subprocess
import sys

# 重导出 core 平台常量（向后兼容 — 现有代码仍可 from ui.textual_app.platform import IS_WINDOWS）
from core.platform import (
    IS_WINDOWS,
    IS_MACOS,
    IS_LINUX,
    CURRENT,
    platform_name,
    user_download_dir,
)

logger = logging.getLogger(__name__)

__all__ = [
    "IS_WINDOWS", "IS_MACOS", "IS_LINUX", "CURRENT",
    "platform_name", "user_download_dir",
    "hide_console", "can_use_tray",
]


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
            r = subprocess.run(
                ["xdotool", "getactivewindow", "windowminimize"],
                capture_output=True, timeout=2)
            return r.returncode == 0
        return False
    except Exception:
        logger.debug("Failed to hide console window, skipping")
        return False


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
    except (ImportError, ValueError, Exception):
        return False
