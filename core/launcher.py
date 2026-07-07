"""应用启动工具 — 单实例锁、守护进程、控制台装饰。

从 shell/main.py 提取，独立可测试。
"""

from __future__ import annotations

import atexit
import ctypes
import os
import sys
from pathlib import Path

from core.platform import IS_WINDOWS


# ── 单实例锁 ────────────────────────────────────────────────────────────────

def pid_alive(pid: int) -> bool:
    """检查 PID 是否存活（跨平台）。"""
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


def acquire_instance_lock(lock_file: Path) -> bool:
    """尝试获取单实例锁。

    若已有进程持锁：
      - Windows：激活其窗口并返回 False
      - 非 Windows：终止旧进程并获取锁

    若锁文件指向已死进程（僵尸锁）：自动清理并获取锁。

    Args:
        lock_file: PID 锁文件路径

    Returns:
        True 表示成功获取锁，False 表示已有实例运行
    """
    if lock_file.exists():
        try:
            pid = int(lock_file.read_text().strip())
            if pid_alive(pid):
                if IS_WINDOWS and _bring_to_front("Aide Agent"):
                    return False
                # 非 Windows 无法激活窗口 → 终止旧实例
                try:
                    os.kill(pid, 15)  # SIGTERM
                except OSError:
                    pass
                lock_file.unlink(missing_ok=True)
            else:
                # 僵尸锁
                lock_file.unlink(missing_ok=True)
        except (ValueError, OSError):
            lock_file.unlink(missing_ok=True)

    lock_file.write_text(str(os.getpid()))
    atexit.register(lambda: release_instance_lock(lock_file))
    return True


def release_instance_lock(lock_file: Path) -> None:
    """释放单实例锁。"""
    try:
        lock_file.unlink(missing_ok=True)
    except OSError:
        pass


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


# ── 控制台装饰 ──────────────────────────────────────────────────────────────

def decorate_console(ico_path: Path | None = None) -> None:
    """设置控制台窗口标题和图标（仅 Windows）。"""
    if not IS_WINDOWS:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.SetConsoleTitleW("Aide Agent")

        if ico_path is None:
            return
        if not ico_path.exists():
            return

        hwnd = kernel32.GetConsoleWindow() or user32.FindWindowW(None, "Aide Agent")
        if not hwnd:
            return

        hicon = user32.LoadImageW(None, str(ico_path), 1, 32, 32, 0x00000010)
        if hicon:
            user32.SendMessageW(hwnd, 0x0080, 0, hicon)  # ICON_SMALL
            user32.SendMessageW(hwnd, 0x0080, 1, hicon)  # ICON_BIG
    except Exception:
        pass


# ── 守护进程 ──────────────────────────────────────────────────────────────────

def ensure_daemon(daemon_lock: Path, daemon_script: Path) -> None:
    """确保托盘守护进程在后台运行。已运行则跳过。

    Args:
        daemon_lock: 守护进程 PID 锁文件路径
        daemon_script: tray_daemon.py 的路径
    """
    if daemon_lock.exists():
        try:
            pid = int(daemon_lock.read_text().strip())
            if pid_alive(pid):
                return
        except (ValueError, OSError):
            pass

    import subprocess

    if not daemon_script.exists():
        return

    if IS_WINDOWS:
        pythonw = Path(sys.executable).parent / "pythonw.exe"
        if not pythonw.exists():
            pythonw = sys.executable
        subprocess.Popen(
            [str(pythonw), str(daemon_script)],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        subprocess.Popen(
            [sys.executable, str(daemon_script)],
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
