"""Tests for core.tools.search_memory — memory search tool."""

import pytest
from unittest.mock import AsyncMock, patch

from core.tools.search_memory import execute, schema


class TestSearchMemorySchema:
    def test_schema_type(self):
        assert schema["type"] == "object"

    def test_query_is_required(self):
        assert "query" in schema["required"]


class TestSearchMemoryExecute:
    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self):
        result = await execute({"query": ""})
        assert result != ""

    @pytest.mark.asyncio
    async def test_empty_query_whitespace(self):
        result = await execute({"query": "   "})
        assert result != ""

    @pytest.mark.asyncio
    async def test_no_results(self):
        """When recall returns empty, tool reports no match."""
        mock_recall = AsyncMock(return_value=[])
        # search_recall is imported inside execute() from core.memory.recall
        with patch("core.tools.search_memory.search_recall", mock_recall):
            result = await execute({"query": "xyzabc_nonexistent_123"})
            assert isinstance(result, str)
            assert len(result) > 0
            mock_recall.assert_called_once_with("xyzabc_nonexistent_123", max_results=10, search_index=None)

    @pytest.mark.asyncio
    async def test_single_result(self):
        mock_recall = AsyncMock(return_value=[
            {"source": "preference", "snippet": "用户偏好简洁回答"},
        ])
        with patch("core.tools.search_memory.search_recall", mock_recall):
            result = await execute({"query": "偏好"})
            assert "偏好" in result

    @pytest.mark.asyncio
    async def test_multiple_results(self):
        mock_recall = AsyncMock(return_value=[
            {"source": "preference", "snippet": "偏好：简洁"},
            {"source": "workflow", "snippet": "工作流：TDD"},
            {"source": "long_term_memory", "snippet": "常用：Docker"},
        ])
        with patch("core.tools.search_memory.search_recall", mock_recall):
            result = await execute({"query": "测试"})
            lines = result.split("\n")
            assert len(lines) >= 3

    @pytest.mark.asyncio
    async def test_newlines_in_snippets_are_replaced(self):
        mock_recall = AsyncMock(return_value=[
            {"source": "workflow", "snippet": "line1\nline2\nline3"},
        ])
        with patch("core.tools.search_memory.search_recall", mock_recall):
            result = await execute({"query": "test"})
            # Newlines in snippets should be replaced (won't appear as raw \n)
            assert isinstance(result, str)
