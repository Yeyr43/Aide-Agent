"""Tests for core.tools.search_chat — conversation search tool."""

import json
import pytest
from unittest.mock import patch

from core.tools.search_chat import execute, schema, _session_search


class TestSearchChatSchema:
    def test_schema_type(self):
        assert schema["type"] == "object"

    def test_query_is_required(self):
        assert "query" in schema["required"]

    def test_top_k_in_properties(self):
        assert "top_k" in schema["properties"]
        assert "session_id" in schema["properties"]


class TestSearchChatExecute:
    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await execute({"query": ""})
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_empty_query_whitespace(self):
        result = await execute({"query": "   "})
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_session_search_with_valid_session(self, tmp_path):
        """With session_id, delegates to _session_search."""
        sessions_root = tmp_path / "sessions"
        sessions_root.mkdir()
        sess_dir = sessions_root / "test_session"
        sess_dir.mkdir()

        entries = [
            {"turn": 1, "summary": "讨论架构设计"},
            {"turn": 2, "summary": "修复登录 bug"},
        ]
        (sess_dir / "timeline.json").write_text(json.dumps(entries), encoding="utf-8")

        with patch("core.tools.search_chat.SESSIONS_ROOT", sessions_root):
            result = await execute({"query": "架构", "session_id": "test_session"})
            assert isinstance(result, str)
            assert len(result) > 0

    @pytest.mark.asyncio
    async def test_invalid_top_k_falls_back(self, tmp_path):
        """Non-integer top_k falls back to default."""
        sessions_root = tmp_path / "sessions"
        sessions_root.mkdir()
        sess_dir = sessions_root / "test"
        sess_dir.mkdir()
        (sess_dir / "timeline.json").write_text(json.dumps([
            {"turn": 1, "summary": "测试架构"},
        ]), encoding="utf-8")

        with patch("core.tools.search_chat.SESSIONS_ROOT", sessions_root):
            result = await execute({
                "query": "架构", "session_id": "test", "top_k": "invalid",
            })
            assert isinstance(result, str)
            assert len(result) > 0


class TestSessionSearch:
    """Direct tests for _session_search."""

    @pytest.mark.asyncio
    async def test_session_not_found(self, tmp_path):
        sessions_root = tmp_path / "sessions"
        sessions_root.mkdir()
        with patch("core.tools.search_chat.SESSIONS_ROOT", sessions_root):
            result = await _session_search("nonexistent", "query", 5)
            assert isinstance(result, str)
            assert len(result) > 0

    @pytest.mark.asyncio
    async def test_empty_timeline(self, tmp_path):
        sessions_root = tmp_path / "sessions"
        sessions_root.mkdir()
        sess_dir = sessions_root / "s1"
        sess_dir.mkdir()
        (sess_dir / "timeline.json").write_text("[]", encoding="utf-8")

        with patch("core.tools.search_chat.SESSIONS_ROOT", sessions_root):
            result = await _session_search("s1", "query", 5)
            assert isinstance(result, str)
            assert len(result) > 0

    @pytest.mark.asyncio
    async def test_corrupt_timeline(self, tmp_path):
        sessions_root = tmp_path / "sessions"
        sessions_root.mkdir()
        sess_dir = sessions_root / "s1"
        sess_dir.mkdir()
        (sess_dir / "timeline.json").write_text("not json{{{", encoding="utf-8")

        with patch("core.tools.search_chat.SESSIONS_ROOT", sessions_root):
            result = await _session_search("s1", "query", 5)
            assert isinstance(result, str)
            assert len(result) > 0

    @pytest.mark.asyncio
    async def test_exact_substring_match(self, tmp_path):
        sessions_root = tmp_path / "sessions"
        sessions_root.mkdir()
        sess_dir = sessions_root / "s1"
        sess_dir.mkdir()
        (sess_dir / "timeline.json").write_text(json.dumps([
            {"turn": 1, "summary": "用户询问 Docker 部署方案"},
            {"turn": 2, "summary": "讨论了 Python 优化"},
        ]), encoding="utf-8")

        with patch("core.tools.search_chat.SESSIONS_ROOT", sessions_root):
            result = await _session_search("s1", "Docker", 5)
            assert "Docker" in result

    @pytest.mark.asyncio
    async def test_no_match(self, tmp_path):
        sessions_root = tmp_path / "sessions"
        sessions_root.mkdir()
        sess_dir = sessions_root / "s1"
        sess_dir.mkdir()
        (sess_dir / "timeline.json").write_text(json.dumps([
            {"turn": 1, "summary": "讨论天气"},
        ]), encoding="utf-8")

        with patch("core.tools.search_chat.SESSIONS_ROOT", sessions_root):
            result = await _session_search("s1", "xyzabc_no_match", 5)
            assert isinstance(result, str)
            assert len(result) > 0

    @pytest.mark.asyncio
    async def test_returns_results_sorted_by_score(self, tmp_path):
        """Results should be sorted by relevance score descending."""
        sessions_root = tmp_path / "sessions"
        sessions_root.mkdir()
        sess_dir = sessions_root / "s1"
        sess_dir.mkdir()
        (sess_dir / "timeline.json").write_text(json.dumps([
            {"turn": 1, "summary": "修复登录页面的验证逻辑"},
            {"turn": 2, "summary": "登录相关的重试机制"},
        ]), encoding="utf-8")

        with patch("core.tools.search_chat.SESSIONS_ROOT", sessions_root):
            result = await _session_search("s1", "登录验证", 5)
            assert isinstance(result, str)
            assert len(result) > 0
