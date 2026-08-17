"""应用启动工具 — 单实例锁、守护进程、控制台装饰。

从入口脚本（core/main.py）提取，独立可测试。
"""

from __future__ import annotations

import atexit
import ctypes
import os
import sys
from pathlib import Path

from core.platform import IS_WINDOWS


# ── 单实例锁 ────────────────────────────────────────────────────────────────

# Aide 进程映像名（无扩展名，小写）——Windows 校验陈旧锁 PID 时用。
# sys.executable.stem 覆盖 PyInstaller 打包的 Aide.exe；python/pythonw 覆盖源码运行。
_AIDE_IMAGE_NAMES: frozenset[str] = frozenset(
    {"python", "pythonw", Path(sys.executable).stem.lower()}
)


def pid_alive(pid: int) -> bool:
    """检查 PID 是否存活（跨平台）。

    负 PID 一律视为不存在：POSIX 的 os.kill(-1, 0) 会检查当前用户的所有
    进程组（返回 True），与"负 pid 是死进程"的语义相悖；Windows 的
    OpenProcess 对负 pid 也会失败。两平台统一提前拦截。

    Windows 额外校验进程映像名必须是 Aide 的 python 进程：守护进程被强杀后
    锁文件残留，PID 可能被系统复用为其它进程（如 asus_framework.exe），
    只看 PID 存活会误判"守护仍在运行"而跳过拉起。映像名对不上 → 视为不存在。
    """
    if pid < 0:
        return False
    try:
        if IS_WINDOWS:
            kernel32 = ctypes.windll.kernel32
            # PROCESS_QUERY_LIMITED_INFORMATION (0x1000) — 允许查询其它进程映像名
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                if not _is_aide_process(handle):
                    return False
            finally:
                kernel32.CloseHandle(handle)
            return True
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError):
        return False


def _is_aide_process(handle: int) -> bool:
    """Windows: 校验进程句柄对应的映像名是否是 Aide 的 python 进程。

    查询失败（受限进程）时保守返回 True——正常持有锁的 Aide 进程不会被
    误判为不存在；ASUS 等非 python 进程的映像名可正常查到，会被正确拦截。
    """
    buf = ctypes.create_unicode_buffer(1024)
    size = ctypes.c_ulong(len(buf))
    if not ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
        return True
    return Path(buf.value).stem.lower() in _AIDE_IMAGE_NAMES


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
                if IS_WINDOWS:
                    # Windows 下即使激活失败也不能 kill：os.kill 在此平台是
                    # TerminateProcess（强杀），会杀掉正在运行的用户会话，
                    # 再启动时表现为"另开一个新进程"。无论激活成败都返回 False。
                    _bring_to_front("Aide Agent", pid)
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


def _find_top_window_by_pid(pid: int) -> int:
    """枚举顶层窗口，返回属于 pid 的第一个可见窗口句柄（0=未找到）。

    conhost 下 Aide 的 python 进程拥有自己的控制台窗口，按 PID 匹配最可靠；
    Windows Terminal 下 python 无顶层窗口，由标题回退路径处理。
    """
    user32 = ctypes.windll.user32
    found: list[int] = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @WNDENUMPROC
    def _callback(hwnd, _lparam):
        wpid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid and user32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
            return False  # 停止枚举
        return True

    user32.EnumWindows(_callback, 0)
    return found[0] if found else 0


def _find_window_by_title_fuzzy(title: str) -> int:
    """枚举顶层窗口，返回标题包含 title 的第一个可见窗口句柄（0=未找到）。

    Windows Terminal 会把控制台标题同步为窗口标题，可能带后缀，精确匹配失败，
    用包含匹配兜底。
    """
    user32 = ctypes.windll.user32
    found: list[int] = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @WNDENUMPROC
    def _callback(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, len(buf))
        if title.lower() in buf.value.lower() and user32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
            return False
        return True

    user32.EnumWindows(_callback, 0)
    return found[0] if found else 0


def _activate_window(hwnd: int) -> None:
    """恢复窗口并抢焦点（临时置顶再取消，绕过 SetForegroundWindow 限制）。"""
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # HWND_TOPMOST(-1) + SWP_NOSIZE|SWP_NOMOVE|SWP_SHOWWINDOW(0x43)
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x43)
    # HWND_NOTOPMOST(-2) + SWP_NOSIZE|SWP_NOMOVE(0x03)
    user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x03)
    user32.SetForegroundWindow(hwnd)


def _bring_to_front(title: str, pid: int | None = None) -> bool:
    """将已有窗口提到最前。仅 Windows。

    定位顺序：按 PID 枚举窗口 → 精确标题 → 模糊标题（Windows Terminal 等
    宿主把控制台标题同步为窗口标题时，精确匹配可能失败）。
    激活失败返回 False——调用方此时应优雅退出，绝不能 kill 旧实例。
    """
    if not IS_WINDOWS:
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = _find_top_window_by_pid(pid) if pid else 0
        if not hwnd:
            hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            hwnd = _find_window_by_title_fuzzy(title)
        if not hwnd:
            return False
        _activate_window(hwnd)
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
    except OSError:
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
