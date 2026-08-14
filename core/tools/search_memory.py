"""search_memory — 搜索 Aide 记忆数据。

委托给 core.memory.recall 共享搜索引擎，含同义词扩展 + 会话+条目双层搜索。
"""

import logging

from core.locale import t
from .definition import ToolDefinition
from core.memory.recall import recall as search_recall

logger = logging.getLogger(__name__)


async def execute(arguments: dict, ctx=None) -> str:
    """搜索记忆数据。

    Args:
        arguments: {"query": str}
        ctx: ToolContext（由 ToolRegistry 自动注入）

    Returns:
        匹配结果摘要
    """
    query = arguments.get("query", "").strip()
    if not query:
        return t("tool.search_memory.empty_query")

    # 从 ToolContext 提取 search_index（如果可用）
    search_index = getattr(ctx, 'search_index', None) if ctx else None

    # 委托给共享搜索引擎（含同义词扩展 + timeline + overview + 记忆文件）
    try:
        matches = await search_recall(query, max_results=10, search_index=search_index)
    except Exception as e:
        logger.warning("search_memory recall failed: %s", e, exc_info=True)
        return t("tool.search_memory.no_match", query=query)

    # ── 格式化输出 ──
    if not matches:
        return t("tool.search_memory.no_match", query=query)

    lines = [t("tool.search_memory.found", n=len(matches), query=query)]
    for m in matches:
        try:
            snippet = m.get("snippet", "").replace("\n", " ")
            source = m.get("source", "unknown")
            lines.append(f"\n{source}\n  {snippet}")
        except Exception:
            logger.debug("search_memory skipping malformed entry")
            continue

    return "\n".join(lines)


# ── JSON Schema ───────────────────────────────────────────────────

schema = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "搜索关键词，会匹配记忆条目和会话总览中的内容",
        },
    },
    "required": ["query"],
}


definition = ToolDefinition(
    name="search_memory",
    description=t("tool_desc.search_memory"),
    parameters=schema,
    execute=execute,
)
