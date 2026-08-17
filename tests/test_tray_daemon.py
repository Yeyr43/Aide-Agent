"""Tests for core.tray_daemon — TrayDaemon 的 TUI 子进程管理与锁清理。

回归：托盘 Hide/Quit 强杀 TUI 后必须清理 aide.pid 实例锁，
否则残留 PID 被复用后再次 `aide` 会误报 "Aide is already running"。
"""

import sys
from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch

from core.tray_daemon import TrayDaemon


@pytest.fixture
def tmp_aide(tmp_path, monkeypatch):
    """把 core.setup.aide_dir() 指到 tmp_path，并在其中放一个 aide.pid。"""
    monkeypatch.setattr("core.setup.aide_dir", lambda: tmp_path)
    lock = tmp_path / "aide.pid"
    lock.write_text("12345", encoding="utf-8")
    return tmp_path


def _daemon_with_process():
    """TrayDaemon + 一个"运行中"的 TUI 子进程 mock。"""
    daemon = TrayDaemon()
    proc = MagicMock()
    proc.poll.return_value = None  # 进程存活
    daemon._tui_process = proc
    return daemon, proc


class TestKillTui:
    def test_kill_running_tui_cleans_lock(self, tmp_aide):
        """托盘杀运行中的 TUI → 实例锁被清理。"""
        daemon, proc = _daemon_with_process()
        daemon._kill_tui()
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once()
        assert daemon._tui_process is None
        assert not (tmp_aide / "aide.pid").exists(), "aide.pid 应被清理"

    def test_kill_timeout_forces_kill_and_cleans(self, tmp_aide):
        """terminate 超时 → kill，仍清理锁。"""
        import subprocess
        daemon, proc = _daemon_with_process()
        proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 5)
        daemon._kill_tui()
        proc.kill.assert_called_once()
        assert not (tmp_aide / "aide.pid").exists()

    def test_no_process_does_not_clean_lock(self, tmp_aide):
        """托盘不管理 TUI（_tui_process 为 None）→ 不动锁（用户终端 TUI 的锁仍有效）。"""
        daemon = TrayDaemon()
        daemon._kill_tui()
        assert (tmp_aide / "aide.pid").exists(), "无托盘管理的 TUI 时不应删锁"

    def test_exited_process_does_not_clean_lock(self, tmp_aide):
        """TUI 已自然退出（poll 非 None）→ 不删锁（atexit 已处理或不该由托盘删）。"""
        daemon, proc = _daemon_with_process()
        proc.poll.return_value = 0  # 已退出
        daemon._kill_tui()
        proc.terminate.assert_not_called()
        assert (tmp_aide / "aide.pid").exists()

    def test_clean_lock_oserror_swallowed(self, tmp_aide, monkeypatch):
        """删除锁抛 OSError → 静默。"""
        daemon, proc = _daemon_with_process()
        from unittest.mock import patch as mpatch
        with mpatch("pathlib.Path.unlink", side_effect=OSError):
            daemon._kill_tui()  # 不抛


class TestBundleMode:
    """Bundle 模式（sys.frozen）：托盘以 `Aide --daemon` 运行，TUI 命令直指可执行文件。"""

    def test_project_root_is_executable_dir_when_frozen(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        daemon = TrayDaemon()
        assert daemon._project_root == Path(sys.executable).parent

    def test_get_tui_command_frozen_windows(self, monkeypatch):
        """frozen + Windows：cmd /c title + Aide.exe。"""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr("core.tray_daemon.IS_WINDOWS", True)
        cmd = TrayDaemon()._get_tui_command()
        assert cmd[0] == "cmd"
        assert sys.executable in cmd[2]

    def test_get_tui_command_frozen_posix(self, monkeypatch):
        """frozen + 非 Windows：直接返回可执行文件路径。"""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr("core.tray_daemon.IS_WINDOWS", False)
        cmd = TrayDaemon()._get_tui_command()
        assert cmd == [sys.executable]

    def test_get_tui_command_source_prefers_dist_build(self, tmp_path, monkeypatch):
        """源码模式：dist/Aide 构建产物存在时优先用它，而非 uv 源码。"""
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr("core.tray_daemon.IS_WINDOWS", False)
        root = tmp_path
        dist_exe = root / "dist" / "Aide" / "Aide"
        dist_exe.parent.mkdir(parents=True)
        dist_exe.write_text("")
        # property 无 setter，patch 类级
        monkeypatch.setattr(TrayDaemon, "_project_root", root)
        cmd = TrayDaemon()._get_tui_command()
        assert cmd == [str(dist_exe)]
