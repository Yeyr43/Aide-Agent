"""Tests for web_fetch tool."""

import pytest
from unittest.mock import patch, MagicMock

from core.tools.builtin.web_fetch import execute, schema, _html_to_text, _extract_charset


class TestWebFetch:
    @pytest.mark.asyncio
    async def test_empty_url(self):
        result = await execute({"url": ""})
        assert "不能为空" in result

    @pytest.mark.asyncio
    async def test_url_without_protocol(self):
        result = await execute({"url": "example.com"})
        assert "http://" in result or "https://" in result

    @pytest.mark.asyncio
    async def test_fetch_success(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
            # side_effect: 第一次返回数据，后续返回空 bytes（分块下载协议）
            mock_resp.read.side_effect = [
                b"<html><body><h1>Hello</h1><p>World</p></body></html>",
                b"",
            ]
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            result = await execute({"url": "http://example.com"})
            assert "# Hello" in result
            assert "World" in result

    @pytest.mark.asyncio
    async def test_http_error(self):
        import urllib.error
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                "http://example.com", 404, "Not Found", {}, None
            )
            result = await execute({"url": "http://example.com"})
            assert "404" in result

    @pytest.mark.asyncio
    async def test_url_error(self):
        import urllib.error
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("connection refused")
            result = await execute({"url": "http://example.com"})
            assert "无法访问" in result

    @pytest.mark.asyncio
    async def test_timeout_error(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = TimeoutError()
            result = await execute({"url": "http://example.com"})
            assert "超时" in result

    @pytest.mark.asyncio
    async def test_max_chars_truncation(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
            mock_resp.read.side_effect = [
                b"<p>" + b"x" * 500 + b"</p>",
                b"",
            ]
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            result = await execute({"url": "http://example.com", "max_chars": 200})
            assert "截断" in result or len(result) <= 350


class TestHtmlToText:
    def test_strips_tags(self):
        result = _html_to_text("<p>Hello <b>World</b></p>")
        assert "Hello" in result
        assert "World" in result

    def test_removes_script(self):
        result = _html_to_text("<html><script>alert('xss')</script><p>safe</p></html>")
        assert "safe" in result
        assert "alert" not in result

    def test_removes_style(self):
        result = _html_to_text("<html><style>.red{color:red}</style><p>text</p></html>")
        assert "text" in result
        assert ".red" not in result

    def test_converts_headings(self):
        result = _html_to_text("<h1>Title</h1><h2>Sub</h2><h3>Subsub</h3>")
        assert "# Title" in result
        assert "## Sub" in result
        assert "### Subsub" in result


class TestExtractCharset:
    def test_from_content_type(self):
        assert _extract_charset("text/html; charset=utf-8") == "utf-8"

    def test_from_content_type_iso(self):
        assert _extract_charset("text/html; charset=ISO-8859-1") == "ISO-8859-1"

    def test_no_charset(self):
        assert _extract_charset("text/html") == ""


class TestWebFetchSchema:
    def test_schema(self):
        assert schema["type"] == "object"
        assert "url" in schema["required"]
        assert "url" in schema["properties"]
        assert "timeout" in schema["properties"]
        assert "max_chars" in schema["properties"]
