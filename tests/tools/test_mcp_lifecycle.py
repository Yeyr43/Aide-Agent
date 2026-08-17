"""Tests for core.mcp.lifecycle — HealthMonitor, ConfigWatcher, scan_mcp_directory."""

import json
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from core.mcp.lifecycle import (
    scan_mcp_directory,
    HealthMonitor,
    ConfigWatcher,
    HEALTH_CHECK_INTERVAL,
    RECONNECT_DELAY,
    WATCH_INTERVAL,
)


class TestScanMcpDirectory:
    def test_empty_directory(self, tmp_path):
        result = scan_mcp_directory(str(tmp_path))
        assert result == {}

    def test_missing_directory(self, tmp_path):
        result = scan_mcp_directory(str(tmp_path / "nonexistent"))
        assert result == {}

    def test_single_json_file(self, tmp_path):
        cfg = [{"name": "my-server", "command": "npx", "args": ["-y", "server"]}]
        (tmp_path / "servers.json").write_text(json.dumps(cfg))

        result = scan_mcp_directory(str(tmp_path))
        assert "my-server" in result
        assert result["my-server"]["command"] == "npx"

    def test_multiple_json_files(self, tmp_path):
        (tmp_path / "servers.json").write_text(json.dumps([
            {"name": "srv1", "command": "echo"},
        ]))
        (tmp_path / "tools.json").write_text(json.dumps([
            {"name": "srv2", "command": "cat"},
        ]))

        result = scan_mcp_directory(str(tmp_path))
        assert len(result) == 2
        assert "srv1" in result
        assert "srv2" in result

    def test_skips_non_list_json(self, tmp_path):
        (tmp_path / "bad.json").write_text('{"not": "a list"}')
        result = scan_mcp_directory(str(tmp_path))
        assert result == {}

    def test_skips_invalid_json(self, tmp_path):
        (tmp_path / "bad.json").write_text("not json at all{{{")
        result = scan_mcp_directory(str(tmp_path))
        assert result == {}

    def test_skips_entry_without_name(self, tmp_path):
        (tmp_path / "cfg.json").write_text(json.dumps([
            {"command": "echo"},  # no name
        ]))
        result = scan_mcp_directory(str(tmp_path))
        assert result == {}

    def test_skips_non_json_files(self, tmp_path):
        (tmp_path / "readme.md").write_text("just a readme")
        result = scan_mcp_directory(str(tmp_path))
        assert result == {}


class TestHealthMonitor:
    def test_initial_state(self):
        adapter = MagicMock()
        monitor = HealthMonitor(adapter)
        assert monitor._interval == HEALTH_CHECK_INTERVAL
        assert not monitor.is_running

    def test_custom_interval(self):
        adapter = MagicMock()
        monitor = HealthMonitor(adapter, interval=10.0)
        assert monitor._interval == 10.0

    @pytest.mark.asyncio
    async def test_start_stop(self):
        adapter = MagicMock()
        adapter._transports = {}
        monitor = HealthMonitor(adapter, interval=0.01)
        monitor.start()
        assert monitor.is_running
        # Let the loop run briefly
        await asyncio.sleep(0.05)
        monitor.stop()
        assert not monitor.is_running

    @pytest.mark.asyncio
    async def test_double_start_is_safe(self):
        adapter = MagicMock()
        adapter._transports = {}
        monitor = HealthMonitor(adapter, interval=0.01)
        monitor.start()
        monitor.start()  # double start
        assert monitor.is_running
        await asyncio.sleep(0.05)
        monitor.stop()

    def test_stop_not_started(self):
        adapter = MagicMock()
        monitor = HealthMonitor(adapter)
        monitor.stop()  # should not crash

    @pytest.mark.asyncio
    async def test_detects_unhealthy_transport(self):
        """When a transport is disconnected, reconnect is attempted."""
        adapter = MagicMock()
        transport = MagicMock()
        transport.is_connected = False
        adapter._transports = {"srv1": transport}
        adapter.reconnect = AsyncMock()

        monitor = HealthMonitor(adapter, interval=0.02)
        monitor.start()
        await asyncio.sleep(0.08)
        monitor.stop()

        # Should have attempted reconnect at least once
        assert adapter.reconnect.call_count >= 1

    @pytest.mark.asyncio
    async def test_skips_healthy_transport(self):
        """Healthy transports are not reconnected."""
        adapter = MagicMock()
        transport = MagicMock()
        transport.is_connected = True
        adapter._transports = {"srv1": transport}
        adapter.reconnect = AsyncMock()

        monitor = HealthMonitor(adapter, interval=0.02)
        monitor.start()
        await asyncio.sleep(0.08)
        monitor.stop()

        # No reconnects for healthy transports
        adapter.reconnect.assert_not_called()


class TestConfigWatcher:
    def test_initial_state(self):
        adapter = MagicMock()
        watcher = ConfigWatcher(adapter, "/tmp/mcp")
        assert watcher._mcp_dir == "/tmp/mcp"
        assert watcher._interval == WATCH_INTERVAL
        assert not watcher.is_running

    def test_custom_interval(self):
        adapter = MagicMock()
        watcher = ConfigWatcher(adapter, "/tmp/mcp", interval=3.0)
        assert watcher._interval == 3.0

    @pytest.mark.asyncio
    async def test_stop_not_started(self):
        adapter = MagicMock()
        watcher = ConfigWatcher(adapter, "/tmp/mcp")
        await watcher.stop()  # should not crash


class TestConfigWatcherReload:
    """ConfigWatcher.reload_config — 增量重载新增/删除/重连/禁用。"""

    @staticmethod
    def _make_adapter():
        from core.mcp.adapter import MCPAdapter
        adapter = MCPAdapter()
        adapter.connect = AsyncMock()
        adapter._sync_tools_to_registry = AsyncMock(return_value=0)
        return adapter

    @pytest.mark.asyncio
    async def test_reload_adds_new(self, tmp_path):
        (tmp_path / "servers.json").write_text(json.dumps([
            {"name": "srv1", "command": "echo", "args": ["-x"]},
        ]))
        adapter = self._make_adapter()
        watcher = ConfigWatcher(adapter, str(tmp_path))
        result = await watcher.reload_config()
        assert result == (1, 0, 0)
        adapter.connect.assert_awaited_once_with("srv1")
        cfg = adapter._registry.get("srv1")
        assert cfg.command == "echo"
        assert cfg.args == ["-x"]
        adapter._sync_tools_to_registry.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reload_reconnects_existing(self, tmp_path):
        from core.mcp.adapter import MCPServerConfig
        (tmp_path / "servers.json").write_text(json.dumps([
            {"name": "srv1", "command": "new-cmd"},
        ]))
        adapter = self._make_adapter()
        adapter.add_server(MCPServerConfig(name="srv1", command="old-cmd"))
        watcher = ConfigWatcher(adapter, str(tmp_path))
        result = await watcher.reload_config()
        assert result == (0, 0, 1)
        adapter.connect.assert_awaited_once_with("srv1")

    @pytest.mark.asyncio
    async def test_reload_disconnects_removed(self, tmp_path):
        from core.mcp.adapter import MCPServerConfig
        (tmp_path / "servers.json").write_text(json.dumps([
            {"name": "srv1", "command": "echo"},
        ]))
        adapter = self._make_adapter()
        adapter.add_server(MCPServerConfig(name="srv1", command="echo"))
        adapter.add_server(MCPServerConfig(name="srv2", command="gone"))
        watcher = ConfigWatcher(adapter, str(tmp_path))
        result = await watcher.reload_config()
        assert result == (0, 1, 1)
        assert "srv2" not in adapter._registry.names

    @pytest.mark.asyncio
    async def test_reload_disabled_skips_connect(self, tmp_path):
        (tmp_path / "servers.json").write_text(json.dumps([
            {"name": "srv1", "command": "echo", "enabled": False},
        ]))
        adapter = self._make_adapter()
        watcher = ConfigWatcher(adapter, str(tmp_path))
        result = await watcher.reload_config()
        assert result == (0, 0, 0)
        adapter.connect.assert_not_called()
        adapter._sync_tools_to_registry.assert_not_called()

    @pytest.mark.asyncio
    async def test_reload_connect_failure(self, tmp_path):
        (tmp_path / "servers.json").write_text(json.dumps([
            {"name": "bad", "command": "nope"},
        ]))
        adapter = self._make_adapter()
        adapter.connect = AsyncMock(side_effect=RuntimeError("cannot connect"))
        watcher = ConfigWatcher(adapter, str(tmp_path))
        result = await watcher.reload_config()
        assert result == (0, 0, 0)
        adapter._sync_tools_to_registry.assert_not_called()


class TestConfigWatcherStartStop:
    @pytest.mark.asyncio
    async def test_start_creates_backend(self, tmp_path):
        adapter = MagicMock()
        watcher = ConfigWatcher(adapter, str(tmp_path), interval=0.01)
        watcher.start()
        assert watcher._watcher is not None
        await asyncio.sleep(0.05)
        assert watcher.is_running
        await watcher.stop()
        assert watcher._watcher is None
        assert not watcher.is_running

    @pytest.mark.asyncio
    async def test_start_stops_existing_watcher(self, tmp_path):
        adapter = MagicMock()
        watcher = ConfigWatcher(adapter, str(tmp_path), interval=0.01)
        old = MagicMock()
        watcher._watcher = old
        watcher.start()
        old.stop.assert_called_once()
        await asyncio.sleep(0.01)
        await watcher.stop()
