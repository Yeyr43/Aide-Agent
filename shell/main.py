"""Aide Agent 入口脚本。

用法:
    uv run python shell/main.py
    uv run python shell/main.py --background    # 后台启动（最小化到托盘）

开发时从项目根目录运行，core/ 和 ui/ 需要在 Python path 中。
"""

import argparse
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
    """确保 Windows 上使用 SelectorEventLoop。

    Python 3.8+ 在 Windows 上默认 ProactorEventLoop，不支持
    asyncio.create_subprocess_shell()，导致 run_shell 工具崩溃。
    macOS/Linux 默认策略无需修改。
    """
    if sys.platform == "win32":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass  # 某些环境可能已经设置过


def main() -> None:
    """启动 Aide Agent Textual 终端。"""
    parser = argparse.ArgumentParser(
        prog="aide",
        description="Aide Agent — 本地个人 AI 管家",
    )
    parser.add_argument(
        "--background", "--tray",
        action="store_true",
        help="启动后最小化到系统托盘",
    )
    args = parser.parse_args()

    _ensure_event_loop_policy()

    # 初始化 ~/.aide/ 目录结构（幂等，含旧配置迁移）
    ensure_aide_root()

    app = AideApp(start_in_tray=args.background)
    app.run()


if __name__ == "__main__":
    main()
