"""SearchIndex — 全局会话搜索索引。

每轮对话后追加一条摘要 + 预计算 embedding。
搜索时对 query 做 embedding，余弦相似度匹配，返回 top-K。

索引文件：
  sessions/_search_index.json    — [{id, session_id, turn, summary}]
  sessions/_search_embeddings.npy — (N, 384) float32，行号对应 id

当 embedding 引擎不可用时自动降级为 bigram Jaccard 关键词匹配。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.storage import atomic_write_json

logger = logging.getLogger(__name__)

# bigram Jaccard fallback（与 context/relevance.py 一致）
from core.context.relevance import _bigrams, _jaccard

EMBEDDING_DIM = 384


@dataclass
class SearchResult:
    session_id: str
    turn: int
    summary: str
    score: float                         # 0.0 ~ 1.0


class SearchIndex:
    """全局搜索索引：JSON metadata + numpy embeddings。

    用法:
        index = SearchIndex(sessions_root)
        await index.add(session_id, turn, summary)
        results = await index.search("Docker 部署")
        index.remove_session(session_id)
    """

    def __init__(self, sessions_root: Path) -> None:
        self._root = sessions_root
        self._index_path = sessions_root / "_search_index.json"
        self._emb_path = sessions_root / "_search_embeddings.npy"
        # entries: [{id: int, session_id: str, turn: int, summary: str}, ...]
        self._entries: list[dict] = []
        # embeddings: (N, 384) float32, row i ↔ entry with id==i
        self._embeddings: np.ndarray | None = None
        self._next_id: int = 0
        self._dirty_emb: bool = False
        self._load()

    # ── 持久化 ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        """从磁盘加载索引。"""
        if self._index_path.exists():
            try:
                self._entries = json.loads(
                    self._index_path.read_text(encoding="utf-8"),
                )
                if self._entries:
                    self._next_id = max(e.get("id", 0) for e in self._entries) + 1
            except (json.JSONDecodeError, OSError):
                logger.warning("搜索索引损坏，重建空索引")
                self._entries = []

        if self._emb_path.exists():
            try:
                self._embeddings = np.load(self._emb_path)
                # 确保是 (N, 384) 的 float32
                if self._embeddings.ndim != 2 or self._embeddings.shape[1] != EMBEDDING_DIM:
                    logger.warning("嵌入文件维度异常，重建")
                    self._embeddings = None
            except (OSError, ValueError) as e:
                logger.warning(f"嵌入文件加载失败: {e}")
                self._embeddings = None

        if self._embeddings is None and self._entries:
            # 索引有 entries 但 embeddings 丢失 → 清空 entries 重建
            logger.warning("嵌入文件缺失，清空索引等待重建")
            self._entries = []
            self._next_id = 0

    def _save_entries(self) -> None:
        """原子写入 entries JSON。"""
        atomic_write_json(
            self._index_path,
            self._entries,
        )

    def _save_embeddings(self) -> None:
        """写入 embeddings .npy（已有 _embeddings 时调用）。"""
        if self._embeddings is not None and self._dirty_emb:
            np.save(str(self._emb_path), self._embeddings)
            self._dirty_emb = False

    # ── CRUD ────────────────────────────────────────────────────────────

    def add(self, session_id: str, turn: int, summary: str) -> None:
        """追加一条轮次摘要到索引（含 embedding 预计算）。"""
        # 计算 embedding
        emb_vec = self._compute_embedding(summary)

        entry = {
            "id": self._next_id,
            "session_id": session_id,
            "turn": turn,
            "summary": summary,
        }
        self._entries.append(entry)
        self._next_id += 1

        # 追加到 embeddings 矩阵
        if emb_vec is not None:
            row = emb_vec.astype(np.float32).reshape(1, -1)
            if self._embeddings is None:
                self._embeddings = row
            else:
                self._embeddings = np.vstack([self._embeddings, row])
            self._dirty_emb = True

        # 写穿：先存 embeddings（numpy），再原子写 entries（JSON）
        self._save_embeddings()
        self._save_entries()

    def remove_session(self, session_id: str) -> int:
        """删除指定会话的所有索引条目。

        Returns:
            删除的条目数
        """
        keep_ids = {
            e["id"] for e in self._entries
            if e["session_id"] != session_id
        }
        removed = len(self._entries) - len(keep_ids)

        if removed == 0:
            return 0

        # 重建 entries 和 embeddings（保持 id 连续性）
        new_entries = [e for e in self._entries if e["session_id"] != session_id]
        new_ids = {e["id"] for e in new_entries}

        if self._embeddings is not None and len(new_entries) > 0:
            # 保留 keep_ids 对应的行
            rows_to_keep = [
                i for i, e in enumerate(self._entries)
                if e["id"] in keep_ids
            ]
            self._embeddings = self._embeddings[rows_to_keep]
            self._dirty_emb = True
        elif len(new_entries) == 0:
            self._embeddings = None
            self._dirty_emb = True
            self._next_id = 0

        self._entries = new_entries
        self._save_embeddings()
        self._save_entries()

        logger.info(
            f"搜索索引：从会话 {session_id} 移除 {removed} 条记录"
        )
        return removed

    # ── 搜索 ────────────────────────────────────────────────────────────

    async def search(
        self, query: str, top_k: int = 5,
    ) -> list[SearchResult]:
        """搜索索引，返回 top-K 结果。

        有 embedding → 语义搜索（余弦相似度）。
        无 embedding → Jaccard fallback（bigram 字符重叠）。
        """
        if not self._entries:
            return []

        eng = _get_embedding_engine()
        q_emb = eng.embed(query) if eng.available else None

        if q_emb is not None and self._embeddings is not None:
            return self._semantic_search(q_emb, top_k)
        else:
            return self._keyword_search(query, top_k)

    def _semantic_search(
        self, q_emb: np.ndarray, top_k: int,
    ) -> list[SearchResult]:
        """余弦相似度搜索（O(N) 点积）。"""
        # (N, 384) @ (384,) → (N,)
        scores = np.dot(self._embeddings, q_emb)  # all L2-normalized
        # 取 top-K
        if len(scores) <= top_k:
            top_indices = list(range(len(scores)))
        else:
            top_indices = np.argpartition(-scores, top_k)[:top_k]
            top_indices = top_indices[np.argsort(-scores[top_indices])]

        results: list[SearchResult] = []
        for idx in top_indices:
            score = float(scores[idx])
            if score < 0.1:  # 过滤噪声
                continue
            # entries 和 embeddings 行号一一对应
            if idx < len(self._entries):
                e = self._entries[idx]
                results.append(SearchResult(
                    session_id=e["session_id"],
                    turn=e["turn"],
                    summary=e["summary"],
                    score=round(score, 4),
                ))
        return results

    def _keyword_search(
        self, query: str, top_k: int,
    ) -> list[SearchResult]:
        """Jaccard fallback：bigram 字符重叠度。"""
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
        """从所有 timeline.json 重建索引（修复不一致）。

        Returns:
            索引的条目总数
        """
        self._entries = []
        self._embeddings = None
        self._next_id = 0
        self._dirty_emb = True

        if not self._root.exists():
            self._save_entries()
            return 0

        eng = _get_embedding_engine()
        batch_entries: list[dict] = []
        batch_vectors: list[np.ndarray] = []

        for session_dir in sorted(self._root.iterdir()):
            if not session_dir.is_dir() or session_dir.name.startswith("_"):
                continue
            timeline = session_dir / "timeline.json"
            if not timeline.exists():
                continue
            try:
                entries = json.loads(timeline.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for tl in entries:
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
                batch_entries.append(entry)

                if eng.available:
                    vec = eng.embed(summary)
                    if vec is not None:
                        batch_vectors.append(vec)
                    elif batch_vectors:
                        # 某条 embedding 失败时用零向量占位
                        batch_vectors.append(np.zeros(EMBEDDING_DIM, dtype=np.float32))

        self._entries = batch_entries

        if batch_vectors:
            self._embeddings = np.vstack(
                [v.astype(np.float32).reshape(1, -1) for v in batch_vectors]
            )
            self._dirty_emb = True

        self._save_embeddings()
        self._save_entries()

        logger.info(f"搜索索引重建完成：{len(self._entries)} 条记录")
        return len(self._entries)

    # ── 统计 ────────────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        return len(self._entries)

    @property
    def has_embeddings(self) -> bool:
        return self._embeddings is not None and len(self._embeddings) > 0

    # ── 内部 ────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_embedding(text: str) -> np.ndarray | None:
        """计算单条摘要的 embedding（引擎不可用时返回 None）。"""
        eng = _get_embedding_engine()
        if eng.available:
            return eng.embed(text)
        return None


# ── embedding 引擎引用（延迟导入，避免循环依赖）────────────────────────────

def _get_embedding_engine():
    """延迟导入 EmbeddingEngine，避免循环依赖。"""
    from core.context.embeddings import get_embedding_engine
    return get_embedding_engine()


# ── 模块级单例 ────────────────────────────────────────────────────────────

_search_index: SearchIndex | None = None


def get_search_index(sessions_root: Path | None = None) -> SearchIndex:
    """获取模块级 SearchIndex 单例。

    首次调用时必须提供 sessions_root；后续调用可省略。
    """
    global _search_index
    if _search_index is None:
        if sessions_root is None:
            from core.context.ingester import SESSIONS_ROOT
            sessions_root = SESSIONS_ROOT
        _search_index = SearchIndex(sessions_root)
    return _search_index
