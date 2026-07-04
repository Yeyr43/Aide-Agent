"""TrayManager — pystray 系统托盘生命周期管理。

pystray.Icon.run() 是阻塞调用，在独立 daemon 线程中运行。
所有菜单回调通过 app.call_from_thread() 桥接到 Textual 的 asyncio 事件循环。

注意：AideApp 已不再使用此模块（托盘由 shell/tray_daemon.py 管理），
此模块仅保留供未来可能的嵌入式场景。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from ..platform import IS_MACOS, IS_LINUX, can_use_tray

if TYPE_CHECKING:
    from textual.app import App

logger = logging.getLogger(__name__)


def _load_icon() -> Image.Image:
    """加载托盘图标，优先 Aide.ico，回退到程序生成。"""
    ico = Path(__file__).parent.parent.parent.parent / "Aide.ico"
    if ico.exists():
        return Image.open(ico)
    # Fallback
    from PIL import ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill=(30, 30, 50, 255), outline=(100, 150, 200, 255), width=2)
    draw.text((24, 22), "A", fill=(180, 220, 255, 255))
    return img


class TrayManager:
    """管理 pystray 托盘图标生命周期。"""

    def __init__(self, app: App) -> None:
        self._app = app
        self._icon = None
        self._thread = None
        self._running = False

    def start(self) -> None:
        """启动托盘图标。macOS 使用独立进程，其他平台使用 daemon 线程。"""
        if self._running:
            return

        try:
            import pystray
        except ImportError:
            logger.warning("pystray 未安装，跳过托盘")
            return

        if not can_use_tray():
            logger.warning(
                "系统托盘不可用。Linux 用户请安装: "
                "sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-appindicator3-0.1"
            )
            return

        icon = _load_icon()

        menu = pystray.Menu(
            pystray.MenuItem("显示窗口", self._on_show, default=True),
            pystray.MenuItem("隐藏到托盘", self._on_hide),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._on_quit),
        )

        self._icon = pystray.Icon(
            "Aide Agent",
            icon,
            menu=menu,
        )

        self._running = True

        if IS_MACOS:
            # macOS: Cocoa run loop 必须在主线程
            # run_detached() 使用 multiprocessing 派生子进程运行 run loop
            self._icon.run_detached()
            logger.info("托盘已启动 (macOS detached)")
        else:
            # Windows / Linux: daemon 线程可行
            self._thread = threading.Thread(target=self._icon.run, daemon=True)
            self._thread.start()
            logger.info("托盘已启动 (thread)")

    def stop(self) -> None:
        """停止托盘图标。"""
        if self._icon:
            self._icon.stop()
            self._running = False
            logger.info("托盘已停止")

    def _on_show(self) -> None:
        """托盘菜单：显示窗口。"""
        try:
            self._app.call_from_thread(self._app.action_restore)
        except Exception as e:
            logger.debug(f"show 回调失败: {e}")

    def _on_hide(self) -> None:
        """托盘菜单：隐藏窗口。"""
        try:
            self._app.call_from_thread(self._app.action_hide_to_tray)
        except Exception as e:
            logger.debug(f"hide 回调失败: {e}")

    def _on_quit(self) -> None:
        """托盘菜单：退出应用。"""
        try:
            self._app.call_from_thread(self._app.exit)
        except Exception as e:
            logger.debug(f"quit 回调失败: {e}")
