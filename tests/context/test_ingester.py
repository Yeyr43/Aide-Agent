"""ContextIngester 测试 — session_id 键归一化回归。"""

import pytest
from pathlib import Path

from core.context.ingester import ContextIngester


class TestSetSession:
    """回归：set_session 的 session_id 键必须统一为目录名。

    audit 发现：agent.py 传完整路径 → add 索引用完整路径键，而
    rebuild/delete 用目录名，导致删除会话后索引条目残留。
    """

    def _make(self, tmp_path):
        from core.storage import JsonStore
        store = JsonStore(tmp_path / "store.json")
        return ContextIngester(store, sessions_root=tmp_path / "sessions")

    def test_full_path_normalized_to_dirname(self, tmp_path):
        ingester = self._make(tmp_path)
        session_dir = tmp_path / "sessions" / "20260816_120000"
        session_dir.mkdir(parents=True)

        ingester.set_session(str(session_dir))
        assert ingester.session_id == "20260816_120000"
        assert ingester._session_dir == session_dir

    def test_id_normalized_to_dirname(self, tmp_path):
        ingester = self._make(tmp_path)
        session_dir = tmp_path / "sessions" / "20260816_120000"
        session_dir.mkdir(parents=True)

        ingester.set_session("20260816_120000")
        assert ingester.session_id == "20260816_120000"

    def test_add_uses_dirname_key(self, tmp_path):
        """ingest 喂给 SearchIndex 的 session_id 必须是目录名（与 rebuild 一致）。"""
        import asyncio
        from core.search.index import SearchIndex
        from core.storage import JsonStore

        index = SearchIndex(tmp_path / "search_index")
        store = JsonStore(tmp_path / "store.json")
        ingester = ContextIngester(
            store,
            sessions_root=tmp_path / "sessions",
            search_index=index,
        )
        session_dir = tmp_path / "sessions" / "20260816_120000"
        session_dir.mkdir(parents=True)
        ingester.set_session(str(session_dir))

        async def _run():
            await store.start()
            await ingester.ingest(1, "hello", "hi there", [])
            await store.close()

        asyncio.run(_run())

        entries = index._entries
        assert len(entries) == 1
        assert entries[0]["session_id"] == "20260816_120000"
