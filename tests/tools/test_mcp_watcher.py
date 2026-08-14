"""Tests for core.mcp.watcher — FileWatcher directory polling."""

import asyncio
import pytest
import json
from pathlib import Path

from core.mcp.watcher import FileWatcher


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
