"""search_chat — 搜索对话历史。

会话内搜索：直接扫描指定 session 的 timeline.json（关键词 + bigram Jaccard）。
全局搜索：查询预构建的向量索引（_search_index.json + _search_embeddings.npy）。

不加 session_id 时全局搜索，传入 session_id 时限定会话内搜索。
"""

from __future__ import annotations

import json
import logging

from core.locale import t
from core.context.relevance import _bigrams, _jaccard
from core.context.ingester import SESSIONS_ROOT

logger = logging.getLogger(__name__)


async def execute(arguments: dict) -> str:
    """搜索对话历史。

    Args:
        arguments: {"query": str, "top_k": int (可选, 默认 5),
                     "session_id": str (可选, 不传则全局搜索)}
        session_dir: 当前会话目录（由 ToolRegistry 注入，会话内搜索时使用）

    Returns:
        格式化的搜索结果
    """
    query = arguments.get("query", "").strip()
    if not query:
        return t("tool.search_chat.empty_query")

    top_k_raw = arguments.get("top_k", 5)
    try:
        top_k = min(max(int(top_k_raw), 1), 20)
    except (TypeError, ValueError):
        top_k = 5

    sid = arguments.get("session_id", "").strip() or None

    if sid:
        return await _session_search(sid, query, top_k)
    else:
        return await _global_search(query, top_k)


async def _session_search(
    session_id: str, query: str, top_k: int,
) -> str:
    """会话内搜索：子串匹配 + bigram Jaccard。"""
    timeline_path = SESSIONS_ROOT / session_id / "timeline.json"
    if not timeline_path.exists():
        return t("tool.search_chat.session_not_found", session_id=session_id)

    try:
        entries = json.loads(timeline_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return t("tool.search_chat.session_read_error")

    if not entries:
        return t("tool.search_chat.no_results")

    query_lower = query.lower()
    q_bigrams = _bigrams(query)

    scored: list[tuple[dict, float]] = []
    for e in entries:
        summary = e.get("summary", "")
        score = 0.0

        # 精确子串匹配（权重高）
        if query_lower in summary.lower():
            score += 0.6

        # Bigram Jaccard
        jac = _jaccard(q_bigrams, _bigrams(summary))
        score += jac * 0.4

        if score > 0.05:
            scored.append((e, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    if not scored:
        return t("tool.search_chat.no_match", query=query)

    lines = [
        t("tool.search_chat.session_header",
          session_id=session_id, total=len(scored))
    ]
    for i, (e, score) in enumerate(scored[:top_k]):
        turn = e.get("turn", "?")
        summary = e.get("summary", "")
        lines.append(
            f"{i + 1}. [Turn {turn}] {summary} "
            f"（{t('tool.search_chat.match')}: {score:.2f}）"
        )

    return "\n".join(lines)


async def _global_search(query: str, top_k: int) -> str:
    """全局搜索：语义向量索引 + Jaccard fallback。"""
    from core.search import get_search_index

    index = get_search_index()
    if index.size == 0:
        return t("tool.search_chat.index_empty")

    results = await index.search(query, top_k=top_k)

    if not results:
        return t("tool.search_chat.no_match", query=query)

    lines = [t("tool.search_chat.global_header", total=len(results))]
    for i, r in enumerate(results):
        lines.append(
            f"{i + 1}. [{r.session_id}#{r.turn}] {r.summary} "
            f"（{t('tool.search_chat.score')}: {r.score:.2f}）"
        )

    return "\n".join(lines)


# ── JSON Schema ───────────────────────────────────────────────────

schema = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "搜索查询（自然语言或关键词）。例如：'Docker 部署'、'上次讨论的配置方案'",
        },
        "top_k": {
            "type": "integer",
            "description": "返回结果数（默认 5，最大 20）",
        },
        "session_id": {
            "type": "string",
            "description": (
                "限定会话 ID（可选，不传则全局搜索所有会话）。"
                "会话 ID 格式为 YYYYMMDD_HHMMSS，可从搜索结果或会话列表中获取。"
            ),
        },
    },
    "required": ["query"],
}
