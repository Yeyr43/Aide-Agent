"""跨平台常量与通用工具 — 零 UI 依赖，可被 shell/ 和 core/ 层直接使用。

平台检测常量集中在此，UI 特定的函数（hide_console、can_use_tray）
保留在 ui/textual_app/platform.py 中。
"""

from __future__ import annotations

import os
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


def user_download_dir() -> Path:
    """返回平台默认下载/桌面目录（用于 /export 等）。

    优先级：XDG_DOWNLOAD_DIR > ~/Downloads > ~/Desktop > ~
    """
    xdg = os.environ.get("XDG_DOWNLOAD_DIR", "")
    if xdg:
        return Path(xdg)

    for candidate in ["Downloads", "Desktop"]:
        p = Path.home() / candidate
        if p.is_dir():
            return p

    return Path.home()
