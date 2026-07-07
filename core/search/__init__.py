"""会话搜索 — 全局向量索引 + 会话内关键词搜索。"""

from .index import SearchIndex, SearchResult, get_search_index

__all__ = ["SearchIndex", "SearchResult", "get_search_index"]
