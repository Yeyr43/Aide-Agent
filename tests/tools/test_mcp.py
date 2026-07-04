"""Tests for MCP adapter — protocol types, transports, adapter."""

import json
import pytest

from core.tools.mcp.protocol import (
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
from core.tools.mcp.adapter import MCPAdapter, MCPServerConfig


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
        assert "test" in adapter._servers

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
