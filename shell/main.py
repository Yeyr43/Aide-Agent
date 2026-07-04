"""Aide Agent 入口脚本。

用法:
    uv run python shell/main.py
    aide
"""

import asyncio
import ctypes
import sys
from pathlib import Path

# 确保项目根目录在 Python path 中（仅开发模式，PyInstaller bundle 中跳过）
from core.resources import is_bundled
if not is_bundled():
    _project_root = Path(__file__).parent.parent
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))

from core.setup import ensure_aide_root
from ui.textual_app.app import AideApp


def _ensure_event_loop_policy() -> None:
    """确保 Windows 上使用 SelectorEventLoop。"""
    if sys.platform == "win32":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass


def _set_console_icon() -> None:
    """设置控制台窗口图标为 Aide.ico（仅 Windows）。"""
    if sys.platform != "win32":
        return
    try:
        ico = Path(__file__).parent.parent / "Aide.ico"
        if not ico.exists():
            return
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        hicon = ctypes.windll.user32.LoadImageW(
            None, str(ico), 1, 0, 0, 0x00000010 | 0x00000040
        )
        if hwnd and hicon:
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
    except Exception:
        pass  # 非关键，静默失败


def main() -> None:
    """启动 Aide Agent。"""
    _ensure_event_loop_policy()
    ensure_aide_root()
    _set_console_icon()
    app = AideApp()
    app.run()


if __name__ == "__main__":
    main()
