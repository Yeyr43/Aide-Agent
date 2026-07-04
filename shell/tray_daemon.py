"""Aide Tray Daemon — 独立托盘守护进程。

后台常驻（Windows: pythonw.exe 无控制台），管理托盘图标。
"Show Window" → 弹出 Textual TUI，"Hide" → 关闭 TUI。
关闭 TUI 终端 ≠ 退出程序，托盘持续运行。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw


def _make_icon(size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 4
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(30, 30, 50, 255),
        outline=(100, 150, 200, 255),
        width=2,
    )
    draw.text((size // 2 - 8, size // 2 - 10), "A", fill=(180, 220, 255, 255))
    return img


class TrayDaemon:
    """托盘守护进程 — 唯一的后台常驻进程。"""

    def __init__(self) -> None:
        self._tui_process: subprocess.Popen | None = None
        self._icon = None  # pystray.Icon

    # ── TUI 子进程管理 ──────────────────────────────────────────────────

    def _get_tui_command(self) -> list[str]:
        """返回启动 TUI 的命令行。兼容源码运行和 PyInstaller 打包。"""
        # PyInstaller: dist/Aide/Aide.exe
        exe = Path(__file__).parent.parent / "dist" / "Aide" / "Aide.exe"
        if sys.platform != "win32":
            exe = Path(__file__).parent.parent / "dist" / "Aide" / "Aide"
        if exe.exists():
            return [str(exe)]

        # 源码模式: uv run python shell/main.py
        return ["uv", "run", "python", "shell/main.py"]

    def _spawn_tui(self) -> None:
        if self._tui_process is not None and self._tui_process.poll() is None:
            return  # 已在运行

        cmd = self._get_tui_command()
        proj = Path(__file__).parent.parent

        flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
        self._tui_process = subprocess.Popen(cmd, cwd=str(proj), creationflags=flags)

    def _kill_tui(self) -> None:
        if self._tui_process is None or self._tui_process.poll() is not None:
            self._tui_process = None
            return
        try:
            if sys.platform == "win32":
                self._tui_process.terminate()
            else:
                self._tui_process.send_signal(signal.SIGTERM)
            self._tui_process.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            self._tui_process.kill()
        self._tui_process = None

    # ── 托盘菜单 ────────────────────────────────────────────────────────

    def _on_show(self) -> None:
        self._spawn_tui()

    def _on_hide(self) -> None:
        self._kill_tui()

    def _on_quit(self) -> None:
        self._kill_tui()
        if self._icon:
            self._icon.stop()

    # ── 启动 ────────────────────────────────────────────────────────────

    def run(self) -> None:
        import pystray

        icon = _make_icon()
        menu = pystray.Menu(
            pystray.MenuItem("Show Window", self._on_show, default=True),
            pystray.MenuItem("Hide Window", self._on_hide),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit),
        )

        self._icon = pystray.Icon("Aide Agent", icon, menu=menu)
        self._spawn_tui()  # 默认弹出 TUI
        self._icon.run()


def main() -> None:
    TrayDaemon().run()


if __name__ == "__main__":
    main()
