"""Tests for core.main — 入口门控（daemon / 单实例 / 调试开关）。

不启动真实 TUI，mock AideApp.run 验证 main() 的控制流。
"""

import sys
from unittest.mock import patch

import pytest

from core.main import main


class TestMainEntry:
    def test_default_starts_daemon_and_app(self):
        """默认：ensure_daemon 被调、AideApp.run 被调。"""
        with patch("core.main.ensure_aide_root") as mroot, \
             patch("core.main.acquire_instance_lock", return_value=True), \
             patch("core.main.decorate_console"), \
             patch("core.main.ensure_daemon") as mdaemon, \
             patch("ui.textual_app.app.AideApp") as App:
            main()
        mroot.assert_called_once()
        mdaemon.assert_called_once()
        App.return_value.run.assert_called_once()

    def test_no_daemon_skips_ensure_daemon(self, capsys):
        """--no-daemon：不拉守护，打印跳过提示，TUI 照常启动。"""
        with patch.object(sys, "argv", ["core/main.py", "--no-daemon"]), \
             patch("core.main.ensure_aide_root"), \
             patch("core.main.acquire_instance_lock", return_value=True), \
             patch("core.main.decorate_console"), \
             patch("core.main.ensure_daemon") as mdaemon, \
             patch("ui.textual_app.app.AideApp") as App:
            main()
        mdaemon.assert_not_called()
        App.return_value.run.assert_called_once()
        assert "tray daemon skipped" in capsys.readouterr().out

    def test_already_running_no_app(self, capsys):
        """实例锁被占：打印已运行，不启动 AideApp。"""
        with patch("core.main.ensure_aide_root"), \
             patch("core.main.acquire_instance_lock", return_value=False), \
             patch("ui.textual_app.app.AideApp") as App:
            main()
        App.assert_not_called()
        assert "already running" in capsys.readouterr().out

    def test_smoke_test_routes(self):
        """--smoke-test：走 _smoke_test 分支。"""
        with patch.object(sys, "argv", ["core/main.py", "--smoke-test"]), \
             patch("core.main._smoke_test") as msmoke:
            main()
        msmoke.assert_called_once()
