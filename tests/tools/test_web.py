"""Tests for web tool — search + fetch combined."""

import asyncio
import socket
import ssl
import urllib.error
import urllib.request

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from core.tools.web import (
    execute,
    schema,
    _html_to_text,
    _extract_charset,
    _is_private_host,
    _SafeRedirectHandler,
    _detect_charset_from_html,
    MAX_DOWNLOAD_BYTES,
)


# ── Schema ─────────────────────────────────────────────────────────────────

class TestWebSchema:
    def test_schema(self):
        assert schema["type"] == "object"
        assert "action" in schema["required"]
        assert schema["properties"]["action"]["enum"] == ["search", "fetch"]

    def test_unknown_action(self):
        import asyncio
        async def _run():
            return await execute({"action": "paste"})
        result = asyncio.run(_run())
        assert "错误" in result
        assert "未知" in result.lower()


# ── Search mode ────────────────────────────────────────────────────────────

class TestWebSearch:
    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await execute({"action": "search", "query": ""})
        assert "不能为空" in result

    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        with patch("core.tools.web.DDGS") as mock_ddgs:
            mock_instance = MagicMock()
            mock_instance.text.return_value = [
                {"title": "Result 1", "href": "http://a.com", "body": "snippet A"},
                {"title": "Result 2", "href": "http://b.com", "body": "snippet B"},
            ]
            mock_ddgs.return_value.__enter__.return_value = mock_instance

            result = await execute({"action": "search", "query": "test"})
            assert "Result 1" in result
            assert "http://a.com" in result


# ── Fetch mode ─────────────────────────────────────────────────────────────

class TestWebFetch:
    @pytest.mark.asyncio
    async def test_empty_url(self):
        result = await execute({"action": "fetch", "url": ""})
        assert "不能为空" in result

    @pytest.mark.asyncio
    async def test_url_without_protocol(self):
        result = await execute({"action": "fetch", "url": "example.com"})
        assert "http://" in result or "https://" in result

    @pytest.mark.asyncio
    async def test_fetch_success(self):
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
            mock_resp.read.side_effect = [
                b"<html><body><h1>Hello</h1><p>World</p></body></html>",
                b"",
            ]
            mock_open.return_value.__enter__.return_value = mock_resp

            result = await execute({"action": "fetch", "url": "http://example.com"})
            assert "# Hello" in result
            assert "World" in result

    @pytest.mark.asyncio
    async def test_http_error(self):
        import urllib.error
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            mock_open.side_effect = urllib.error.HTTPError(
                "http://example.com", 404, "Not Found", {}, None
            )
            result = await execute({"action": "fetch", "url": "http://example.com"})
            assert "404" in result

    @pytest.mark.asyncio
    async def test_max_chars_truncation(self):
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
            mock_resp.read.side_effect = [
                b"<p>" + b"x" * 500 + b"</p>",
                b"",
            ]
            mock_open.return_value.__enter__.return_value = mock_resp

            result = await execute({"action": "fetch", "url": "http://example.com", "max_chars": 200})
            assert "截断" in result or len(result) <= 350


# ── HTML helpers ───────────────────────────────────────────────────────────

class TestHtmlToText:
    def test_strips_tags(self):
        result = _html_to_text("<p>Hello <b>World</b></p>")
        assert "Hello" in result
        assert "World" in result

    def test_removes_script(self):
        result = _html_to_text("<html><script>alert('xss')</script><p>safe</p></html>")
        assert "safe" in result
        assert "alert" not in result

    def test_converts_headings(self):
        result = _html_to_text("<h1>Title</h1><h2>Sub</h2><h3>Subsub</h3>")
        assert "# Title" in result
        assert "## Sub" in result
        assert "### Subsub" in result


class TestExtractCharset:
    def test_from_content_type(self):
        assert _extract_charset("text/html; charset=utf-8") == "utf-8"

    def test_no_charset(self):
        assert _extract_charset("text/html") == ""

    @pytest.mark.asyncio
    async def test_fetch_resolves_real_socket_for_timeout(self):
        """回归：resp.fp→raw(SocketIO)→_sock(SSLSocket) 沿链解析，
        不能对 SocketIO 调 settimeout（曾 AttributeError 导致 web fetch 全挂）。"""
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
            mock_resp.read.side_effect = [b"<p>ok</p>", b""]
            # 模拟真实结构：raw 是无 settimeout 的 SocketIO-like，_sock 是真正的 socket
            fake_socketio = MagicMock(spec=[])  # spec=[] → 无任何属性（模拟 SocketIO 缺 settimeout）
            fake_sslsocket = MagicMock()
            fake_socketio._sock = fake_sslsocket
            mock_resp.fp.raw = fake_socketio
            mock_open.return_value.__enter__.return_value = mock_resp

            result = await execute({"action": "fetch", "url": "http://example.com"})
            assert "ok" in result
            fake_sslsocket.settimeout.assert_called()  # 超时设在真正的 socket 上


# ── Search 边角 ────────────────────────────────────────────────────────────

class TestWebSearchEdgeCases:
    @pytest.mark.asyncio
    async def test_invalid_num_falls_back(self):
        """非 int / <1 的 num 回退到默认 5，仍能出结果。"""
        with patch("core.tools.web.DDGS") as mock_ddgs:
            mock_instance = MagicMock()
            mock_instance.text.return_value = [
                {"title": "Result", "href": "http://a.com", "body": "snippet"},
            ]
            mock_ddgs.return_value.__enter__.return_value = mock_instance
            result = await execute({"action": "search", "query": "test", "num": "abc"})
            assert "Result" in result

    @pytest.mark.asyncio
    async def test_search_timeout(self):
        """_search 超时 → 返回超时错误（asyncio.wait_for 传播内部 TimeoutError）。"""
        with patch("core.tools.web._search", side_effect=asyncio.TimeoutError("timed out")):
            result = await execute({"action": "search", "query": "test"})
        assert "超时" in result

    @pytest.mark.asyncio
    async def test_search_failed(self):
        with patch("core.tools.web._search", side_effect=RuntimeError("boom")):
            result = await execute({"action": "search", "query": "test"})
        assert "失败" in result

    @pytest.mark.asyncio
    async def test_search_no_results(self):
        with patch("core.tools.web.DDGS") as mock_ddgs:
            mock_instance = MagicMock()
            mock_instance.text.return_value = []
            mock_ddgs.return_value.__enter__.return_value = mock_instance
            result = await execute({"action": "search", "query": "nothing"})
        assert "未找到" in result


# ── _is_private_host ───────────────────────────────────────────────────────

class TestIsPrivateHost:
    def test_localhost_variants(self):
        assert _is_private_host("localhost")
        assert _is_private_host("127.0.0.1")
        assert _is_private_host("0.0.0.0")
        assert _is_private_host("::1")

    def test_private_ip(self):
        assert _is_private_host("192.168.1.1")
        assert _is_private_host("10.0.0.1")

    def test_public_ip_false(self):
        assert not _is_private_host("8.8.8.8")

    def test_dns_failure_returns_false(self):
        with patch("socket.gethostbyname", side_effect=socket.gaierror("no such host")):
            assert _is_private_host("nonexistent.invalid") is False


# ── _SafeRedirectHandler ──────────────────────────────────────────────────

class TestSafeRedirectHandler:
    def test_redirect_to_private_host_blocked(self):
        handler = _SafeRedirectHandler()
        with pytest.raises(urllib.error.URLError):
            handler.redirect_request(
                None, None, 302, "Found", {}, "http://127.0.0.1/redirect"
            )

    def test_redirect_to_public_host_delegates(self):
        handler = _SafeRedirectHandler()
        req = urllib.request.Request("http://example.com/orig")
        new = handler.redirect_request(
            req, None, 302, "Found", {}, "http://example.com/target"
        )
        assert new is not None


# ── Fetch 边角 ────────────────────────────────────────────────────────────

class TestWebFetchEdgeCases:
    @pytest.mark.asyncio
    async def test_url_without_hostname(self):
        result = await execute({"action": "fetch", "url": "http://"})
        # 无 hostname → invalid_url（消息含 "http:// 或 https://"）
        assert "http" in result
        assert "未" not in result  # 不是 fetch 成功路径

    @pytest.mark.asyncio
    async def test_private_host_blocked(self):
        result = await execute({"action": "fetch", "url": "http://127.0.0.1/secret"})
        assert "内网" in result or "安全" in result

    @pytest.mark.asyncio
    async def test_urlparse_error(self):
        with patch("core.tools.web.urlparse", side_effect=ValueError("bad url")):
            result = await execute({"action": "fetch", "url": "http://example.com"})
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_invalid_timeout_falls_back(self):
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "text/plain; charset=utf-8"}
            mock_resp.read.side_effect = [b"hello", b""]
            mock_open.return_value.__enter__.return_value = mock_resp
            result = await execute({"action": "fetch", "url": "http://example.com", "timeout": "abc"})
            assert "hello" in result

    @pytest.mark.asyncio
    async def test_invalid_max_chars_falls_back(self):
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "text/plain; charset=utf-8"}
            mock_resp.read.side_effect = [b"hello", b""]
            mock_open.return_value.__enter__.return_value = mock_resp
            result = await execute({"action": "fetch", "url": "http://example.com", "max_chars": "abc"})
            assert "hello" in result

    @pytest.mark.asyncio
    async def test_socket_settimeout_oserror_ignored(self):
        """socket.settimeout 抛 OSError 时静默忽略，不中断抓取。"""
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
            mock_resp.read.side_effect = [b"<p>ok</p>", b""]
            fake_socketio = MagicMock(spec=[])
            fake_sslsocket = MagicMock()
            fake_sslsocket.settimeout.side_effect = OSError("cannot set timeout")
            fake_socketio._sock = fake_sslsocket
            mock_resp.fp.raw = fake_socketio
            mock_open.return_value.__enter__.return_value = mock_resp
            result = await execute({"action": "fetch", "url": "http://example.com"})
            assert "ok" in result

    @pytest.mark.asyncio
    async def test_read_socket_timeout(self):
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
            mock_resp.read.side_effect = socket.timeout("timed out")
            mock_open.return_value.__enter__.return_value = mock_resp
            result = await execute({"action": "fetch", "url": "http://example.com"})
            assert "超时" in result or "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_download_too_large(self):
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
            mock_resp.read.return_value = b"x" * (MAX_DOWNLOAD_BYTES + 1)
            mock_open.return_value.__enter__.return_value = mock_resp
            result = await execute({"action": "fetch", "url": "http://example.com"})
            assert "过大" in result

    @pytest.mark.asyncio
    async def test_non_text_content(self):
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "application/pdf"}
            mock_resp.read.side_effect = [b"%PDF-1.4", b""]
            mock_open.return_value.__enter__.return_value = mock_resp
            result = await execute({"action": "fetch", "url": "http://example.com/doc.pdf"})
            assert "非文本" in result

    @pytest.mark.asyncio
    async def test_charset_detected_from_html(self):
        """Content-Type 无 charset 时从 <meta charset> 探测编码。"""
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "text/html"}
            mock_resp.read.side_effect = [b'<meta charset="gb2312">hello world', b""]
            mock_open.return_value.__enter__.return_value = mock_resp
            result = await execute({"action": "fetch", "url": "http://example.com"})
            assert "hello world" in result

    @pytest.mark.asyncio
    async def test_urlerror(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=urllib.error.URLError("conn refused")):
            result = await execute({"action": "fetch", "url": "http://example.com"})
            assert "无法访问" in result

    @pytest.mark.asyncio
    async def test_ssl_error(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=ssl.SSLError("cert verify failed")):
            result = await execute({"action": "fetch", "url": "https://example.com"})
            assert "SSL" in result

    @pytest.mark.asyncio
    async def test_timeout_error(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=TimeoutError("timed out")):
            result = await execute({"action": "fetch", "url": "http://example.com"})
            assert "超时" in result or "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=RuntimeError("boom")):
            result = await execute({"action": "fetch", "url": "http://example.com"})
            assert "失败" in result


# ── _detect_charset_from_html ──────────────────────────────────────────────

class TestDetectCharset:
    def test_from_meta(self):
        assert _detect_charset_from_html(b'<meta charset="utf-8">') == "utf-8"
        assert _detect_charset_from_html(b"<meta charset=gb2312>") == "gb2312"

    def test_default_utf8(self):
        assert _detect_charset_from_html(b"<html><body>hi</body></html>") == "utf-8"
