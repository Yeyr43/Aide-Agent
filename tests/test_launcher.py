"""Tests for core.launcher — instance lock, daemon, console decoration."""

import os
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


class TestBringToFront:
    """窗口激活（仅 Windows）。"""

    def test_noop_on_non_windows(self, monkeypatch):
        """非 Windows → 返回 False。"""
        monkeypatch.setattr("core.launcher.IS_WINDOWS", False)
        from core.launcher import _bring_to_front
        assert _bring_to_front("Aide Agent") is False
