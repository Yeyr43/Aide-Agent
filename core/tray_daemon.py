"""Aide Tray Daemon — 独立托盘守护进程。

后台常驻（Windows: pythonw.exe 无控制台），管理托盘图标。
"Show Window" → 若 TUI 未运行则打开新终端。
关闭 TUI 终端 ≠ 退出程序，托盘持续运行。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 脚本位于 core/ 包内（PyInstaller bundle 中 sys.frozen 已设，跳过）：
# 1) 移除脚本目录（core/）——否则 `import locale` 解析到 core/locale.py 遮蔽
#    stdlib locale，subprocess 等 stdlib 导入崩溃；
# 2) 注入项目根目录，使 core.* 导入可用。
# 比较用 os.path.normcase：Windows 上 Path.resolve() 返回磁盘实际大小写，
# 而 sys.path[0] 保留启动时传参大小写，直接字符串比较会失配导致 core/ 未移除、
# `import locale` 崩（pythonw 以相对/小写路径拉起时必现）。
if not getattr(sys, "frozen", False):
    _here = Path(__file__).resolve().parent
    _here_norm = os.path.normcase(str(_here))
    sys.path[:] = [p for p in sys.path if os.path.normcase(str(p)) != _here_norm]
    _project_root = _here.parent
    _root_norm = os.path.normcase(str(_project_root))
    if not any(os.path.normcase(str(p)) == _root_norm for p in sys.path):
        sys.path.insert(0, str(_project_root))

import atexit
import subprocess

from PIL import Image

from core.platform import IS_WINDOWS

# ── Windows: 启动即释放控制台 ──
# uv 的 pythonw.exe 是 CUI 子系统（PE subsystem=3，非标准 GUI），进程启动即被
# Windows 分配控制台，Windows Terminal（默认终端）会为其弹出空白命令行。
# 在此立即 FreeConsole 释放，避免每次运行 Aide 都弹空白窗口。
if IS_WINDOWS:
    try:
        import ctypes
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass


def _load_icon() -> Image.Image:
    """加载托盘图标，优先 Aide.ico，回退到程序生成。"""
    from core.resources import get_resource_path
    ico = get_resource_path("Aide.ico")
    if ico.exists():
        return Image.open(ico)
    # Fallback: generate simple icon
    from PIL import ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill=(30, 30, 50, 255), outline=(100, 150, 200, 255), width=2)
    draw.text((24, 22), "A", fill=(180, 220, 255, 255))
    return img


class TrayDaemon:
    """托盘守护进程。"""

    def __init__(self) -> None:
        self._tui_process: subprocess.Popen | None = None
        self._icon = None

    @property
    def _project_root(self) -> Path:
        # bundle 模式：可执行文件所在目录（onedir 下 Aide 二进制位置），
        # 作为 TUI 子进程 cwd；源码模式为项目根。
        if getattr(sys, "frozen", False):
            return Path(sys.executable).parent
        return Path(__file__).parent.parent

    # ── TUI 子进程管理 ──────────────────────────────────────────────────

    def _get_tui_command(self) -> list[str]:
        # bundle 模式：当前可执行文件即 TUI（Aide.exe / Aide），
        # 由 main() 内的 --daemon 分流保证本进程只跑托盘循环。
        if getattr(sys, "frozen", False):
            exe = str(sys.executable)
            if IS_WINDOWS:
                return ["cmd", "/c", f"title Aide Agent && {exe}"]
            return [exe]
        exe = self._project_root / "dist" / "Aide" / ("Aide.exe" if IS_WINDOWS else "Aide")
        if exe.exists():
            if IS_WINDOWS:
                return ["cmd", "/c", f"title Aide Agent && {exe}"]
            return [str(exe)]
        # Source mode
        if IS_WINDOWS:
            return ["cmd", "/c", "title Aide Agent && uv run python core/main.py"]
        return ["uv", "run", "python", "core/main.py"]

    def _spawn_tui(self) -> None:
        if self._tui_process is not None and self._tui_process.poll() is None:
            return
        cmd = self._get_tui_command()
        if IS_WINDOWS:
            self._tui_process = subprocess.Popen(
                cmd, cwd=str(self._project_root),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        else:
            self._tui_process = subprocess.Popen(
                cmd, cwd=str(self._project_root),
                start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

    def _kill_tui(self) -> None:
        killed = False
        if self._tui_process is not None and self._tui_process.poll() is None:
            try:
                self._tui_process.terminate()
                self._tui_process.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                self._tui_process.kill()
            killed = True
        self._tui_process = None
        if killed:
            # TUI 被强杀，atexit 无法清理实例锁 → 托盘负责删除，
            # 否则残留 aide.pid 的 PID 被复用后会误报 "Aide is already running"
            try:
                from core.setup import aide_dir
                (aide_dir() / "aide.pid").unlink(missing_ok=True)
            except OSError:
                pass

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

        icon = _load_icon()
        menu = pystray.Menu(
            pystray.MenuItem("Show Window", self._on_show, default=True),
            pystray.MenuItem("Hide Window", self._on_hide),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit),
        )

        self._icon = pystray.Icon("Aide Agent", icon, menu=menu)
        # 不自动弹窗 — TUI 由 aide 启动脚本在当前终端运行
        self._icon.run()


def main() -> None:
    # Write PID for single-instance detection
    # 用 aide_dir()（兼容 AIDE_HOME）而非 Path.home()/.aide — 与 ensure_daemon
    # 检查的 _DAEMON_LOCK 路径一致，避免每次启动重复拉起 daemon
    from core.setup import aide_dir
    pid_file = aide_dir() / "daemon.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))
    atexit.register(lambda: pid_file.unlink(missing_ok=True))

    TrayDaemon().run()


if __name__ == "__main__":
    main()
