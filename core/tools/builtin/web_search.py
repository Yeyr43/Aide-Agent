"""web_search — 联网搜索（DuckDuckGo，免费、无需配置）。

安全限制：单次最多 10 条结果，15 秒超时。
"""

import asyncio

from ddgs import DDGS

from core.locale import t

_SEARCH_TIMEOUT = 15.0       # 搜索硬超时（秒）
_MAX_RESULTS = 10             # 结果数硬上限


async def execute(arguments: dict) -> str:
    """执行 DuckDuckGo 搜索。

    Args:
        arguments: {"query": str, "num": int (可选，默认 5)}

    Returns:
        搜索结果摘要
    """
    query = arguments.get("query", "").strip()
    if not query:
        return t("tool.web_search.empty_query")

    num = arguments.get("num", 5)
    if not isinstance(num, int) or num < 1:
        num = 5
    num = min(num, _MAX_RESULTS)

    try:
        results = await asyncio.wait_for(
            asyncio.to_thread(_search, query, num),
            timeout=_SEARCH_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return t("tool.web_search.timeout", timeout=_SEARCH_TIMEOUT)
    except Exception as e:
        return t("tool.web_search.failed", e=e)

    if not results:
        return t("tool.web_search.no_results", query=query)

    lines = [t("tool.web_search.results_for", query=query) + "\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", t("tool.web_search.untitled"))
        url = r.get("href", r.get("url", ""))
        snippet = r.get("body", r.get("snippet", ""))[:200]
        lines.append(f"{i}. {title}\n   {url}\n   {snippet}\n")

    return "\n".join(lines)


def _search(query: str, num: int) -> list[dict]:
    """同步调用 DuckDuckGo（在线程池中运行）。"""
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=num))


# ── JSON Schema ───────────────────────────────────────────────────

schema = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "搜索查询词",
        },
        "num": {
            "type": "integer",
            "description": "返回结果数量（默认 5，最大 10）",
        },
    },
    "required": ["query"],
}
