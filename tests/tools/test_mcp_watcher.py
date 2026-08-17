"""Tests for core.mcp.watcher — FileWatcher directory polling."""

import asyncio
import pytest
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

from core.mcp.watcher import FileWatcher, PollingBackend, WatchfilesBackend


class TestFileWatcher:
    def test_initial_state(self, tmp_path):
        watcher = FileWatcher(str(tmp_path), on_change=_noop_callback)
        assert not watcher.is_running
        assert watcher._backend._mtimes == {}

    @pytest.mark.asyncio
    async def test_start_stop(self, tmp_path):
        called = []

        async def on_change():
            called.append(1)
            return (1, 1, 1)

        watcher = FileWatcher(str(tmp_path), on_change=on_change, interval=0.05)
        await watcher.start()
        assert watcher.is_running
        await watcher.stop()
        assert not watcher.is_running

    @pytest.mark.asyncio
    async def test_detects_new_json_file(self, tmp_path):
        called = []

        async def on_change():
            called.append(1)
            return (1, 0, 0)

        watcher = FileWatcher(str(tmp_path), on_change=on_change, interval=0.05)
        await watcher.start()

        # Create a new json file
        new_file = tmp_path / "new_server.json"
        new_file.write_text(json.dumps([{"name": "test", "command": "echo"}]))

        # Wait for polling
        await asyncio.sleep(0.2)

        await watcher.stop()
        assert len(called) >= 1

    @pytest.mark.asyncio
    async def test_callback_returns_tuple(self, tmp_path):
        """Callback should return (added, removed, changed) counts."""

        async def on_change():
            return (2, 1, 3)

        watcher = FileWatcher(str(tmp_path), on_change=on_change, interval=0.05)
        await watcher.start()

        # Trigger change
        (tmp_path / "trigger.json").write_text("{}")
        await asyncio.sleep(0.2)

        await watcher.stop()

    @pytest.mark.asyncio
    async def test_no_callback_when_no_change(self, tmp_path):
        called = []

        async def on_change():
            called.append(1)
            return (0, 0, 0)

        watcher = FileWatcher(str(tmp_path), on_change=on_change, interval=0.05)
        await watcher.start()

        # Don't modify anything
        await asyncio.sleep(0.15)

        await watcher.stop()
        # No new files should mean no callback
        assert len(called) == 0

    @pytest.mark.asyncio
    async def test_detects_file_modification(self, tmp_path):
        called = []

        async def on_change():
            called.append(1)
            return (0, 0, 1)

        # Pre-create a file
        existing = tmp_path / "existing.json"
        existing.write_text(json.dumps([{"name": "old"}]))
        await asyncio.sleep(0.01)

        watcher = FileWatcher(str(tmp_path), on_change=on_change, interval=0.05)
        await watcher.start()

        # Modify existing file
        existing.write_text(json.dumps([{"name": "updated"}]))
        await asyncio.sleep(0.2)

        await watcher.stop()
        assert len(called) >= 1

    @pytest.mark.asyncio
    async def test_detects_file_removal(self, tmp_path):
        called = []

        async def on_change():
            called.append(1)
            return (0, 1, 0)

        existing = tmp_path / "removable.json"
        existing.write_text("{}")
        await asyncio.sleep(0.01)

        watcher = FileWatcher(str(tmp_path), on_change=on_change, interval=0.05)
        await watcher.start()

        # Remove file
        existing.unlink()
        await asyncio.sleep(0.2)

        await watcher.stop()
        assert len(called) >= 1

    @pytest.mark.asyncio
    async def test_handles_missing_directory(self, tmp_path):
        """Should not crash when watch directory disappears."""
        called = []

        async def on_change():
            called.append(1)
            return (0, 0, 0)

        nonexistent = tmp_path / "gone"
        watcher = FileWatcher(str(nonexistent), on_change=on_change, interval=0.05)
        await watcher.start()
        await asyncio.sleep(0.1)
        await watcher.stop()
        # Should not crash

    @pytest.mark.asyncio
    async def test_double_start_is_safe(self, tmp_path):
        called = []

        async def on_change():
            called.append(1)
            return (0, 0, 0)

        watcher = FileWatcher(str(tmp_path), on_change=on_change, interval=0.05)
        await watcher.start()
        await watcher.start()  # double start
        assert watcher.is_running
        await watcher.stop()

    @pytest.mark.asyncio
    async def test_stop_not_started_is_safe(self, tmp_path):
        called = []

        async def on_change():
            called.append(1)
            return (0, 0, 0)

        watcher = FileWatcher(str(tmp_path), on_change=on_change)
        await watcher.stop()  # should not crash
        assert not watcher.is_running


async def _noop_callback():
    return (0, 0, 0)


# ── PollingBackend OSError 处理 ───────────────────────────────────────


class _StatOSErrorFile:
    name = "x.json"

    def stat(self):
        raise OSError("permission denied")


class _FakeDir:
    """替身 Path — is_dir 恒真，glob 返回 stat 抛错的文件。"""

    def __init__(self, path):
        self.path = path

    def is_dir(self):
        return True

    def glob(self, pattern):
        return [_StatOSErrorFile()]


class TestPollingBackendOSError:
    @pytest.mark.asyncio
    async def test_start_snapshot_stat_oserror(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.mcp.watcher.Path", _FakeDir)

        async def on_change():
            return (0, 0, 0)

        backend = PollingBackend(interval=0.01)
        await backend.start(str(tmp_path), on_change)
        assert backend._mtimes == {}
        await backend.stop()

    @pytest.mark.asyncio
    async def test_poll_loop_stat_oserror(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.mcp.watcher.Path", _FakeDir)

        async def on_change():
            return (0, 0, 0)

        backend = PollingBackend(interval=0.01)
        await backend.start(str(tmp_path), on_change)
        await asyncio.sleep(0.05)  # 让 poll 循环至少跑一轮
        await backend.stop()


# ── WatchfilesBackend（事件驱动后端）─────────────────────────────────


class TestWatchfilesBackend:
    def test_initial_state(self):
        backend = WatchfilesBackend()
        assert backend._stop_event is None
        assert backend._task is None
        assert not backend.is_running

    @pytest.mark.asyncio
    async def test_start_requires_watchfiles(self, tmp_path):
        backend = WatchfilesBackend()

        async def on_change():
            return (0, 0, 0)

        with pytest.raises(ImportError, match="watchfiles"):
            await backend.start(str(tmp_path), on_change)

    @pytest.mark.asyncio
    async def test_start_stop_with_fake_watchfiles(self, tmp_path, monkeypatch):
        calls = []

        async def fake_awatch(*args, **kwargs):
            yield "changed"
            await asyncio.sleep(60)  # 阻塞直到被取消

        fake_module = MagicMock()
        fake_module.awatch = fake_awatch
        monkeypatch.setitem(sys.modules, "watchfiles", fake_module)

        async def on_change():
            calls.append(1)
            return (0, 0, 0)

        backend = WatchfilesBackend()
        await backend.start(str(tmp_path), on_change)
        assert backend.is_running
        await asyncio.sleep(0.05)
        assert calls, "on_change should be invoked on file change"
        await backend.stop()
        assert not backend.is_running
        assert backend._stop_event is None
        assert backend._task is None

    @pytest.mark.asyncio
    async def test_stop_not_started_is_safe(self):
        backend = WatchfilesBackend()
        await backend.stop()
        assert not backend.is_running
