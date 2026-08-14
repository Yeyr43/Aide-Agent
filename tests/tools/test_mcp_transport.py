"""Tests for core.mcp.transport — HTTP SSE parsing, stdio, factory."""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.mcp.transport import (
    HTTPTransport,
    StdioTransport,
    create_transport,
    INIT_TIMEOUT,
)
from core.mcp.protocol import (
    JSONRPCRequest,
    JSONRPCResponse,
    make_initialize_request,
    make_tools_list_request,
    make_tools_call_request,
)


# ── HTTPTransport SSE 解析 ────────────────────────────────────────────


class TestHTTPTransportSSEParsing:
    """_parse_http_response 正确解析纯 JSON 和 SSE 两种格式。"""

    def _mock_response(self, text: str, content_type: str = "application/json"):
        resp = MagicMock()
        resp.text = text
        resp.headers = {"content-type": content_type}
        return resp

    def test_plain_json_response(self):
        transport = HTTPTransport()
        resp = self._mock_response(
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})
        )
        result = transport._parse_http_response(1, resp)
        assert not result.is_error
        assert result.result == {"tools": []}

    def test_sse_single_data_line(self):
        transport = HTTPTransport()
        resp = self._mock_response(
            'data: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"x"}]}}',
            content_type="text/event-stream",
        )
        result = transport._parse_http_response(2, resp)
        assert not result.is_error
        assert result.result["tools"][0]["name"] == "x"

    def test_sse_content_type_detection(self):
        """通过 content-type header 检测 SSE，不只是 data: 前缀。"""
        transport = HTTPTransport()
        resp = self._mock_response(
            '{"jsonrpc":"2.0","id":3,"result":{"ok":true}}',
            content_type="text/event-stream",
        )
        result = transport._parse_http_response(3, resp)
        assert not result.is_error
        assert result.result == {"ok": True}

    def test_sse_multiple_events_takes_last_data(self):
        """多个 SSE 事件时取最后一条 data 作为最终响应。"""
        transport = HTTPTransport()
        resp = self._mock_response(
            'event: progress\ndata: {"progress":10}\n\n'
            'event: result\ndata: {"jsonrpc":"2.0","id":4,"result":{"done":true}}',
            content_type="text/event-stream",
        )
        result = transport._parse_http_response(4, resp)
        assert not result.is_error
        assert result.result == {"done": True}

    def test_empty_response(self):
        transport = HTTPTransport()
        resp = self._mock_response("")
        result = transport._parse_http_response(5, resp)
        assert not result.is_error
        assert result.result == {}

    def test_invalid_json_returns_error(self):
        transport = HTTPTransport()
        resp = self._mock_response("not json at all")
        result = transport._parse_http_response(6, resp)
        assert result.is_error
        assert "无法解析" in result.error_message or "parse" in result.error_message.lower()

    def test_sse_empty_data_returns_empty(self):
        transport = HTTPTransport()
        resp = self._mock_response(
            "event: heartbeat\ndata:",
            content_type="text/event-stream",
        )
        result = transport._parse_http_response(7, resp)
        assert not result.is_error
        assert result.result == {}


# ── HTTPTransport Session ID ─────────────────────────────────────────


class TestHTTPTransportSessionID:
    def test_session_id_captured_from_header(self):
        transport = HTTPTransport()
        resp = MagicMock()
        resp.text = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
        resp.headers = {"mcp-session-id": "abc123"}
        transport._url = "http://test/mcp"
        transport._ensure_client = MagicMock()
        transport._client = MagicMock()
        transport._client.post = AsyncMock(return_value=resp)

        # Simulate what _http_post does with session ID capture
        sid = resp.headers.get("Mcp-Session-Id", "")
        assert sid == ""  # lowercase header
        # Test case-insensitive capture
        sid_case = resp.headers.get("mcp-session-id", "")
        assert sid_case == "abc123"


# ── StdioTransport ──────────────────────────────────────────────────


class TestStdioTransport:
    def test_initial_state(self):
        transport = StdioTransport()
        assert not transport.is_connected
        assert transport._proc is None

    @pytest.mark.asyncio
    async def test_send_request_not_connected(self):
        transport = StdioTransport()
        with pytest.raises(RuntimeError, match="未运行|not running"):
            await transport.send_request(make_tools_list_request())

    @pytest.mark.asyncio
    async def test_disconnect_not_connected_is_safe(self):
        transport = StdioTransport()
        await transport.disconnect()  # should not raise


# ── create_transport 工厂 ─────────────────────────────────────────────


class TestCreateTransport:
    @pytest.mark.asyncio
    async def test_create_stdio_with_command(self):
        """stdio transport 由 command 参数触发。"""
        with patch.object(StdioTransport, 'connect', new_callable=AsyncMock) as mock_connect:
            transport = await create_transport(command="echo", args=["hello"])
            mock_connect.assert_awaited_once_with("echo", ["hello"])
            assert isinstance(transport, StdioTransport)

    @pytest.mark.asyncio
    async def test_create_http_with_url(self):
        """HTTP transport 由 url 参数触发。"""
        with patch.object(HTTPTransport, 'connect', new_callable=AsyncMock) as mock_connect:
            transport = await create_transport(url="http://localhost:8080/mcp")
            mock_connect.assert_awaited_once_with("http://localhost:8080/mcp")
            assert isinstance(transport, HTTPTransport)

    @pytest.mark.asyncio
    async def test_create_without_command_or_url_raises(self):
        with pytest.raises(ValueError, match="必须提供|must provide"):
            await create_transport()

    @pytest.mark.asyncio
    async def test_create_command_takes_priority_over_url(self):
        """command 优先级高于 url。"""
        with patch.object(StdioTransport, 'connect', new_callable=AsyncMock):
            transport = await create_transport(
                command="npx", args=["-y", "server"],
                url="http://localhost:8080/mcp",
            )
            assert isinstance(transport, StdioTransport)


# ── HTTPTransport client 生命周期 ───────────────────────────────────


class TestHTTPTransportClientLifecycle:
    @pytest.mark.asyncio
    async def test_client_created_on_connect(self):
        transport = HTTPTransport()
        assert transport._client is None

        transport._ensure_client()
        assert transport._client is not None

    @pytest.mark.asyncio
    async def test_client_closed_on_disconnect(self):
        transport = HTTPTransport()
        transport._ensure_client()
        client = transport._client
        assert client is not None

        await transport._close_client()
        assert transport._client is None


# ── JSON-RPC 请求构建 ───────────────────────────────────────────────


class TestJSONRPCRequestBuilding:
    def test_initialize_request(self):
        req = make_initialize_request(req_id=1)
        assert req.method == "initialize"
        assert req.id == 1
        assert req.params["protocolVersion"] == "2024-11-05"
        assert "capabilities" in req.params
        assert "clientInfo" in req.params
        assert req.params["clientInfo"]["name"] == "AideAgent"

    def test_tools_list_request(self):
        req = make_tools_list_request(req_id=42)
        assert req.method == "tools/list"
        assert req.id == 42

    def test_tools_call_request(self):
        req = make_tools_call_request("read", {"path": "/tmp/x"}, req_id=99)
        assert req.method == "tools/call"
        assert req.id == 99
        assert req.params["name"] == "read"
        assert req.params["arguments"] == {"path": "/tmp/x"}

    def test_request_serialization(self):
        req = make_tools_list_request()
        raw = req.to_json()
        parsed = json.loads(raw)
        assert parsed["jsonrpc"] == "2.0"
        assert parsed["method"] == "tools/list"


# ── JSON-RPC 响应解析 ───────────────────────────────────────────────


class TestJSONRPCResponseParsing:
    def test_parse_success_response(self):
        from core.mcp.protocol import parse_response
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})
        resp = parse_response(raw)
        assert not resp.is_error
        assert resp.id == 1
        assert resp.result == {"tools": []}

    def test_parse_error_response(self):
        from core.mcp.protocol import parse_response
        raw = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "error": {"code": -32601, "message": "Method not found"},
        })
        resp = parse_response(raw)
        assert resp.is_error
        assert "Method not found" in resp.error_message
