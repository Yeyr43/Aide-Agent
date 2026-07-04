"""search_memory — 搜索 Aide 记忆数据。

委托给 core.memory.recall 共享搜索引擎，含同义词扩展 + 会话+条目双层搜索。
"""

from core.locale import t


async def execute(arguments: dict) -> str:
    """搜索记忆数据。

    Args:
        arguments: {"query": str}

    Returns:
        匹配结果摘要
    """
    query = arguments.get("query", "").strip()
    if not query:
        return t("tool.search_memory.empty_query")

    # 委托给共享搜索引擎（含同义词扩展 + timeline + overview + 条目）
    from core.memory.recall import recall as search_recall

    matches = await search_recall(query, max_results=10)

    # ── 格式化输出 ──
    if not matches:
        return t("tool.search_memory.no_match", query=query)

    lines = [t("tool.search_memory.found", n=len(matches), query=query)]
    for m in matches:
        snippet = m["snippet"].replace("\n", " ")
        lines.append(f"\n{m['source']}\n  {snippet}")

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
