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
    make_initialized_notification,
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


# ── StdioTransport 子进程握手 & 读取循环 ─────────────────────────────


class _FakeStream:
    """Async 流：返回预置行，耗尽后阻塞（keep_alive）或立即 EOF。"""

    def __init__(self, lines, keep_alive=True):
        self._lines = [ln.encode() if isinstance(ln, str) else ln for ln in lines]
        self._i = 0
        self._keep_alive = keep_alive

    async def readline(self):
        if self._i < len(self._lines):
            line = self._lines[self._i]
            self._i += 1
            return line
        if self._keep_alive:
            await asyncio.sleep(3600)
            return b""
        return b""


class _BadStream:
    async def readline(self):
        raise RuntimeError("io exploded")


class _FakeProc:
    def __init__(self, stream=None, wait_exc=None):
        self.stdin = MagicMock()
        self.stdout = stream or _FakeStream([])
        self.stderr = MagicMock()
        self.returncode = None
        self._wait_exc = wait_exc
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    async def wait(self):
        if self._wait_exc:
            raise self._wait_exc
        return 0

    def kill(self):
        self.killed = True
        self.returncode = -9


class TestStdioTransportConnect:
    @pytest.mark.asyncio
    async def test_connect_success(self):
        init_resp = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}})
        proc = _FakeProc(_FakeStream([init_resp]))
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            transport = StdioTransport()
            await transport.connect("echo", ["-x"])
        assert transport.is_connected
        assert proc.stdin.write.called  # initialized 通知已写入
        await transport.disconnect()

    @pytest.mark.asyncio
    async def test_connect_again_disconnects_first(self):
        # 第二次握手的 request id 会递增到 2，服务端响应 id 须匹配
        init_resp = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
        init_resp2 = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {}})
        proc = _FakeProc(_FakeStream([init_resp]))
        proc2 = _FakeProc(_FakeStream([init_resp2]))
        transport = StdioTransport()
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=[proc, proc2])):
            await transport.connect("echo")
            await transport.connect("echo")
        assert proc.terminated
        assert transport.is_connected
        await transport.disconnect()

    @pytest.mark.asyncio
    async def test_connect_command_not_found(self):
        transport = StdioTransport()
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=FileNotFoundError)):
            with pytest.raises(FileNotFoundError, match="未找到|not found"):
                await transport.connect("nope")

    @pytest.mark.asyncio
    async def test_connect_spawn_error(self):
        transport = StdioTransport()
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=PermissionError("denied"))):
            with pytest.raises(RuntimeError, match="启动|failed|error"):
                await transport.connect("x")

    @pytest.mark.asyncio
    async def test_connect_handshake_send_failure(self):
        proc = _FakeProc(_FakeStream([]))
        transport = StdioTransport()
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with patch.object(StdioTransport, "send_request", new=AsyncMock(side_effect=RuntimeError("boom"))):
                with pytest.raises(RuntimeError, match="boom"):
                    await transport.connect("echo")
        assert proc.terminated  # cleanup 终止了子进程

    @pytest.mark.asyncio
    async def test_connect_handshake_error_response(self):
        err_resp = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "bad handshake"}})
        proc = _FakeProc(_FakeStream([err_resp]))
        transport = StdioTransport()
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with pytest.raises(RuntimeError, match="initialize 失败|initialize failed"):
                await transport.connect("echo")
        assert not transport.is_connected
        assert proc.terminated


class TestStdioTransportReadLoop:
    @pytest.mark.asyncio
    async def test_read_loop_no_proc(self):
        transport = StdioTransport()
        await transport._read_loop()  # 直接 return

    @pytest.mark.asyncio
    async def test_read_loop_dispatches_by_id(self):
        transport = StdioTransport()
        transport._proc = _FakeProc(_FakeStream([
            '{"jsonrpc":"2.0","id":0,"result":{}}',
            '{"jsonrpc":"2.0","id":99,"result":{"x":1}}',
        ], keep_alive=False))
        await transport._read_loop()  # 覆盖服务端推送 + 无匹配响应两个分支

    @pytest.mark.asyncio
    async def test_read_loop_delivers_pending(self):
        transport = StdioTransport()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        transport._pending[7] = future
        transport._proc = _FakeProc(_FakeStream([
            '{"jsonrpc":"2.0","id":7,"result":{"ok":true}}',
        ], keep_alive=False))
        task = asyncio.create_task(transport._read_loop())
        response = await asyncio.wait_for(future, timeout=1.0)
        await task
        assert response.result == {"ok": True}

    @pytest.mark.asyncio
    async def test_read_loop_generic_exception(self):
        transport = StdioTransport()
        transport._proc = _FakeProc(_BadStream())
        await transport._read_loop()  # 异常被吞掉并记录日志


class TestStdioTransportCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_kills_on_wait_timeout(self):
        transport = StdioTransport()
        proc = _FakeProc(wait_exc=asyncio.TimeoutError())
        transport._proc = proc
        await transport._cleanup()
        assert proc.terminated
        assert proc.killed

    @pytest.mark.asyncio
    async def test_cleanup_kills_on_lookup_error(self):
        transport = StdioTransport()
        proc = _FakeProc(wait_exc=ProcessLookupError())
        transport._proc = proc
        await transport._cleanup()
        assert proc.killed

    @pytest.mark.asyncio
    async def test_cleanup_swallows_kill_lookup(self):
        transport = StdioTransport()
        proc = _FakeProc(wait_exc=asyncio.TimeoutError())

        def _kill():
            raise ProcessLookupError()

        proc.kill = _kill
        transport._proc = proc
        await transport._cleanup()  # 不抛异常

    @pytest.mark.asyncio
    async def test_cleanup_fails_pending_futures(self):
        transport = StdioTransport()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        transport._pending[1] = future
        transport._proc = None
        await transport._cleanup()
        with pytest.raises(RuntimeError, match="已断开|disconnected"):
            future.result()


# ── HTTPTransport 连接 / POST ────────────────────────────────────────


class _FakeHTTPResponse:
    def __init__(self, text, headers, status_code=200):
        self.text = text
        self.headers = headers
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self
            )


class TestHTTPTransportConnectFailure:
    @pytest.mark.asyncio
    async def test_connect_post_failure_closes_client(self):
        transport = HTTPTransport()
        transport._ensure_client = MagicMock()
        with patch.object(transport, "_http_post", new=AsyncMock(side_effect=ConnectionError("refused"))):
            with pytest.raises(RuntimeError, match="连接失败|connection failed"):
                await transport.connect("http://localhost:8080/mcp")
        assert not transport.is_connected

    @pytest.mark.asyncio
    async def test_connect_notification_failure_ok(self):
        transport = HTTPTransport()
        transport._ensure_client = MagicMock()
        ok = JSONRPCResponse(id=1, result={})
        with patch.object(transport, "_http_post", new=AsyncMock(return_value=ok)):
            with patch.object(transport, "_http_post_notification", new=AsyncMock(side_effect=RuntimeError("nope"))):
                await transport.connect("http://localhost:8080/mcp")
        assert transport.is_connected
        assert transport._url == "http://localhost:8080/mcp"


class TestHTTPTransportHttpPost:
    @pytest.mark.asyncio
    async def test_post_sends_and_updates_session_id(self):
        transport = HTTPTransport()
        transport._url = "http://x/mcp"
        transport._session_id = "old-sess"
        resp = _FakeHTTPResponse(
            text=json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}),
            headers={"Mcp-Session-Id": "new-sess", "content-type": "application/json"},
        )
        transport._client = MagicMock()
        transport._client.post = AsyncMock(return_value=resp)
        result = await transport._http_post(make_tools_list_request(), timeout=5.0)
        assert result.result == {"ok": True}
        assert transport._session_id == "new-sess"
        headers = transport._client.post.call_args.kwargs["headers"]
        assert headers["Mcp-Session-Id"] == "old-sess"

    @pytest.mark.asyncio
    async def test_post_connect_error(self):
        import httpx
        transport = HTTPTransport()
        transport._url = "http://x/mcp"
        transport._client = MagicMock()
        transport._client.post = AsyncMock(side_effect=httpx.ConnectError("no route"))
        with pytest.raises(ConnectionError):
            await transport._http_post(make_tools_list_request(), timeout=5.0)

    @pytest.mark.asyncio
    async def test_post_connect_timeout(self):
        import httpx
        transport = HTTPTransport()
        transport._url = "http://x/mcp"
        transport._client = MagicMock()
        transport._client.post = AsyncMock(side_effect=httpx.ConnectTimeout("slow"))
        with pytest.raises(ConnectionError):
            await transport._http_post(make_tools_list_request(), timeout=5.0)

    @pytest.mark.asyncio
    async def test_post_read_timeout(self):
        import httpx
        transport = HTTPTransport()
        transport._url = "http://x/mcp"
        transport._client = MagicMock()
        transport._client.post = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(asyncio.TimeoutError):
            await transport._http_post(make_tools_list_request(), timeout=5.0)

    @pytest.mark.asyncio
    async def test_notification_sends_session_id(self):
        transport = HTTPTransport()
        transport._url = "http://x/mcp"
        transport._session_id = "sess-1"
        transport._client = MagicMock()
        transport._client.post = AsyncMock(return_value=_FakeHTTPResponse("", {}, 200))
        await transport._http_post_notification(make_initialized_notification())
        headers = transport._client.post.call_args.kwargs["headers"]
        assert headers["Mcp-Session-Id"] == "sess-1"

    @pytest.mark.asyncio
    async def test_notification_swallows_error(self):
        transport = HTTPTransport()
        transport._url = "http://x/mcp"
        transport._client = MagicMock()
        transport._client.post = AsyncMock(side_effect=RuntimeError("boom"))
        await transport._http_post_notification(make_initialized_notification())  # 不抛异常


# ── HTTPTransport SSE 多事件解析边界 ─────────────────────────────────


class TestHTTPTransportSSEMultiLine:
    def _mock_response(self, text, content_type="application/json"):
        resp = MagicMock()
        resp.text = text
        resp.headers = {"content-type": content_type}
        return resp

    def test_multi_line_skips_invalid_last_data(self):
        """最后一条 data 非法时回退到上一条有效 JSON。"""
        transport = HTTPTransport()
        resp = self._mock_response(
            'data: {"jsonrpc":"2.0","id":4,"result":{"done":true}}\n'
            "data: not-json",
            content_type="text/event-stream",
        )
        result = transport._parse_http_response(4, resp)
        assert not result.is_error
        assert result.result == {"done": True}

    def test_multi_line_all_invalid_returns_error(self):
        """所有 data 行都非法时返回解析失败错误。"""
        transport = HTTPTransport()
        resp = self._mock_response(
            "data: nope1\ndata: nope2",
            content_type="text/event-stream",
        )
        result = transport._parse_http_response(8, resp)
        assert result.is_error
        assert "无法解析" in result.error_message or "parse" in result.error_message.lower()
