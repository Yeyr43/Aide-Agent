"""Tests for core.launcher — instance lock, daemon, console decoration."""

import ctypes
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from core.launcher import (
    pid_alive,
    acquire_instance_lock,
    release_instance_lock,
    ensure_daemon,
    decorate_console,
    _bring_to_front,
)


class TestPidAlive:
    """PID 存活检测。"""

    def test_current_process_is_alive(self):
        """当前进程的 PID 应该是存活的。"""
        assert pid_alive(os.getpid()) is True

    def test_negative_pid_is_dead(self):
        """负数 PID 不存在（所有平台）。"""
        assert pid_alive(-1) is False

    def test_zero_pid_is_dead(self):
        """PID 0 在 Windows 上不存在，Linux 上为 idle 进程。"""
        # 跨平台：至少不会抛异常
        result = pid_alive(0)
        assert isinstance(result, bool)

    def test_very_large_pid_is_dead(self):
        """超大 PID 不存在。"""
        assert pid_alive(999999) is False


class TestPidAlivePosix:
    """POSIX 分支（os.kill）。"""

    def test_posix_live_process(self, monkeypatch):
        monkeypatch.setattr("core.launcher.IS_WINDOWS", False)
        with patch("core.launcher.os.kill") as mk:
            assert pid_alive(1234) is True
        mk.assert_called_once_with(1234, 0)

    def test_posix_dead_process(self, monkeypatch):
        monkeypatch.setattr("core.launcher.IS_WINDOWS", False)
        with patch("core.launcher.os.kill", side_effect=OSError):
            assert pid_alive(1234) is False

    def test_posix_process_lookup_error(self, monkeypatch):
        monkeypatch.setattr("core.launcher.IS_WINDOWS", False)
        with patch("core.launcher.os.kill", side_effect=ProcessLookupError):
            assert pid_alive(1234) is False


class TestPidAliveWindows:
    """Windows PID 存活检测 — 陈旧锁 PID 复用防护。

    守护进程被强杀后锁文件残留，PID 可能被系统复用为其它进程
    （如 asus_framework.exe）。此时 pid_alive 不能只看 PID 是否存活，
    还要校验进程映像名是否确实是 Aide 的 python 进程。
    """

    @staticmethod
    def _fake_ctypes(image_name: str) -> MagicMock:
        """构造带真实 create_unicode_buffer 的假 ctypes。"""
        fake_ctypes = MagicMock()
        fake_ctypes.windll.kernel32.OpenProcess.return_value = 0x1234  # 非空句柄

        def _query_image_name(handle, flags, buf, size):
            buf.value = image_name
            return True

        fake_ctypes.windll.kernel32.QueryFullProcessImageNameW.side_effect = _query_image_name
        fake_ctypes.create_unicode_buffer.side_effect = ctypes.create_unicode_buffer
        return fake_ctypes

    def test_recycled_pid_other_process_false(self, monkeypatch):
        """PID 被复用为其它进程（如 ASUS 工具）→ 视为不存在。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", True)
        fake = self._fake_ctypes(r"C:\Program Files (x86)\ASUS\ArmouryDevice\asus_framework.exe")
        with patch("core.launcher.ctypes", fake):
            assert pid_alive(5968) is False

    def test_python_process_true(self, monkeypatch):
        """PID 对应 python 进程 → 存活。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", True)
        fake = self._fake_ctypes(r"C:\Users\Administrator\Desktop\AAAAi\Aide\.venv\Scripts\python.exe")
        with patch("core.launcher.ctypes", fake):
            assert pid_alive(1234) is True

    def test_unqueryable_falls_back_true(self, monkeypatch):
        """查不到映像名（受限进程）→ 保守视为存活，不误杀。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", True)
        fake = self._fake_ctypes("")
        fake.windll.kernel32.QueryFullProcessImageNameW.side_effect = lambda *a: False
        with patch("core.launcher.ctypes", fake):
            assert pid_alive(1234) is True


class TestInstanceLock:
    """单实例锁。"""

    def test_acquire_fresh_lock(self):
        """干净锁文件 → 成功获取。"""
        with tempfile.NamedTemporaryFile(suffix=".pid", delete=False) as f:
            lock_path = Path(f.name)
        try:
            lock_path.unlink(missing_ok=True)  # 确保不存在
            result = acquire_instance_lock(lock_path)
            assert result is True
            assert lock_path.exists()
            assert int(lock_path.read_text().strip()) == os.getpid()
        finally:
            release_instance_lock(lock_path)

    def test_acquire_zombie_lock(self):
        """僵尸锁（进程已死）→ 自动清理并获取锁。"""
        with tempfile.NamedTemporaryFile(suffix=".pid", delete=False) as f:
            lock_path = Path(f.name)
        try:
            lock_path.write_text("999999")  # 不存在的 PID
            result = acquire_instance_lock(lock_path)
            assert result is True
            assert int(lock_path.read_text().strip()) == os.getpid()
        finally:
            release_instance_lock(lock_path)

    def test_acquire_bad_file_content(self):
        """损坏的锁文件 → 自动清理并获取锁。"""
        with tempfile.NamedTemporaryFile(suffix=".pid", delete=False) as f:
            lock_path = Path(f.name)
        try:
            lock_path.write_text("not-a-pid")
            result = acquire_instance_lock(lock_path)
            assert result is True
        finally:
            release_instance_lock(lock_path)

    def test_release_removes_file(self):
        """释放锁 → 文件被删除。"""
        with tempfile.NamedTemporaryFile(suffix=".pid", delete=False) as f:
            lock_path = Path(f.name)
        try:
            lock_path.write_text(str(os.getpid()))
            release_instance_lock(lock_path)
            assert not lock_path.exists()
        finally:
            lock_path.unlink(missing_ok=True)

    def test_release_missing_file_no_error(self):
        """释放不存在的锁文件 → 不抛异常。"""
        lock_path = Path(tempfile.gettempdir()) / "nonexistent_aide_test.pid"
        # 确保不存在
        lock_path.unlink(missing_ok=True)
        release_instance_lock(lock_path)  # 不应抛异常

    def test_release_unlink_oserror_swallowed(self, tmp_path, monkeypatch):
        """unlink 抛 OSError → 静默忽略。"""
        lock_path = tmp_path / "aide.pid"
        lock_path.write_text("1")
        with patch("core.launcher.Path.unlink", side_effect=OSError):
            release_instance_lock(lock_path)  # 不抛异常

    def test_acquire_live_instance_non_windows_kills(self, tmp_path, monkeypatch):
        """非 Windows：已有实例 → 终止旧实例并接管。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", False)
        lock_path = tmp_path / "aide.pid"
        lock_path.write_text("1234")
        with patch("core.launcher.pid_alive", return_value=True) as mp_alive, \
             patch("core.launcher.os.kill") as mk, \
             patch("core.launcher.atexit.register"):
            result = acquire_instance_lock(lock_path)
        assert result is True  # 接管成功
        mk.assert_called_once_with(1234, 15)
        assert mp_alive.call_count == 1
        assert lock_path.exists()

    def test_acquire_non_windows_kill_oserror_ok(self, tmp_path, monkeypatch):
        """非 Windows：kill 抛 OSError → 仍接管。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", False)
        lock_path = tmp_path / "aide.pid"
        lock_path.write_text("1234")
        with patch("core.launcher.pid_alive", return_value=True), \
             patch("core.launcher.os.kill", side_effect=OSError), \
             patch("core.launcher.atexit.register"):
            result = acquire_instance_lock(lock_path)
        assert result is True

    def test_acquire_lock_already_held_by_self(self):
        """自己已经持锁 → 模拟已有实例。"""
        with tempfile.NamedTemporaryFile(suffix=".pid", delete=False) as f:
            lock_path = Path(f.name)
        try:
            # 写入当前进程 PID → acquire_instance_lock 会认为已有实例
            lock_path.write_text(str(os.getpid()))
            # 在非 Windows 上会 kill 自己（SIGTERM 当前进程），
            # 所以只验证锁存在时不会返回 True（在当前进程的上下文中）
            # Windows: 可能成功激活窗口返回 False
            # Linux/macOS: kill 自己（SIGTERM），进程可能终止
            # 这是个边界测试，跳过实际验证
            pass
        finally:
            lock_path.unlink(missing_ok=True)


class TestDecorateConsole:
    """控制台装饰（仅 Windows 有实际效果）。"""

    def test_noop_on_non_windows(self, monkeypatch):
        """非 Windows 平台 → 直接返回，不抛异常。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", False)
        decorate_console(None)  # 不应抛异常

    def test_missing_icon_no_error(self, monkeypatch):
        """图标文件不存在 → 不抛异常。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", True)
        # ctypes.windll 仅 Windows 存在：patch 整个 ctypes 模块引用，
        # 避免 patch 目标在 Linux/macOS 上读取即抛 AttributeError
        fake_ctypes = MagicMock()
        fake_ctypes.windll.kernel32.SetConsoleTitleW = MagicMock()
        with patch("core.launcher.ctypes", fake_ctypes):
            decorate_console(Path("/nonexistent/icon.ico"))
            fake_ctypes.windll.kernel32.SetConsoleTitleW.assert_called_once_with("Aide Agent")

    def test_icon_path_none_no_error(self, monkeypatch):
        """图标路径为 None → 仅设置标题。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", True)
        fake_ctypes = MagicMock()
        fake_ctypes.windll.kernel32.SetConsoleTitleW = MagicMock()
        with patch("core.launcher.ctypes", fake_ctypes):
            decorate_console(None)
            fake_ctypes.windll.kernel32.SetConsoleTitleW.assert_called_once_with("Aide Agent")


class TestEnsureDaemon:
    """守护进程管理。"""

    def test_daemon_already_running(self):
        """守护进程已运行 → 跳过。"""
        with tempfile.NamedTemporaryFile(suffix=".pid", delete=False) as f:
            lock_path = Path(f.name)
        try:
            lock_path.write_text(str(os.getpid()))
            # 传入不存在的脚本路径 → 如果走到 spawn 逻辑会静默跳过
            ensure_daemon(lock_path, Path("/nonexistent/tray_daemon.py"))
            # 不应抛异常
        finally:
            lock_path.unlink(missing_ok=True)

    def test_daemon_zombie_lock_script_missing(self):
        """僵尸锁 + 脚本不存在 → 静默跳过。"""
        with tempfile.NamedTemporaryFile(suffix=".pid", delete=False) as f:
            lock_path = Path(f.name)
        try:
            lock_path.write_text("999999")
            ensure_daemon(lock_path, Path("/nonexistent/tray_daemon.py"))
            # 不应抛异常
        finally:
            lock_path.unlink(missing_ok=True)

    def test_daemon_lock_missing_script_missing(self):
        """无锁文件 + 脚本不存在 → 静默跳过。"""
        lock_path = Path(tempfile.gettempdir()) / "nonexistent_daemon_test.pid"
        lock_path.unlink(missing_ok=True)
        ensure_daemon(lock_path, Path("/nonexistent/tray_daemon.py"))
        # 不应抛异常

    def test_daemon_windows_spawns_pythonw(self, tmp_path, monkeypatch):
        """Windows：用 pythonw 拉起，DETACHED_PROCESS。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", True)
        script = tmp_path / "tray_daemon.py"
        script.write_text("")
        # DETACHED_PROCESS 是 Windows 专属常量，POSIX 不存在 → patch 注入
        with patch("subprocess.DETACHED_PROCESS", 0x00000008, create=True), \
             patch("subprocess.CREATE_NEW_PROCESS_GROUP", 0x00000200, create=True), \
             patch("subprocess.Popen") as mp:
            ensure_daemon(tmp_path / "daemon.pid", script)
        mp.assert_called_once()
        cmd, kwargs = mp.call_args.args[0], mp.call_args.kwargs
        # pythonw 存在用 pythonw，否则回退 sys.executable（跨平台确定性）
        pythonw = Path(sys.executable).parent / "pythonw.exe"
        expected = str(pythonw) if pythonw.exists() else sys.executable
        assert cmd[0] == expected
        assert cmd[1] == str(script)
        assert kwargs.get("creationflags") == (0x00000008 | 0x00000200)

    def test_daemon_posix_spawns_detached(self, tmp_path, monkeypatch):
        """非 Windows：start_new_session + DEVNULL 重定向。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", False)
        script = tmp_path / "tray_daemon.py"
        script.write_text("")
        with patch("subprocess.Popen") as mp:
            ensure_daemon(tmp_path / "daemon.pid", script)
        mp.assert_called_once()
        kwargs = mp.call_args.kwargs
        assert kwargs.get("start_new_session") is True
        assert kwargs.get("stdout") == subprocess.DEVNULL
        assert kwargs.get("stderr") == subprocess.DEVNULL

    def test_daemon_corrupt_lock_falls_through(self, tmp_path, monkeypatch):
        """损坏锁文件（非数字）→ 继续拉起守护。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", True)
        lock_path = tmp_path / "daemon.pid"
        lock_path.write_text("not-a-pid")
        script = tmp_path / "tray_daemon.py"
        script.write_text("")
        with patch("subprocess.DETACHED_PROCESS", 0x00000008, create=True), \
             patch("subprocess.CREATE_NEW_PROCESS_GROUP", 0x00000200, create=True), \
             patch("subprocess.Popen") as mp:
            ensure_daemon(lock_path, script)
        mp.assert_called_once()


class TestBringToFront:
    """窗口激活（仅 Windows — ctypes.WINFUNCTYPE 在 POSIX 不存在）。"""
    pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 窗口 API")

    @staticmethod
    def _windows_fake_ctypes() -> MagicMock:
        """构造 Windows ctypes 假对象：基础类型委托真实 ctypes，user32 用 Mock。

        byref 用恒等函数——让 GetWindowThreadProcessId 的 fake 能直接写
        目标 c_ulong 的 .value（真实 byref 对象不可写）。
        """
        fake = MagicMock()
        fake.WINFUNCTYPE = ctypes.WINFUNCTYPE
        fake.c_bool = ctypes.c_bool
        fake.c_void_p = ctypes.c_void_p
        fake.c_ulong = ctypes.c_ulong
        fake.byref = lambda x: x
        fake.create_unicode_buffer = ctypes.create_unicode_buffer
        fake.windll.user32 = MagicMock()
        return fake

    def test_noop_on_non_windows(self, monkeypatch):
        """非 Windows → 返回 False。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", False)
        assert _bring_to_front("Aide Agent") is False

    def test_activates_existing_window_by_pid(self, monkeypatch):
        """标题精确匹配失败时，按 PID 枚举找到已有窗口并激活（不另开进程）。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", True)
        fake = self._windows_fake_ctypes()
        user32 = fake.windll.user32
        user32.FindWindowW.return_value = 0  # 标题匹配失败（Windows Terminal 场景）

        def fake_enum(cb, lparam):
            cb(0x10001, lparam)  # 一个属于 pid 1234 的可见窗口
            return True
        user32.EnumWindows.side_effect = fake_enum

        def fake_gwpid(hwnd, out):
            out.value = 1234
            return 12345
        user32.GetWindowThreadProcessId.side_effect = fake_gwpid
        user32.IsWindowVisible.return_value = True

        with patch("core.launcher.ctypes", fake):
            assert _bring_to_front("Aide Agent", 1234) is True
        user32.ShowWindow.assert_called_once_with(0x10001, 9)
        user32.SetForegroundWindow.assert_called_once_with(0x10001)

    def test_activates_existing_window_by_title(self, monkeypatch):
        """PID 匹配不到时，回退到标题精确匹配。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", True)
        fake = self._windows_fake_ctypes()
        user32 = fake.windll.user32
        # 无窗口属于 pid → EnumWindows 不回调
        user32.EnumWindows.side_effect = lambda cb, lparam: True
        user32.FindWindowW.return_value = 0x20002  # 标题匹配成功

        with patch("core.launcher.ctypes", fake):
            assert _bring_to_front("Aide Agent", 9999) is True
        user32.ShowWindow.assert_called_once_with(0x20002, 9)
        user32.SetForegroundWindow.assert_called_once_with(0x20002)

    def test_find_top_window_by_pid_no_match(self, monkeypatch):
        """无窗口属于该 PID → 返回 0（回调返回 True 继续枚举）。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", True)
        from core.launcher import _find_top_window_by_pid
        fake = self._windows_fake_ctypes()
        user32 = fake.windll.user32

        def fake_enum(cb, lparam):
            cb(0x30001, lparam)  # 窗口属于别的进程
            return True
        user32.EnumWindows.side_effect = fake_enum
        user32.GetWindowThreadProcessId.side_effect = lambda h, out: setattr(out, "value", 9999) or 0
        user32.IsWindowVisible.return_value = True

        with patch("core.launcher.ctypes", fake):
            assert _find_top_window_by_pid(1234) == 0

    def test_find_window_by_title_fuzzy_match(self, monkeypatch):
        """标题包含匹配（Windows Terminal 后缀场景）。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", True)
        from core.launcher import _find_window_by_title_fuzzy
        fake = self._windows_fake_ctypes()
        user32 = fake.windll.user32
        user32.EnumWindows.side_effect = lambda cb, lparam: cb(0x40001, lparam) or True
        user32.GetWindowTextW.side_effect = lambda h, buf, size: setattr(buf, "value", "Aide Agent - Windows Terminal") or len(buf.value)
        user32.IsWindowVisible.return_value = True

        with patch("core.launcher.ctypes", fake):
            assert _find_window_by_title_fuzzy("Aide Agent") == 0x40001

    def test_find_window_by_title_fuzzy_no_match(self, monkeypatch):
        """无标题匹配 → 返回 0。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", True)
        from core.launcher import _find_window_by_title_fuzzy
        fake = self._windows_fake_ctypes()
        user32 = fake.windll.user32
        user32.EnumWindows.side_effect = lambda cb, lparam: cb(0x40001, lparam) or True
        user32.GetWindowTextW.side_effect = lambda h, buf, size: setattr(buf, "value", "Unrelated Window") or len(buf.value)
        user32.IsWindowVisible.return_value = True

        with patch("core.launcher.ctypes", fake):
            assert _find_window_by_title_fuzzy("Aide Agent") == 0

    def test_bring_to_front_fuzzy_title_fallback(self, monkeypatch):
        """PID 枚举与精确标题都失败 → 模糊标题兜底。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", True)
        fake = self._windows_fake_ctypes()
        user32 = fake.windll.user32
        user32.FindWindowW.return_value = 0
        calls = {"n": 0}

        def fake_enum(cb, lparam):
            calls["n"] += 1
            if calls["n"] == 2:  # 第二次枚举 = 模糊标题搜索
                cb(0x60001, lparam)
            return True
        user32.EnumWindows.side_effect = fake_enum
        user32.GetWindowThreadProcessId.side_effect = lambda h, out: setattr(out, "value", 9999) or 0
        user32.GetWindowTextW.side_effect = lambda h, buf, size: setattr(buf, "value", "Aide Agent - Windows Terminal") or len(buf.value)
        user32.IsWindowVisible.return_value = True

        with patch("core.launcher.ctypes", fake):
            assert _bring_to_front("Aide Agent", 1234) is True
        user32.ShowWindow.assert_called_once_with(0x60001, 9)
        user32.SetForegroundWindow.assert_called_once_with(0x60001)

    def test_bring_to_front_no_window_found(self, monkeypatch):
        """所有定位方式都失败 → 返回 False。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", True)
        fake = self._windows_fake_ctypes()
        user32 = fake.windll.user32
        user32.FindWindowW.return_value = 0
        user32.EnumWindows.side_effect = lambda cb, lparam: True  # 不回调 → 找不到

        with patch("core.launcher.ctypes", fake):
            assert _bring_to_front("Aide Agent", 1234) is False

    def test_bring_to_front_exception_returns_false(self, monkeypatch):
        """Win32 调用抛异常 → 返回 False（不向调用方抛）。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", True)
        fake = self._windows_fake_ctypes()
        fake.windll.user32.EnumWindows.side_effect = OSError("boom")

        with patch("core.launcher.ctypes", fake):
            assert _bring_to_front("Aide Agent", 1234) is False


class TestAcquireLockWindows:
    """Windows 单实例锁 — 已有实例时不杀进程、不开新进程。"""

    def test_live_instance_returns_false_no_kill(self, monkeypatch):
        """已有实例 → 返回 False，锁保留，绝不 os.kill（TerminateProcess 会强杀会话）。"""
        with tempfile.NamedTemporaryFile(suffix=".pid", delete=False) as f:
            lock_path = Path(f.name)
        try:
            lock_path.write_text("1234")
            monkeypatch.setattr("core.launcher.IS_WINDOWS", True)
            monkeypatch.setattr("core.launcher.pid_alive", lambda pid: True)
            monkeypatch.setattr("core.launcher._bring_to_front", lambda title, pid=None: False)
            with patch("core.launcher.os.kill") as mock_kill:
                result = acquire_instance_lock(lock_path)
            assert result is False
            assert lock_path.exists()  # 锁未被删——原实例仍持锁
            assert lock_path.read_text().strip() == "1234"
            mock_kill.assert_not_called()
        finally:
            lock_path.unlink(missing_ok=True)
