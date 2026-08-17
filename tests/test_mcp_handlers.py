"""Tests for core.commands.builtin.mcp_handlers — /mcp command."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from core.commands.builtin.mcp_handlers import handle_mcp


class TestHandleMCP:
    @pytest.mark.asyncio
    async def test_no_adapter_returns_error(self):
        """Without an MCP adapter, command returns an error message."""
        app = MagicMock()
        app.mcp_adapter = None
        result = await handle_mcp(app, "")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_list_empty(self):
        """List with no servers shows empty message."""
        app = MagicMock()
        adapter = MagicMock()
        adapter.list_servers.return_value = []
        app.mcp_adapter = adapter

        result = await handle_mcp(app, "list")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_list_with_servers(self):
        """List shows server status for each server."""
        app = MagicMock()
        adapter = MagicMock()

        mock_server = MagicMock()
        mock_server.name = "test-server"

        mock_status = MagicMock()
        mock_status.healthy = True
        mock_status.connected = True
        mock_status.circuit_tripped = False
        mock_status.transport = "stdio"
        mock_status.tool_count = 5

        adapter.list_servers.return_value = [mock_server]
        adapter.get_server_status.return_value = mock_status
        app.mcp_adapter = adapter

        result = await handle_mcp(app, "list")
        assert isinstance(result, str)
        assert "test-server" in result

    @pytest.mark.asyncio
    async def test_list_circuit_broken_server(self):
        """Circuit-tripped server shows warning icon."""
        app = MagicMock()
        adapter = MagicMock()

        mock_server = MagicMock()
        mock_server.name = "broken-srv"

        mock_status = MagicMock()
        mock_status.healthy = False
        mock_status.connected = False
        mock_status.circuit_tripped = True
        mock_status.transport = "stdio"
        mock_status.tool_count = 0

        adapter.list_servers.return_value = [mock_server]
        adapter.get_server_status.return_value = mock_status
        app.mcp_adapter = adapter

        result = await handle_mcp(app, "list")
        assert isinstance(result, str)
        assert "broken-srv" in result

    @pytest.mark.asyncio
    async def test_list_connected_not_healthy(self):
        """Connected but unhealthy server shows yellow."""
        app = MagicMock()
        adapter = MagicMock()

        mock_server = MagicMock()
        mock_server.name = "partial-srv"

        mock_status = MagicMock()
        mock_status.healthy = False
        mock_status.connected = True
        mock_status.circuit_tripped = False
        mock_status.transport = "http"
        mock_status.tool_count = 3

        adapter.list_servers.return_value = [mock_server]
        adapter.get_server_status.return_value = mock_status
        app.mcp_adapter = adapter

        result = await handle_mcp(app, "list")
        assert "partial-srv" in result

    @pytest.mark.asyncio
    async def test_list_disconnected(self):
        """Disconnected server shows red."""
        app = MagicMock()
        adapter = MagicMock()

        mock_server = MagicMock()
        mock_server.name = "offline"

        mock_status = MagicMock()
        mock_status.healthy = False
        mock_status.connected = False
        mock_status.circuit_tripped = False
        mock_status.transport = "stdio"
        mock_status.tool_count = 0

        adapter.list_servers.return_value = [mock_server]
        adapter.get_server_status.return_value = mock_status
        app.mcp_adapter = adapter

        result = await handle_mcp(app, "list")
        assert "offline" in result

    @pytest.mark.asyncio
    async def test_connect_missing_name(self):
        """connect without name shows usage."""
        app = MagicMock()
        app.mcp_adapter = MagicMock()

        result = await handle_mcp(app, "connect")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_connect_success(self):
        """connect with valid name succeeds."""
        app = MagicMock()
        adapter = MagicMock()
        adapter.connect = AsyncMock()
        adapter._sync_tools_to_registry = AsyncMock(return_value=3)
        app.mcp_adapter = adapter

        result = await handle_mcp(app, "connect myserver")
        assert isinstance(result, str)
        assert len(result) > 0
        adapter.connect.assert_called_once_with("myserver")

    @pytest.mark.asyncio
    async def test_connect_keyerror(self):
        """connect with unknown name returns error."""
        app = MagicMock()
        adapter = MagicMock()
        adapter.connect = AsyncMock(side_effect=KeyError("unknown"))
        app.mcp_adapter = adapter

        result = await handle_mcp(app, "connect unknown")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_connect_exception(self):
        """connect failure returns error message."""
        app = MagicMock()
        adapter = MagicMock()
        adapter.connect = AsyncMock(side_effect=RuntimeError("connection refused"))
        app.mcp_adapter = adapter

        result = await handle_mcp(app, "connect bad")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_disconnect_missing_name(self):
        """disconnect without name shows usage."""
        app = MagicMock()
        app.mcp_adapter = MagicMock()

        result = await handle_mcp(app, "disconnect")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_disconnect_success(self):
        """disconnect with valid name succeeds."""
        app = MagicMock()
        adapter = MagicMock()
        adapter.disconnect = AsyncMock()
        app.mcp_adapter = adapter

        result = await handle_mcp(app, "disconnect myserver")
        assert isinstance(result, str)
        assert len(result) > 0
        adapter.disconnect.assert_called_once_with("myserver")

    @pytest.mark.asyncio
    async def test_reload(self):
        """reload returns count summary."""
        app = MagicMock()
        adapter = MagicMock()
        adapter.reload_config = AsyncMock(return_value=(2, 1, 1))
        app.mcp_adapter = adapter

        result = await handle_mcp(app, "reload")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_unknown_subcommand(self):
        """Unknown subcommand shows help."""
        app = MagicMock()
        app.mcp_adapter = MagicMock()

        result = await handle_mcp(app, "unknown_cmd")
        assert isinstance(result, str)
        assert len(result) > 0
