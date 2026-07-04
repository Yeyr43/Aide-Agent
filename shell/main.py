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
    """设置控制台窗口标题和图标（仅 Windows）。"""
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        ico = Path(__file__).parent.parent / "Aide.ico"
        if not ico.exists():
            return

        # 1. 设置标题（先设，后面 FindWindow 要用）
        kernel32.SetConsoleTitleW("Aide Agent")

        # 2. 获取控制台窗口句柄
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            # 回退：通过标题查找
            hwnd = user32.FindWindowW(None, "Aide Agent")
        if not hwnd:
            return

        # 3. 加载图标并设置
        hicon = user32.LoadImageW(None, str(ico), 1, 32, 32, 0x00000010)
        if not hicon:
            return
        user32.SendMessageW(hwnd, 0x0080, 0, hicon)  # ICON_SMALL
        user32.SendMessageW(hwnd, 0x0080, 1, hicon)  # ICON_BIG
    except Exception:
        pass


def main() -> None:
    """启动 Aide Agent。"""
    _ensure_event_loop_policy()
    ensure_aide_root()
    _set_console_icon()
    app = AideApp()
    app.run()


if __name__ == "__main__":
    main()
