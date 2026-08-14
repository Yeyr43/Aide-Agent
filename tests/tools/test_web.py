"""Tests for web tool — search + fetch combined."""

import pytest
from unittest.mock import patch, MagicMock

from core.tools.web import execute, schema, _html_to_text, _extract_charset


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
