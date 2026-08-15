"""测试 search/index.py — SearchIndex bigram Jaccard 搜索。"""

import json
import pytest
from pathlib import Path

from core.search.index import SearchIndex, SearchResult


class TestSearchIndex:
    """测试 SearchIndex 核心功能。"""

    @pytest.fixture
    def index(self, tmp_path):
        """创建一个基于临时目录的 SearchIndex。"""
        return SearchIndex(tmp_path)

    def test_initial_state_empty(self, index):
        assert index.size == 0

    def test_add_entry(self, index):
        index.add("20260801_120000", 1, "用户询问 Python 异步编程")
        assert index.size == 1

    def test_add_multiple_entries(self, index):
        index.add("session_1", 1, "summary one")
        index.add("session_1", 2, "summary two")
        index.add("session_2", 1, "summary three")
        assert index.size == 3

    def test_remove_session(self, index):
        index.add("session_a", 1, "a1")
        index.add("session_a", 2, "a2")
        index.add("session_b", 1, "b1")
        assert index.size == 3

        removed = index.remove_session("session_a")
        assert removed == 2
        assert index.size == 1

    def test_remove_nonexistent_session(self, index):
        index.add("session_1", 1, "summary")
        removed = index.remove_session("nonexistent")
        assert removed == 0
        assert index.size == 1

    def test_remove_all_resets_id(self, index):
        index.add("only_session", 1, "only")
        index.remove_session("only_session")
        assert index.size == 0

    @pytest.mark.asyncio
    async def test_search_returns_results(self, index):
        index.add("s1", 1, "Python 异步编程问题")
        index.add("s2", 1, "Docker 部署配置")
        index.add("s3", 1, "Python 测试框架选择")

        results = await index.search("Python 编程")
        assert len(results) > 0
        # 与 "Python" 匹配度最高
        assert any("Python" in r.summary for r in results)

    @pytest.mark.asyncio
    async def test_search_empty_index(self, index):
        results = await index.search("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_no_match(self, index):
        index.add("s1", 1, "Python programming")
        results = await index.search("xyzabc123")
        assert results == []  # bigram 完全不匹配

    @pytest.mark.asyncio
    async def test_search_top_k(self, index):
        for i in range(10):
            index.add(f"sess_{i}", 1, f"topic number {i} about Python")
        results = await index.search("Python", top_k=3)
        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_search_result_has_score(self, index):
        index.add("s1", 1, "Python async programming tips")
        index.add("s1", 2, "Python test strategies")
        results = await index.search("Python programming")
        assert len(results) >= 1
        for r in results:
            assert 0.0 <= r.score <= 1.0

    @pytest.mark.asyncio
    async def test_search_scores_are_sorted(self, index):
        index.add("s1", 1, "Python async await asyncio event loop coroutine")
        index.add("s2", 1, "some random unrelated topic here")
        results = await index.search("Python async")
        if len(results) >= 2:
            assert results[0].score >= results[-1].score

    @pytest.mark.asyncio
    async def test_rebuild_from_timeline(self, tmp_path):
        """索引从各会话 timeline.json 重建（timeline 是唯一源，不持久化索引文件）。"""
        session_dir = tmp_path / "20260801_120000"
        session_dir.mkdir()
        from core.storage import append_jsonl
        append_jsonl(session_dir / "timeline.json", {
            "turn": 1, "timestamp": "x", "summary": "Python 异步编程",
        })

        idx = SearchIndex(tmp_path)
        assert idx.size == 0  # 启动时为空，不加载旧索引文件
        await idx.rebuild()
        assert idx.size == 1

    def test_ignores_old_index_file(self, tmp_path):
        """旧 _search_index.json 缓存不再加载（索引从 timeline 重建）。"""
        index_path = tmp_path / "_search_index.json"
        from core.storage import append_jsonl
        append_jsonl(index_path, {"id": 0, "session_id": "s1", "turn": 1, "summary": "old data"})

        idx = SearchIndex(tmp_path)
        assert idx.size == 0


class TestSearchResult:
    """测试 SearchResult dataclass。"""

    def test_fields(self):
        sr = SearchResult(
            session_id="20260801_120000",
            turn=3,
            summary="test summary",
            score=0.85,
        )
        assert sr.session_id == "20260801_120000"
        assert sr.turn == 3
        assert sr.summary == "test summary"
        assert sr.score == 0.85
