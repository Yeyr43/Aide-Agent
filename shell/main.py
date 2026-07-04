"""Aide Agent 入口脚本。

用法:
    uv run python shell/main.py
    aide

启动后自动最小化到系统托盘，点击托盘图标显示窗口。
"""

import asyncio
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


def main() -> None:
    """启动 Aide Agent。"""
    _ensure_event_loop_policy()
    ensure_aide_root()
    app = AideApp()
    app.run()


if __name__ == "__main__":
    main()
