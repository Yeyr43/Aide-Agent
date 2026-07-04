"""Integration tests for MCP transport layer — StdioTransport + HTTPTransport.

Tests the full request/response lifecycle using mocked subprocesses and HTTP.
"""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

from core.tools.mcp.protocol import (
    JSONRPCRequest,
    JSONRPCResponse,
    parse_response,
)
from core.tools.mcp.transport import (
    StdioTransport,
    HTTPTransport,
    create_transport,
    INIT_TIMEOUT,
    REQUEST_TIMEOUT,
)


# ── StdioTransport Tests ─────────────────────────────────────────────────


class TestStdioTransportLifecycle:
    @pytest.mark.asyncio
    async def test_init_state(self):
        t = StdioTransport()
        assert t.is_connected is False
        assert t._proc is None

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self):
        t = StdioTransport()
        await t.disconnect()  # should not raise
        assert t.is_connected is False

    @pytest.mark.asyncio
    async def test_send_request_without_process(self):
        t = StdioTransport()
        with pytest.raises(RuntimeError, match="子进程未运行"):
            await t.send_request(JSONRPCRequest(method="test", id=1))


class TestStdioTransportRequestResponse:
    @pytest.mark.asyncio
    async def test_send_request_and_receive_response(self):
        """send_request should return parsed response when read loop dispatches it."""
        with patch("asyncio.create_subprocess_exec") as mock_sp:
            mock_proc = _make_mock_process()
            mock_sp.return_value = mock_proc

            t = StdioTransport()
            t._proc = mock_proc
            t._connected = True

            # Set up read loop to dispatch response matching request ID
            async def simulate_read():
                # Wait briefly for send_request to register its future
                await asyncio.sleep(0.01)
                # Dispatch the response directly
                resp = JSONRPCResponse(id=1, result={"tools": ["a", "b"]})
                future = t._pending.get(1)
                if future and not future.done():
                    future.set_result(resp)

            t._reader_task = asyncio.create_task(simulate_read())

            resp = await t.send_request(JSONRPCRequest(method="tools/list"))
            assert resp.result == {"tools": ["a", "b"]}

            await t.disconnect()

    @pytest.mark.asyncio
    async def test_pending_futures_cancelled_on_disconnect(self):
        """All pending futures should be resolved with error on disconnect."""
        with patch("asyncio.create_subprocess_exec") as mock_sp:
            mock_proc = _make_mock_process()
            mock_proc.stdout.readline = AsyncMock()
            # Never return — the read loop will wait forever
            mock_proc.stdout.readline.side_effect = asyncio.CancelledError

            t = StdioTransport()
            t._proc = mock_proc
            t._connected = True
            t._reader_task = asyncio.create_task(t._read_loop())

            # Start a request (won't complete because read loop never dispatches)
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            t._pending[42] = future

            await t.disconnect()

            # The future should have been resolved with an error
            assert future.done()
            with pytest.raises(RuntimeError, match="连接已断开"):
                future.result()

    @pytest.mark.asyncio
    async def test_read_loop_dispatches_to_correct_future(self):
        """Read loop should dispatch response to the future with matching ID."""
        t = StdioTransport()

        # Create pending futures
        loop = asyncio.get_running_loop()
        f1 = loop.create_future()
        f2 = loop.create_future()
        t._pending[1] = f1
        t._pending[2] = f2

        # Simulate the read loop processing a response for request 2
        mock_proc = _make_mock_process()
        mock_proc.stdout.readline = AsyncMock()
        mock_proc.stdout.readline.side_effect = [
            json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"data": "for f2"}}).encode(),
            asyncio.CancelledError,  # stop the loop
        ]
        t._proc = mock_proc

        read_task = asyncio.create_task(t._read_loop())
        try:
            await asyncio.sleep(0.05)  # give read loop time to process
        finally:
            read_task.cancel()
            try:
                await read_task
            except asyncio.CancelledError:
                pass

        # f2 should be resolved
        assert f2.done()
        assert f2.result().result == {"data": "for f2"}
        # f1 should still be pending
        assert not f1.done()

    @pytest.mark.asyncio
    async def test_read_loop_handles_malformed_line(self):
        """Malformed JSON lines should be skipped, not crash the loop."""
        t = StdioTransport()
        mock_proc = _make_mock_process()
        mock_proc.stdout.readline = AsyncMock()
        mock_proc.stdout.readline.side_effect = [
            b"not valid json\n",
            b"",  # EOF
        ]
        t._proc = mock_proc

        # Should not raise
        await t._read_loop()

    @pytest.mark.asyncio
    async def test_read_loop_handles_empty_line(self):
        """Empty lines should be skipped."""
        t = StdioTransport()
        mock_proc = _make_mock_process()
        mock_proc.stdout.readline = AsyncMock()
        mock_proc.stdout.readline.side_effect = [
            b"\n",
            b" \n",
            b"",  # EOF
        ]
        t._proc = mock_proc
        await t._read_loop()  # should not raise


# ── HTTPTransport Tests ──────────────────────────────────────────────────


class TestHTTPTransportLifecycle:
    def test_init_state(self):
        t = HTTPTransport()
        assert t.is_connected is False
        assert t._url == ""

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self):
        t = HTTPTransport()
        await t.disconnect()  # should not raise

    @pytest.mark.asyncio
    async def test_send_request_without_connection(self):
        t = HTTPTransport()
        with pytest.raises(RuntimeError, match="连接未建立"):
            await t.send_request(JSONRPCRequest(method="test"))


class TestHTTPTransportRequest:
    @pytest.mark.asyncio
    async def test_connect_sends_initialize(self):
        """connect() sends initialize request and parses response."""
        t = HTTPTransport()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.headers = {}
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
            }).encode()

            mock_urlopen.return_value = mock_resp

            await t.connect("http://localhost:9999/mcp")
            assert t.is_connected is True
            assert t._url == "http://localhost:9999/mcp"

    @pytest.mark.asyncio
    async def test_connect_handles_initialize_error(self):
        """If initialize returns error, connect should raise."""
        t = HTTPTransport()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.headers = {}
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32000, "message": "Not ready"},
            }).encode()

            mock_urlopen.return_value = mock_resp

            with pytest.raises(RuntimeError, match="initialize"):
                await t.connect("http://localhost:9999/mcp")
            assert t.is_connected is False

    @pytest.mark.asyncio
    async def test_captures_session_id(self):
        """HTTP transport captures Mcp-Session-Id from response headers."""
        t = HTTPTransport()
        t._connected = True
        t._url = "http://localhost:9999/mcp"

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.headers = {"Mcp-Session-Id": "session-abc-123"}
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"tools": []},
            }).encode()

            mock_urlopen.return_value = mock_resp

            resp = await t.send_request(JSONRPCRequest(method="tools/list"))
            assert t._session_id == "session-abc-123"

    @pytest.mark.asyncio
    async def test_parse_sse_response(self):
        """HTTP transport extracts data from SSE-formatted response."""
        t = HTTPTransport()
        t._connected = True
        t._url = "http://localhost:9999/mcp"

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.headers = {}
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = (
                'event: message\ndata: {"jsonrpc":"2.0","id":3,"result":{"status":"ok"}}\n\n'
            ).encode()

            mock_urlopen.return_value = mock_resp

            resp = await t.send_request(JSONRPCRequest(method="test"))
            assert resp.result == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_handle_http_error(self):
        """HTTP errors should raise with descriptive message."""
        t = HTTPTransport()
        t._connected = True
        t._url = "http://localhost:9999/mcp"

        with patch("urllib.request.urlopen") as mock_urlopen:
            import urllib.error
            mock_urlopen.side_effect = urllib.error.HTTPError(
                "http://localhost:9999/mcp", 500, "Internal Error", {}, None
            )

            with pytest.raises(RuntimeError, match="HTTP 500"):
                await t.send_request(JSONRPCRequest(method="test"))


# ── Transport Factory Tests ──────────────────────────────────────────────


class TestCreateTransport:
    @pytest.mark.asyncio
    async def test_creates_stdio_transport(self):
        with patch("core.tools.mcp.transport.StdioTransport.connect") as mock_conn:
            mock_conn.return_value = None
            transport = await create_transport(command="echo", args=["hello"])
            assert isinstance(transport, StdioTransport)
            mock_conn.assert_called_once_with("echo", ["hello"])

    @pytest.mark.asyncio
    async def test_creates_http_transport(self):
        with patch("core.tools.mcp.transport.HTTPTransport.connect") as mock_conn:
            mock_conn.return_value = None
            transport = await create_transport(url="http://localhost:8080/mcp")
            assert isinstance(transport, HTTPTransport)
            mock_conn.assert_called_once_with("http://localhost:8080/mcp")

    @pytest.mark.asyncio
    async def test_no_command_or_url_raises(self):
        with pytest.raises(ValueError, match="必须提供"):
            await create_transport()


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_mock_process():
    """Create a mock asyncio subprocess."""
    mock = MagicMock()
    mock.returncode = None
    mock.stdin = MagicMock()
    mock.stdout = MagicMock()
    mock.stderr = MagicMock()
    mock.stdout.readline = AsyncMock()
    mock.stdout.readline.return_value = b""  # EOF by default
    mock.wait = AsyncMock()
    mock.wait.return_value = 0
    return mock
