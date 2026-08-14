"""SearchIndex — 全局会话搜索索引。

每轮对话后追加一条摘要。
搜索时使用 bigram Jaccard 关键词匹配，返回 top-K。

索引文件：
  sessions/_search_index.json    — [{id, session_id, turn, summary}]
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from core.storage import atomic_write_json

logger = logging.getLogger(__name__)

from core.context.relevance import _bigrams, _jaccard


@dataclass
class SearchResult:
    session_id: str
    turn: int
    summary: str
    score: float                         # 0.0 ~ 1.0


class SearchIndex:
    """全局搜索索引：JSON metadata + bigram Jaccard 关键词匹配。

    用法:
        index = SearchIndex(sessions_root)
        await index.add(session_id, turn, summary)
        results = await index.search("Docker 部署")
        index.remove_session(session_id)
    """

    def __init__(self, sessions_root: Path) -> None:
        self._root = sessions_root
        self._index_path = sessions_root / "_search_index.json"
        # entries: [{id: int, session_id: str, turn: int, summary: str}, ...]
        self._entries: list[dict] = []
        self._next_id: int = 0
        self._load()

    # ── 持久化 ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        """从磁盘加载索引（兼容 JSON 数组和 JSONL 两种格式）。"""
        from core.storage import read_jsonl
        self._entries = read_jsonl(self._index_path)
        if self._entries:
            self._next_id = max(e.get("id", 0) for e in self._entries) + 1

    def _save_entries(self) -> None:
        """全量重写索引（用于 remove_session / rebuild）。"""
        from core.storage import overwrite_jsonl
        overwrite_jsonl(self._index_path, self._entries)

    # ── CRUD ────────────────────────────────────────────────────────────

    def add(self, session_id: str, turn: int, summary: str) -> None:
        """追加一条轮次摘要到索引（JSONL 追加）。"""
        from core.storage import append_jsonl
        entry = {
            "id": self._next_id,
            "session_id": session_id,
            "turn": turn,
            "summary": summary,
        }
        self._entries.append(entry)
        self._next_id += 1
        append_jsonl(self._index_path, entry)

    def remove_session(self, session_id: str) -> int:
        """删除指定会话的所有索引条目。

        Returns:
            删除的条目数
        """
        new_entries = [e for e in self._entries if e["session_id"] != session_id]
        removed = len(self._entries) - len(new_entries)

        if removed == 0:
            return 0

        self._entries = new_entries
        if not new_entries:
            self._next_id = 0
        self._save_entries()

        logger.info(
            f"搜索索引：从会话 {session_id} 移除 {removed} 条记录"
        )
        return removed

    # ── 搜索 ────────────────────────────────────────────────────────────

    async def search(
        self, query: str, top_k: int = 5,
    ) -> list[SearchResult]:
        """搜索索引，返回 top-K 结果（bigram Jaccard 关键词匹配）。"""
        if not self._entries:
            return []

        q_bigrams = _bigrams(query)
        scored: list[tuple[int, float]] = []
        for i, e in enumerate(self._entries):
            sim = _jaccard(q_bigrams, _bigrams(e["summary"]))
            if sim > 0.05:
                scored.append((i, sim))
        scored.sort(key=lambda x: x[1], reverse=True)

        results: list[SearchResult] = []
        for idx, score in scored[:top_k]:
            e = self._entries[idx]
            results.append(SearchResult(
                session_id=e["session_id"],
                turn=e["turn"],
                summary=e["summary"],
                score=round(score, 4),
            ))
        return results

    # ── 重建 ────────────────────────────────────────────────────────────

    async def rebuild(self) -> int:
        """从所有 timeline.json 重建索引。

        Returns:
            索引的条目总数
        """
        self._entries = []
        self._next_id = 0

        if not self._root.exists():
            self._save_entries()
            return 0

        for session_dir in sorted(self._root.iterdir()):
            if not session_dir.is_dir() or session_dir.name.startswith("_"):
                continue
            timeline = session_dir / "timeline.json"
            if not timeline.exists():
                continue
            try:
                from core.storage import read_jsonl
                tl_entries = read_jsonl(timeline)
            except (json.JSONDecodeError, OSError):
                continue
            for tl in tl_entries:
                summary = tl.get("summary", "")
                if not summary:
                    continue
                entry = {
                    "id": self._next_id,
                    "session_id": session_dir.name,
                    "turn": tl.get("turn", 0),
                    "summary": summary,
                }
                self._next_id += 1
                self._entries.append(entry)

        self._save_entries()

        logger.info(f"搜索索引重建完成：{len(self._entries)} 条记录")
        return len(self._entries)

    # ── 统计 ────────────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        return len(self._entries)
