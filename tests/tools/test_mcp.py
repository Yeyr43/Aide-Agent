"""Tests for MCP adapter — protocol types, transports, adapter."""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.mcp.protocol import (
    JSONRPCRequest,
    JSONRPCNotification,
    JSONRPCResponse,
    parse_response,
    make_initialize_request,
    make_initialized_notification,
    make_tools_list_request,
    make_tools_call_request,
    MCP_VERSION,
)
from core.mcp.adapter import MCPAdapter, MCPServerConfig, ServerRegistry


# ── Protocol Tests ───────────────────────────────────────────────────


class TestJSONRPCRequest:
    def test_to_json(self):
        req = JSONRPCRequest(method="tools/list", id=1)
        data = req.to_json()
        obj = json.loads(data)
        assert obj["jsonrpc"] == "2.0"
        assert obj["method"] == "tools/list"
        assert obj["id"] == 1

    def test_to_json_with_params(self):
        req = JSONRPCRequest(method="tools/call", params={"name": "read", "arguments": {"path": "/x"}}, id=3)
        data = req.to_json()
        obj = json.loads(data)
        assert obj["params"]["name"] == "read"


class TestJSONRPCNotification:
    def test_to_json_no_id(self):
        notif = JSONRPCNotification(method="notifications/initialized")
        data = notif.to_json()
        obj = json.loads(data)
        assert "id" not in obj
        assert obj["method"] == "notifications/initialized"


class TestJSONRPCResponse:
    def test_parse_success(self):
        resp = parse_response(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}))
        assert resp.id == 1
        assert resp.result == {"tools": []}
        assert not resp.is_error

    def test_parse_error(self):
        resp = parse_response(json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "Invalid"}}))
        assert resp.is_error
        assert "Invalid" in resp.error_message

    def test_is_error_false_on_success(self):
        resp = JSONRPCResponse(id=1, result={"ok": True})
        assert not resp.is_error

    def test_error_message_empty_on_success(self):
        resp = JSONRPCResponse(id=1, result={"ok": True})
        assert resp.error_message == ""


class TestMCPHandshakeMessages:
    def test_initialize_request(self):
        req = make_initialize_request(42)
        obj = json.loads(req.to_json())
        assert obj["method"] == "initialize"
        assert obj["id"] == 42
        assert obj["params"]["protocolVersion"] == MCP_VERSION
        assert "capabilities" in obj["params"]
        assert obj["params"]["clientInfo"]["name"] == "AideAgent"

    def test_initialized_notification(self):
        notif = make_initialized_notification()
        obj = json.loads(notif.to_json())
        assert "id" not in obj
        assert obj["method"] == "notifications/initialized"

    def test_tools_list_request(self):
        req = make_tools_list_request(7)
        obj = json.loads(req.to_json())
        assert obj["method"] == "tools/list"
        assert obj["id"] == 7

    def test_tools_call_request(self):
        req = make_tools_call_request("read_file", {"path": "/tmp/x"}, 99)
        obj = json.loads(req.to_json())
        assert obj["method"] == "tools/call"
        assert obj["params"]["name"] == "read_file"
        assert obj["params"]["arguments"] == {"path": "/tmp/x"}
        assert obj["id"] == 99


# ── Adapter Tests ────────────────────────────────────────────────────


class TestMCPServerConfig:
    def test_stdio_config(self):
        cfg = MCPServerConfig(name="test", command="echo", args=["hello"])
        assert cfg.name == "test"
        assert cfg.command == "echo"
        assert cfg.args == ["hello"]
        assert cfg.url == ""

    def test_http_config(self):
        cfg = MCPServerConfig(name="remote", url="http://localhost:8080/mcp")
        assert cfg.url == "http://localhost:8080/mcp"
        assert cfg.command == ""

    def test_enabled_default(self):
        cfg = MCPServerConfig(name="test")
        assert cfg.enabled is True


class TestMCPAdapter:
    def test_add_server(self):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="test", command="echo"))
        assert "test" in adapter._registry.names

    def test_add_multiple_servers(self):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="a"))
        adapter.add_server(MCPServerConfig(name="b"))
        assert len(adapter.list_servers()) == 2

    def test_remove_server(self):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="test"))
        assert adapter.remove_server("test") is True
        assert len(adapter.list_servers()) == 0

    def test_remove_nonexistent_server(self):
        adapter = MCPAdapter()
        assert adapter.remove_server("nope") is False

    async def test_discover_tools_not_connected(self):
        adapter = MCPAdapter()
        tools = await adapter.discover_tools("nonexistent")
        assert tools == []

    async def test_discover_tools_returns_cached(self):
        adapter = MCPAdapter()
        adapter._tool_cache["test"] = []
        tools = await adapter.discover_tools("test")
        assert tools == []

    def test_connected_servers_empty_initially(self):
        adapter = MCPAdapter()
        assert adapter.connected_servers == []

    async def test_call_tool_not_connected(self):
        adapter = MCPAdapter()
        result = await adapter.call_tool("nope", "tool", {})
        assert "未连接" in result

    async def test_execute_aide_tool_not_mcp_prefix(self):
        adapter = MCPAdapter()
        result = await adapter.execute_aide_tool("read_file", {})
        assert result is None

    async def test_execute_aide_tool_invalid_mcp_name(self):
        adapter = MCPAdapter()
        result = await adapter.execute_aide_tool("mcp_invalid", {})
        assert "无效的 MCP 工具名" in result


class TestMCPAdapterToolParsing:
    """测试工具发现 → Aide ToolDefinition 映射。"""

    def _make_mock_response(self, tools_data: list[dict]) -> dict:
        return type("Response", (), {
            "is_error": False,
            "error_message": "",
            "result": {"tools": tools_data},
        })()

    async def test_discover_tools_maps_names_with_prefix(self, monkeypatch):
        adapter = MCPAdapter()

        # Mock transport
        class MockTransport:
            async def send_request(self, request):
                resp = type("R", (), {
                    "is_error": False,
                    "error_message": "",
                    "result": {
                        "tools": [
                            {
                                "name": "read_file",
                                "description": "Read a file",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "path": {"type": "string"}
                                    },
                                    "required": ["path"],
                                },
                            }
                        ]
                    },
                })()
                return resp

        adapter._transports["fs"] = MockTransport()
        tools = await adapter.discover_tools("fs")

        assert len(tools) == 1
        assert tools[0].name == "mcp_fs_read_file"
        assert "[MCP:fs]" in tools[0].description
        assert tools[0].parameters["type"] == "object"

    async def test_discover_tools_handles_error_response(self, monkeypatch):
        adapter = MCPAdapter()

        class MockTransport:
            async def send_request(self, request):
                resp = type("R", (), {
                    "is_error": True,
                    "error_message": "Server error",
                    "result": {},
                })()
                return resp

        adapter._transports["bad"] = MockTransport()
        tools = await adapter.discover_tools("bad")
        assert tools == []

    async def test_refresh_tools_clears_cache(self):
        adapter = MCPAdapter()
        adapter._tool_cache["test"] = ["cached"]
        adapter._transports["test"] = None  # 会失败，但先测缓存清除

        # refresh 会清除缓存
        assert adapter._tool_cache.get("test") == ["cached"]
        # 然后重新发现会失败（transport 是 None），但缓存已被清除
        result = await adapter.refresh_tools("test")
        # 因为 transport 是 None 且不在 _transports 中（值None≠key不存在？不对，key存在但值是None）
        # 实际上 "test" in adapter._transports 是 True（值是 None）
        # discover_tools 中检查 if name not in self._transports，这里 name 在，所以继续
        # 但 transport 是 None，调用 send_request 会抛 AttributeError
        # 被 catch 后返回 []
        assert result == []

    async def test_call_tool_parses_text_content(self):
        adapter = MCPAdapter()

        class MockTransport:
            async def send_request(self, request, timeout=None):
                resp = type("R", (), {
                    "is_error": False,
                    "error_message": "",
                    "result": {
                        "content": [
                            {"type": "text", "text": "Hello World"}
                        ]
                    },
                })()
                return resp

        adapter._transports["test"] = MockTransport()
        result = await adapter.call_tool("test", "echo", {})
        assert result == "Hello World"

    async def test_call_tool_parses_string_content(self):
        adapter = MCPAdapter()

        class MockTransport:
            async def send_request(self, request, timeout=None):
                resp = type("R", (), {
                    "is_error": False,
                    "error_message": "",
                    "result": {"content": "plain string result"},
                })()
                return resp

        adapter._transports["test"] = MockTransport()
        result = await adapter.call_tool("test", "echo", {})
        assert result == "plain string result"

    async def test_call_tool_handles_timeout(self):
        adapter = MCPAdapter()

        class MockTransport:
            async def send_request(self, request, timeout=None):
                import asyncio
                raise asyncio.TimeoutError("timed out")

        adapter._transports["test"] = MockTransport()
        result = await adapter.call_tool("test", "slow", {})
        assert "超时" in result


class TestMCPToolExecutionBinding:
    """测试 execute_aide_tool 方法的路由逻辑。"""

    async def test_execute_aide_tool_routes_correctly(self):
        adapter = MCPAdapter()
        called_with = {}

        class MockTransport:
            async def send_request(self, request, timeout=None):
                obj = json.loads(request.to_json())
                called_with["server"] = "myfs"
                called_with["tool"] = obj["params"]["name"]
                called_with["args"] = obj["params"]["arguments"]
                resp = type("R", (), {
                    "is_error": False,
                    "error_message": "",
                    "result": {"content": [{"type": "text", "text": "done"}]},
                })()
                return resp

        adapter._transports["myfs"] = MockTransport()
        result = await adapter.execute_aide_tool("mcp_myfs_read_file", {"path": "/tmp/x"})

        assert result == "done"
        assert called_with["tool"] == "read_file"
        assert called_with["args"] == {"path": "/tmp/x"}


class TestMCPCallToolIsError:
    """回归：MCP CallToolResult 工具级失败（isError=true）必须反馈给 LLM，
    不能把失败内容当成功结果喂回。"""

    @staticmethod
    def _adapter_with_transport(response) -> MCPAdapter:
        from core.mcp.adapter import MCPAdapter

        adapter = MCPAdapter()

        class MockResponse:
            is_error = response.get("is_error", False)
            error_message = response.get("error_message", "")
            result = response.get("result", {})

        class MockTransport:
            async def send_request(self, request, timeout=None):
                return MockResponse()

        adapter._transports["srv"] = MockTransport()
        return adapter

    async def test_iserror_surfaces_to_llm(self):
        adapter = self._adapter_with_transport({
            "result": {"isError": True, "content": [{"type": "text", "text": "file not found"}]},
        })
        result = await adapter.call_tool("srv", "read_file", {"path": "/nope"})
        assert "执行失败" in result, result
        assert "file not found" in result

    async def test_success_still_returns_content(self):
        adapter = self._adapter_with_transport({
            "result": {"content": [{"type": "text", "text": "ok content"}]},
        })
        result = await adapter.call_tool("srv", "t", {})
        assert result == "ok content"


async def _noop_connect(name: str) -> None:
    pass


# ── ServerRegistry ────────────────────────────────────────────────────


class TestServerRegistry:
    def test_get_returns_config_or_none(self):
        reg = ServerRegistry()
        assert reg.get("x") is None
        cfg = MCPServerConfig(name="x", command="echo")
        reg.add(cfg)
        assert reg.get("x") is cfg

    def test_get_status_no_transport(self):
        reg = ServerRegistry()
        reg.add(MCPServerConfig(name="x", enabled=True))
        status = reg.get_status("x")
        assert status.transport == "none"
        assert not status.connected
        assert status.enabled
        assert status.tool_count == 0
        assert not status.healthy
        assert not status.circuit_tripped

    def test_get_status_unknown_name_no_transport(self):
        reg = ServerRegistry()
        status = reg.get_status("ghost")
        assert not status.enabled
        assert status.transport == "none"

    def test_get_status_stdio_connected(self):
        from core.mcp.transport import StdioTransport
        reg = ServerRegistry()
        reg.add(MCPServerConfig(name="x", command="echo"))
        transport = StdioTransport()
        transport._connected = True
        transport._proc = MagicMock(returncode=None)
        assert transport.is_connected
        status = reg.get_status("x", transport=transport, tool_count=3, circuit_tripped=True)
        assert status.transport == "stdio"
        assert status.connected
        assert status.healthy
        assert status.tool_count == 3
        assert status.circuit_tripped

    def test_get_status_stdio_disconnected(self):
        from core.mcp.transport import StdioTransport
        reg = ServerRegistry()
        reg.add(MCPServerConfig(name="x", command="echo"))
        transport = StdioTransport()
        status = reg.get_status("x", transport=transport, tool_count=0)
        assert status.transport == "stdio"
        assert not status.connected
        assert not status.healthy

    def test_get_status_http_transport(self):
        from core.mcp.transport import HTTPTransport
        reg = ServerRegistry()
        reg.add(MCPServerConfig(name="h", url="http://x"))
        transport = HTTPTransport()
        transport._connected = True
        transport._url = "http://x"
        status = reg.get_status("h", transport=transport, tool_count=2)
        assert status.transport == "http"
        assert status.connected
        assert status.healthy

    def test_get_all_status(self):
        from core.mcp.transport import HTTPTransport, StdioTransport
        reg = ServerRegistry()
        reg.add(MCPServerConfig(name="a", command="echo"))
        reg.add(MCPServerConfig(name="b", url="http://x"))
        stdio_t = StdioTransport()
        stdio_t._connected = True
        stdio_t._proc = MagicMock(returncode=None)
        http_t = HTTPTransport()
        http_t._connected = True
        http_t._url = "http://x"
        breaker = MagicMock()
        breaker.is_tripped.return_value = True
        statuses = reg.get_all_status(
            {"a": stdio_t, "b": http_t},
            {"a": [MagicMock()]},
            breaker,
        )
        by_name = {s.name: s for s in statuses}
        assert len(by_name) == 2
        assert by_name["a"].transport == "stdio"
        assert by_name["a"].tool_count == 1
        assert by_name["a"].circuit_tripped
        assert by_name["b"].transport == "http"
        assert by_name["b"].tool_count == 0


class TestLoadFromDirectory:
    async def test_loads_and_connects_enabled(self, tmp_path):
        (tmp_path / "servers.json").write_text(json.dumps([
            {"name": "srv1", "command": "echo", "args": ["-x"]},
            {"name": "srv2", "command": "cat", "enabled": False},
            {"name": "srv3", "url": "http://x"},
        ]))
        reg = ServerRegistry()
        connected = []

        async def connect_fn(name):
            connected.append(name)

        count, failed = await reg.load_from_directory(str(tmp_path), connect_fn)
        assert count == 2
        assert failed == []
        assert connected == ["srv1", "srv3"]
        assert set(reg.names) == {"srv1", "srv2", "srv3"}
        assert reg.get("srv3").url == "http://x"
        assert reg.get("srv2").enabled is False

    async def test_empty_directory(self, tmp_path):
        reg = ServerRegistry()
        count, failed = await reg.load_from_directory(str(tmp_path), _noop_connect)
        assert count == 0
        assert failed == []

    async def test_connect_failure_recorded(self, tmp_path):
        (tmp_path / "servers.json").write_text(json.dumps([
            {"name": "bad", "command": "nope"},
        ]))
        reg = ServerRegistry()

        async def fail_fn(name):
            raise RuntimeError("cannot connect")

        count, failed = await reg.load_from_directory(str(tmp_path), fail_fn)
        assert count == 0
        assert failed == ["bad"]
        assert "bad" in reg.names


# ── MCPAdapter 连接管理 ───────────────────────────────────────────────


class TestMCPAdapterConnection:
    async def test_connect_unregistered_raises(self):
        adapter = MCPAdapter()
        with pytest.raises(KeyError):
            await adapter.connect("ghost")

    async def test_connect_disabled_raises(self):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="s", command="echo", enabled=False))
        with pytest.raises(RuntimeError, match="禁用|disabled"):
            await adapter.connect("s")

    async def test_connect_success(self):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="s", command="echo", args=["-x"]))
        transport = MagicMock()
        with patch("core.mcp.adapter.create_transport", new=AsyncMock(return_value=transport)):
            await adapter.connect("s")
        assert adapter._transports["s"] is transport
        assert "s" in adapter.connected_servers

    async def test_connect_resets_breaker_and_redisconnects(self):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="s", command="echo"))
        transport = MagicMock()
        transport.disconnect = AsyncMock()
        adapter._breaker._tripped.add("s")
        with patch("core.mcp.adapter.create_transport", new=AsyncMock(return_value=transport)):
            await adapter.connect("s")
            # already connected → disconnect first, then reconnect
            await adapter.connect("s")
        transport.disconnect.assert_awaited()
        assert "s" not in adapter._breaker._tripped
        assert adapter._breaker._failures.get("s", 0) == 0

    async def test_disconnect_cleans_transports_and_cache(self):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="s", command="echo"))
        transport = MagicMock()
        transport.disconnect = AsyncMock()
        adapter._transports["s"] = transport
        adapter._tool_cache["s"] = [MagicMock()]
        await adapter.disconnect("s")
        transport.disconnect.assert_awaited_once()
        assert "s" not in adapter._transports
        assert "s" not in adapter._tool_cache

    async def test_disconnect_swallows_transport_error(self):
        adapter = MCPAdapter()
        transport = MagicMock()
        transport.disconnect = AsyncMock(side_effect=RuntimeError("boom"))
        adapter._transports["s"] = transport
        await adapter.disconnect("s")  # must not raise

    async def test_reconnect_missing_or_disabled(self):
        adapter = MCPAdapter()
        assert await adapter.reconnect("ghost") is False
        adapter.add_server(MCPServerConfig(name="s", command="echo", enabled=False))
        assert await adapter.reconnect("s") is False

    async def test_reconnect_success(self, monkeypatch):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="s", command="echo"))
        adapter.disconnect = AsyncMock()
        adapter.connect = AsyncMock()
        adapter.refresh_tools = AsyncMock(return_value=[])
        monkeypatch.setattr("core.mcp.adapter.RECONNECT_DELAY", 0)
        assert await adapter.reconnect("s") is True
        adapter.disconnect.assert_awaited_once_with("s")
        adapter.connect.assert_awaited_once_with("s")
        adapter.refresh_tools.assert_awaited_once_with("s")

    async def test_reconnect_connect_fails(self, monkeypatch):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="s", command="echo"))
        adapter.disconnect = AsyncMock()
        adapter.connect = AsyncMock(side_effect=RuntimeError("fail"))
        adapter.refresh_tools = AsyncMock()
        monkeypatch.setattr("core.mcp.adapter.RECONNECT_DELAY", 0)
        assert await adapter.reconnect("s") is False

    async def test_reconnect_disconnect_error_swallowed(self, monkeypatch):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="s", command="echo"))
        adapter.disconnect = AsyncMock(side_effect=Exception("cleanup"))
        adapter.connect = AsyncMock()
        adapter.refresh_tools = AsyncMock(return_value=[])
        monkeypatch.setattr("core.mcp.adapter.RECONNECT_DELAY", 0)
        assert await adapter.reconnect("s") is True

    async def test_get_server_status(self):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="s", command="echo"))
        transport = MagicMock()
        transport.is_connected = True
        adapter._transports["s"] = transport
        adapter._tool_cache["s"] = [MagicMock(), MagicMock()]
        status = adapter.get_server_status("s")
        assert status.connected
        assert status.tool_count == 2
        assert status.healthy
        # MagicMock is not StdioTransport → "http"
        assert status.transport == "http"

    async def test_get_all_status_returns_all(self):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="s", command="echo"))
        adapter.add_server(MCPServerConfig(name="t", command="echo"))
        statuses = adapter.get_all_status()
        assert [s.name for s in statuses] == ["s", "t"]
        assert all(s.transport == "none" for s in statuses)


# ── MCPAdapter 监听 / 注册表同步 ──────────────────────────────────────


class TestMCPAdapterWatcherAndRegistry:
    async def test_reload_config_without_watcher(self):
        adapter = MCPAdapter()
        assert await adapter.reload_config() == (0, 0, 0)

    async def test_reload_config_delegates_to_watcher(self):
        adapter = MCPAdapter()
        mock_watcher = MagicMock()
        mock_watcher.reload_config = AsyncMock(return_value=(1, 2, 3))
        adapter._watcher = mock_watcher
        result = await adapter.reload_config()
        assert result == (1, 2, 3)
        mock_watcher.reload_config.assert_awaited_once()

    def test_start_watcher_sets_dir(self, tmp_path):
        adapter = MCPAdapter()
        mock_watcher = MagicMock()
        with patch("core.mcp.adapter.ConfigWatcher", return_value=mock_watcher):
            adapter.start_watcher(str(tmp_path))
        assert adapter._mcp_dir == str(tmp_path)
        assert adapter._watcher is mock_watcher
        mock_watcher.start.assert_called_once()

    def test_start_watcher_default_dir(self, tmp_path, monkeypatch):
        import pathlib
        monkeypatch.setattr("core.setup.aide_dir", lambda: pathlib.Path(str(tmp_path)))
        adapter = MCPAdapter()
        mock_watcher = MagicMock()
        with patch("core.mcp.adapter.ConfigWatcher", return_value=mock_watcher):
            adapter.start_watcher()
        assert adapter._mcp_dir == str(pathlib.Path(str(tmp_path)) / "mcp")

    async def test_stop_watcher(self):
        adapter = MCPAdapter()
        mock_watcher = MagicMock()
        mock_watcher.stop = AsyncMock()
        adapter._watcher = mock_watcher
        await adapter.stop_watcher()
        mock_watcher.stop.assert_awaited_once()
        assert adapter._watcher is None

    async def test_stop_watcher_none_is_safe(self):
        adapter = MCPAdapter()
        await adapter.stop_watcher()

    def test_set_tool_registry(self):
        adapter = MCPAdapter()
        registry = MagicMock()
        adapter.set_tool_registry(registry)
        assert adapter._tool_registry is registry

    async def test_start_stop_health_check(self):
        adapter = MCPAdapter()
        adapter._health = MagicMock()
        adapter.start_health_check()
        adapter._health.start.assert_called_once()
        adapter.stop_health_check()
        adapter._health.stop.assert_called_once()

    def test_remove_server_ensure_future_runtime_error(self):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="s", command="echo"))
        adapter._transports["s"] = MagicMock()
        adapter.disconnect = MagicMock()  # 避免泄漏未 await 的 coroutine
        with patch("core.mcp.adapter.asyncio.ensure_future", side_effect=RuntimeError("no loop")):
            assert adapter.remove_server("s") is True

    async def test_remove_server_disconnects_and_cleans_mapping(self):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="s", command="echo"))
        transport = MagicMock()
        transport.disconnect = AsyncMock()
        adapter._transports["s"] = transport
        adapter._tool_mapping["mcp_s_t1"] = ("s", "t1")
        adapter._tool_mapping["mcp_other_t"] = ("other", "t")
        assert adapter.remove_server("s") is True
        await asyncio.sleep(0)
        transport.disconnect.assert_awaited_once()
        assert "mcp_s_t1" not in adapter._tool_mapping
        assert "mcp_other_t" in adapter._tool_mapping
        assert "s" not in adapter._transports


class TestMCPAdapterToolRegistrySync:
    async def test_sync_tools_no_registry(self):
        adapter = MCPAdapter()
        assert await adapter._sync_tools_to_registry() == 0

    async def test_sync_tools_atomic_replace(self):
        from core.tools import ToolDefinition
        adapter = MCPAdapter()
        registry = MagicMock()
        registry.list_names.return_value = ["mcp_old", "normal_tool"]
        adapter.set_tool_registry(registry)
        new_tool = ToolDefinition(name="mcp_new", description="d", parameters={})
        adapter.discover_all_tools = AsyncMock(return_value=[new_tool])
        count = await adapter._sync_tools_to_registry()
        assert count == 1
        registry.unregister.assert_called_once_with("mcp_old")
        registry.register.assert_called_once_with(new_tool)

    async def test_discover_all_tools_binds_execute(self):
        from core.tools import ToolDefinition
        adapter = MCPAdapter()
        tdef = ToolDefinition(name="mcp_fs_read_file", description="[MCP:fs] read", parameters={}, execute=None)
        adapter._transports["fs"] = MagicMock()
        adapter._tool_cache["fs"] = [tdef]
        adapter._tool_mapping["mcp_fs_read_file"] = ("fs", "orig_name")
        captured = {}

        async def fake_call(server, tool, args):
            captured["server"] = server
            captured["tool"] = tool
            return "ok"

        adapter.call_tool = fake_call
        tools = await adapter.discover_all_tools()
        assert len(tools) == 1
        assert tools[0].execute is not None
        result = await tools[0].execute({"a": 1})
        assert result == "ok"
        assert captured == {"server": "fs", "tool": "orig_name"}


# ── MCPAdapter 工具发现边界 ──────────────────────────────────────────


class TestMCPAdapterDiscoverEdgeCases:
    async def test_discover_transport_exception(self):
        adapter = MCPAdapter()

        class MockTransport:
            async def send_request(self, request):
                raise ConnectionError("server down")

        adapter._transports["s"] = MockTransport()
        tools = await adapter.discover_tools("s")
        assert tools == []
        assert "s" not in adapter._tool_cache

    async def test_discover_normalizes_params(self):
        adapter = MCPAdapter()
        raw_tools = [
            {"name": "no_type", "inputSchema": {"properties": {"p": {"type": "string"}}}},
            {"name": "not_dict", "inputSchema": ["bad"]},
        ]

        class MockTransport:
            async def send_request(self, request):
                resp = type("R", (), {
                    "is_error": False,
                    "error_message": "",
                    "result": {"tools": raw_tools},
                })()
                return resp

        adapter._transports["s"] = MockTransport()
        tools = await adapter.discover_tools("s")
        by_name = {t.name.removeprefix("mcp_s_"): t for t in tools}
        # inputSchema 无 "type" → 整包成 {"type": "object", "properties": <原 params>}
        assert by_name["no_type"].parameters == {
            "type": "object",
            "properties": {"properties": {"p": {"type": "string"}}},
        }
        # inputSchema 非 dict → 归一为空 properties
        assert by_name["not_dict"].parameters == {
            "type": "object",
            "properties": {},
        }
        # mapping registered
        assert adapter._tool_mapping["mcp_s_no_type"] == ("s", "no_type")


# ── MCPAdapter 工具执行边界 ──────────────────────────────────────────


class TestMCPAdapterCallToolEdgeCases:
    async def test_breaker_tripped_returns_error(self):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="s", command="echo"))
        adapter._breaker._tripped.add("s")
        result = await adapter.call_tool("s", "tool", {})
        assert "已熔断" in result or "熔断" in result

    async def test_reconnect_and_retry_succeeds(self):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="s", command="echo"))

        class MockTransport:
            def __init__(self):
                self.calls = 0

            async def send_request(self, request, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise ConnectionError("broken pipe")
                resp = type("R", (), {
                    "is_error": False,
                    "error_message": "",
                    "result": {"content": [{"type": "text", "text": "recovered"}]},
                })()
                return resp

        adapter._transports["s"] = MockTransport()
        adapter.reconnect = AsyncMock(return_value=True)
        result = await adapter.call_tool("s", "tool", {})
        assert result == "recovered"
        adapter.reconnect.assert_awaited_once_with("s")
        assert adapter._breaker._failures.get("s", 0) == 0

    async def test_reconnect_fails(self):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="s", command="echo"))

        class MockTransport:
            async def send_request(self, request, timeout=None):
                raise RuntimeError("dead")

        adapter._transports["s"] = MockTransport()
        adapter.reconnect = AsyncMock(return_value=False)
        result = await adapter.call_tool("s", "tool", {})
        assert "已断开" in result or "disconnected" in result
        assert adapter._breaker._failures.get("s", 0) == 1

    async def test_reconnect_retry_fails(self):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="s", command="echo"))

        class MockTransport:
            async def send_request(self, request, timeout=None):
                raise ConnectionError("still down")

        adapter._transports["s"] = MockTransport()
        adapter.reconnect = AsyncMock(return_value=True)
        result = await adapter.call_tool("s", "tool", {})
        assert "重连" in result
        assert adapter._breaker._failures.get("s", 0) == 1

    async def test_generic_exception_returns_failure(self):
        adapter = MCPAdapter()

        class MockTransport:
            async def send_request(self, request, timeout=None):
                raise ValueError("bad input")

        adapter._transports["s"] = MockTransport()
        result = await adapter.call_tool("s", "tool", {})
        assert "失败" in result or "failed" in result
        assert adapter._breaker._failures.get("s", 0) == 1

    async def test_error_response_records_failure(self):
        adapter = MCPAdapter()

        class MockTransport:
            async def send_request(self, request, timeout=None):
                resp = type("R", (), {
                    "is_error": True,
                    "error_message": "server blew up",
                    "result": {},
                })()
                return resp

        adapter._transports["s"] = MockTransport()
        result = await adapter.call_tool("s", "tool", {})
        assert "返回错误" in result or "error" in result.lower()
        assert adapter._breaker._failures.get("s", 0) == 1

    async def test_iserror_string_content(self):
        adapter = MCPAdapter()

        class MockTransport:
            async def send_request(self, request, timeout=None):
                resp = type("R", (), {
                    "is_error": False,
                    "error_message": "",
                    "result": {"isError": True, "content": "raw error string"},
                })()
                return resp

        adapter._transports["s"] = MockTransport()
        result = await adapter.call_tool("s", "t", {})
        assert "执行失败" in result
        assert "raw error string" in result

    async def test_mixed_content_blocks(self):
        adapter = MCPAdapter()

        class MockTransport:
            async def send_request(self, request, timeout=None):
                resp = type("R", (), {
                    "is_error": False,
                    "error_message": "",
                    "result": {"content": [
                        {"type": "resource", "resource": {"uri": "file:///x"}},
                        {"type": "image", "data": "base64-data"},
                        "plain string",
                    ]},
                })()
                return resp

        adapter._transports["s"] = MockTransport()
        result = await adapter.call_tool("s", "t", {})
        assert "[Resource:" in result
        assert "base64-data" in result
        assert "plain string" in result

    async def test_non_list_content_json_dump(self):
        adapter = MCPAdapter()

        class MockTransport:
            async def send_request(self, request, timeout=None):
                resp = type("R", (), {
                    "is_error": False,
                    "error_message": "",
                    "result": {"content": None},
                })()
                return resp

        adapter._transports["s"] = MockTransport()
        result = await adapter.call_tool("s", "t", {})
        assert '"content"' in result

    async def test_execute_aide_tool_uses_mapping(self):
        adapter = MCPAdapter()
        adapter._tool_mapping["mcp_srv_renamed"] = ("actual_server", "actual_tool")
        captured = {}

        async def fake_call(server, tool, args):
            captured["server"] = server
            captured["tool"] = tool
            return "mapped"

        adapter.call_tool = fake_call
        result = await adapter.execute_aide_tool("mcp_srv_renamed", {"a": 1})
        assert result == "mapped"
        assert captured == {"server": "actual_server", "tool": "actual_tool"}

    async def test_load_builtin_servers(self, tmp_path):
        adapter = MCPAdapter()
        adapter._registry.load_from_directory = AsyncMock(return_value=(2, ["bad"]))
        count, failed = await adapter.load_builtin_servers(str(tmp_path))
        assert count == 2
        assert failed == ["bad"]
        adapter._registry.load_from_directory.assert_awaited_once_with(str(tmp_path), adapter.connect)

    async def test_load_builtin_servers_default_dir(self, tmp_path, monkeypatch):
        import pathlib
        monkeypatch.setattr("core.setup.aide_dir", lambda: pathlib.Path(str(tmp_path)))
        adapter = MCPAdapter()
        adapter._registry.load_from_directory = AsyncMock(return_value=(0, []))
        await adapter.load_builtin_servers()
        adapter._registry.load_from_directory.assert_awaited_once_with(
            str(pathlib.Path(str(tmp_path)) / "mcp"), adapter.connect
        )
