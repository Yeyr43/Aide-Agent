"""Aide Agent 入口脚本 — 单实例运行。

用法:
    uv run python shell/main.py
    aide

第二次运行 aide 时，不会启动新实例，而是激活已有窗口。
"""

import atexit
import ctypes
import os
import sys
from pathlib import Path

# 确保项目根目录在 Python path 中（仅开发模式，PyInstaller bundle 中跳过）
from core.resources import is_bundled
if not is_bundled():
    _project_root = Path(__file__).parent.parent
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))

from core.setup import aide_dir, ensure_aide_root
from core.platform import IS_WINDOWS

_LOCK_FILE = aide_dir() / "aide.pid"


# ── 单实例锁 ────────────────────────────────────────────────────────────────

def _pid_alive(pid: int) -> bool:
    """检查 PID 是否存活。"""
    try:
        if IS_WINDOWS:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError):
        return False


def _bring_to_front(title: str) -> bool:
    """将已有窗口提到最前。仅 Windows。"""
    if not IS_WINDOWS:
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _acquire_lock() -> bool:
    """尝试获取单实例锁。已有人持锁则激活其窗口并返回 False。"""
    if _LOCK_FILE.exists():
        try:
            pid = int(_LOCK_FILE.read_text().strip())
            if _pid_alive(pid):
                # 已有实例运行中 → 激活窗口
                _bring_to_front("Aide Agent")
                return False
            # 僵尸锁（进程已死）→ 删除
            _LOCK_FILE.unlink(missing_ok=True)
        except (ValueError, OSError):
            _LOCK_FILE.unlink(missing_ok=True)

    # 写入自己的 PID
    _LOCK_FILE.write_text(str(os.getpid()))
    atexit.register(_release_lock)
    return True


def _release_lock() -> None:
    """释放单实例锁。"""
    try:
        _LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ── 控制台装饰 ──────────────────────────────────────────────────────────────

def _decorate_console() -> None:
    """设置控制台窗口标题和图标（仅 Windows）。"""
    if not IS_WINDOWS:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.SetConsoleTitleW("Aide Agent")

        ico = Path(__file__).parent.parent / "Aide.ico"
        if not ico.exists():
            return

        hwnd = kernel32.GetConsoleWindow() or user32.FindWindowW(None, "Aide Agent")
        if not hwnd:
            return

        hicon = user32.LoadImageW(None, str(ico), 1, 32, 32, 0x00000010)
        if hicon:
            user32.SendMessageW(hwnd, 0x0080, 0, hicon)  # ICON_SMALL
            user32.SendMessageW(hwnd, 0x0080, 1, hicon)  # ICON_BIG
    except Exception:
        pass


# ── 守护进程 ──────────────────────────────────────────────────────────────────

_DAEMON_LOCK = aide_dir() / "daemon.pid"


def _ensure_daemon() -> None:
    """确保托盘守护进程在后台运行。已运行则跳过。"""
    if _DAEMON_LOCK.exists():
        try:
            pid = int(_DAEMON_LOCK.read_text().strip())
            if _pid_alive(pid):
                return  # 已在运行
        except (ValueError, OSError):
            pass

    import subprocess
    daemon = Path(__file__).parent / "tray_daemon.py"
    if not daemon.exists():
        return

    if IS_WINDOWS:
        # pythonw: 无控制台窗口
        pythonw = Path(sys.executable).parent / "pythonw.exe"
        if not pythonw.exists():
            pythonw = sys.executable
        subprocess.Popen(
            [str(pythonw), str(daemon)],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        subprocess.Popen(
            [sys.executable, str(daemon)],
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


# ── 入口 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ensure_aide_root()

    if not _acquire_lock():
        print("Aide is already running. Activated existing window.")
        return

    _decorate_console()
    _ensure_daemon()

    from ui.textual_app.app import AideApp
    app = AideApp()
    app.run()


if __name__ == "__main__":
    main()
