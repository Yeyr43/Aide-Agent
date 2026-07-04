"""记忆召回 — 跨会话搜索 + 相关性排序。

P4 Batch 1: 增强关键词匹配，加 synonym map。
P4 Batch 2 将加入时间衰减和语义相似度。
P5: 接入 word-level TF-IDF 评分，复用 relevance.py 的 tokenizer + 评分函数。
    接入 EmbeddingEngine 语义搜索作为可选增强。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import numpy as np

from core.context.embeddings import get_embedding_engine
from core.context.relevance import _tokenize, _tfidf_score
from core.locale import t
from core.setup import aide_dir

logger = logging.getLogger(__name__)

# 同义词映射 — 覆盖常用技术术语与自然语言风格词
SYNONYM_MAP: dict[str, list[str]] = {
    # 原有
    "代码": ["编程", "程序", "脚本", "code", "program", "script"],
    "文件": ["文档", "档案", "file", "document", "读写"],
    "设置": ["配置", "config", "settings", "偏好", "选项", "option"],
    "错误": ["bug", "异常", "error", "问题", "故障", "报错", "exception"],
    # P5 扩展
    "部署": ["deploy", "发布", "上线", "release", "launch"],
    "测试": ["test", "单元测试", "集成测试", "验证", "verify", "check"],
    "数据库": ["database", "db", "查询", "存储", "query", "storage"],
    "前端": ["frontend", "UI", "界面", "网页", "web", "界面"],
    "后端": ["backend", "API", "服务端", "服务器", "server"],
    "性能": ["performance", "速度", "优化", "慢", "speed", "fast"],
    "安全": ["security", "权限", "加密", "认证", "auth", "permission"],
    "日志": ["log", "logging", "记录", "追踪", "trace", "track"],
    "缓存": ["cache", "redis", "memcache", "caching"],
    "容器": ["docker", "container", "k8s", "kubernetes"],
    "版本": ["version", "git", "升级", "更新", "upgrade", "update"],
    "安装": ["install", "setup", "配置环境", "environment"],
    "网络": ["network", "HTTP", "请求", "连接", "request", "connect"],
    "搜索": ["search", "查找", "检索", "grep", "find", "lookup"],
    "简洁": ["简短", "简明", "concise", "直接", "short", "brief"],
    "详细": ["详尽", "详细点", "verbose", "具体", "detail", "specific"],
    "风格": ["偏好", "习惯", "style", "方式", "way", "approach"],
}


def _get_all_synonyms(keyword: str) -> set[str]:
    """Get all synonyms for a keyword from SYNONYM_MAP."""
    kw_lower = keyword.lower()
    for key, synonyms in SYNONYM_MAP.items():
        if kw_lower == key.lower() or kw_lower in (s.lower() for s in synonyms):
            return set(s.lower() for s in synonyms) | {key.lower()}
    return set()


def _expand_query(query: str) -> set[str]:
    """扩展查询词的同义词。"""
    terms = set(query.lower().split())
    query_lower = query.lower()

    # 检查 query 中是否包含任何 key 或 synonym（子串匹配）
    for key, synonyms in SYNONYM_MAP.items():
        key_lower = key.lower()
        all_terms = [key_lower] + [s.lower() for s in synonyms]
        for term in all_terms:
            if term in query_lower or term in terms:
                terms.add(key_lower)
                terms.update(s.lower() for s in synonyms)
                break

    return terms


async def recall(
    query: str,
    aide_root: Path | None = None,
    entry_manager=None,
    max_results: int = 10,
    max_sessions: int = 50,
) -> list[dict]:
    """搜索记忆数据，返回相关结果。

    Args:
        query: 搜索关键词
        aide_root: ~/.aide/ 根目录
        entry_manager: EntryManager 实例（用于搜索条目目录）
        max_results: 最大返回条数（默认 10）
        max_sessions: 最多扫描的会话目录数（默认 50，防止海量会话拖慢搜索）

    Returns:
        匹配结果列表，每项: {"source": str, "snippet": str, "score": float}
    """
    if aide_root is None:
        aide_root = aide_dir()

    keywords = _expand_query(query)
    matches: list[dict] = []

    # 1. 搜索会话数据（限制扫描数量）
    sessions_root = aide_root / "sessions"
    if sessions_root.exists():
        session_count = 0
        for session_dir in sorted(sessions_root.iterdir(), reverse=True):
            if not session_dir.is_dir():
                continue
            _search_session(session_dir, keywords, matches)
            session_count += 1
            if session_count >= max_sessions:
                break

    # 2. 搜索条目目录
    if entry_manager is not None:
        await _search_entries(entry_manager, keywords, matches)

    # 3. 保存原始关键词分数 → TF-IDF 重排序 → 清理内部字段 → 截断
    for m in matches:
        m["_keyword_score"] = m["score"]
    matches = _tfidf_rank(query, matches)
    for m in matches:
        m.pop("_keyword_score", None)
        m.pop("_session_dir", None)
    return matches[:max_results]


def _search_session(session_dir: Path, keywords: set[str], matches: list[dict]) -> None:
    """搜索一个会话目录（meta.json + timeline.json + overview.md）。"""
    # meta.json
    meta_path = session_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            name = meta.get("name", "")
            score = _keyword_score(name, keywords)
            if score > 0:
                matches.append({
                    "source": f"[会话 {session_dir.name}]",
                    "snippet": f"会话：{name}",
                    "score": score * 1.5,  # meta 加权
                    "_session_dir": session_dir.name,
                })
        except (json.JSONDecodeError, OSError):
            pass

    # timeline.json
    timeline_path = session_dir / "timeline.json"
    if timeline_path.exists():
        try:
            data = json.loads(timeline_path.read_text(encoding="utf-8"))
            _search_timeline(data, keywords, session_dir.name, matches)
        except (json.JSONDecodeError, OSError):
            pass

    # overview.md
    overview_path = session_dir / "overview.md"
    if overview_path.exists():
        try:
            from core.context.compactor import parse_overview_md
            text = overview_path.read_text(encoding="utf-8")
            sections = parse_overview_md(text)
            for section_name, items in sections.items():
                for item in items:
                    score = _keyword_score(item, keywords)
                    if score > 0:
                        matches.append({
                            "source": f"[会话 {session_dir.name} / {section_name}]",
                            "snippet": item[:200],
                            "score": score + 1,
                            "_session_dir": session_dir.name,
                        })
        except (OSError, Exception):
            pass


def _search_timeline(data: list, keywords: set[str], session_id: str, matches: list[dict]) -> None:
    """搜索 timeline.json 条目。"""
    for entry in data:
        if not isinstance(entry, dict):
            continue
        summary = entry.get("summary", "")
        score = _keyword_score(summary, keywords)
        if score > 0:
            matches.append({
                "source": f"[会话 {session_id} / 轮 {entry.get('turn','?')}]",
                "snippet": summary[:200],
                "score": score + 1,
                "_session_dir": session_id,
            })


async def _search_entries(entry_manager, keywords: set[str], matches: list[dict]) -> None:
    """搜索条目目录。"""
    for entry_type, label in [
        ("preferences", t("mem.entry_type_preferences")),
        ("workflows", t("mem.entry_type_workflows")),
        ("long_term_memory", t("mem.entry_type_long_term_memory")),
    ]:
        try:
            entries = await entry_manager.load(entry_type)
            for entry in entries:
                content = entry.get("content", "")
                status = entry.get("status", "?")
                score = _keyword_score(content, keywords)
                if score > 0:
                    matches.append({
                        "source": f"[{label}] status={status}",
                        "snippet": content[:200],
                        "score": score,
                    })
        except Exception:
            continue


def _keyword_score(text: str, keywords: set[str]) -> float:
    """Weighted keyword match: header/title matches count more."""
    text_lower = text.lower()
    score = 0.0
    lines = text_lower.split("\n")
    header = lines[0] if lines else ""

    for kw in keywords:
        if kw in header:
            score += 2.0
        elif kw in text_lower:
            score += 1.0
        else:
            # 同义词模糊匹配
            for syn in _get_all_synonyms(kw):
                if syn in text_lower:
                    score += 0.5
                    break
    return score


def _session_time_weight(session_dir_name: str) -> float:
    """Decay weight based on session age. 7-day half-life."""
    try:
        ts = datetime.strptime(session_dir_name[:15], "%Y%m%d_%H%M%S")
        age_days = (datetime.now() - ts).days
        if age_days <= 7:
            return 1.0
        elif age_days <= 30:
            return 0.8
        else:
            return max(0.1, 0.5 ** (age_days / 30))
    except (ValueError, IndexError):
        return 0.5


def _tfidf_rank(query: str, candidates: list[dict]) -> list[dict]:
    """用 word-level TF-IDF 重新排序候选结果。

    从候选 snippet 中动态构建词汇表 + DF 表，
    对每个候选计算 TF-IDF 分数并与原始关键词分数混合。

    Args:
        query: 原始搜索查询
        candidates: 候选结果列表，每项需有 "snippet" 和 "score" 字段

    Returns:
        按混合分数降序排列的结果列表
    """
    if not candidates or len(candidates) <= 1:
        return candidates

    # Tokenize query
    query_tokens, _ = _tokenize(query)
    if not query_tokens:
        return sorted(candidates, key=lambda m: m["score"], reverse=True)

    # 从候选 snippet 动态构建 mini 词汇表 + DF 表
    all_snippets = [c["snippet"] for c in candidates]
    N = len(all_snippets)
    df: dict[str, int] = {}
    for snippet in all_snippets:
        tokens, _ = _tokenize(snippet)
        for t in tokens:
            df[t] = df.get(t, 0) + 1

    # TF-IDF 重新评分 + 与原始关键词分数混合
    for c in candidates:
        doc_tokens, _ = _tokenize(c["snippet"])
        tfidf = _tfidf_score(query_tokens, doc_tokens, df, N)
        # 混合：TF-IDF 权重 0.7 + 原始关键词分数权重 0.3
        c["score"] = tfidf * 0.7 + c.get("_keyword_score", c["score"]) * 0.3
        # 时间衰减：近期会话权重更高
        session_dir = c.get("_session_dir", "")
        if session_dir:
            c["score"] *= _session_time_weight(session_dir)

    # 先按 TF-IDF 排序，嵌入仅增强 top-20（避免上百次 ONNX 推理拖慢搜索）
    candidates.sort(key=lambda m: m["score"], reverse=True)
    emb_boost_count = min(20, len(candidates))

    emb_engine = get_embedding_engine()
    if emb_engine.available and emb_boost_count > 1:
        try:
            q_emb = emb_engine.embed(query[:200])
            if q_emb is not None:
                for c in candidates[:emb_boost_count]:
                    d_emb = emb_engine.embed(c["snippet"][:200])
                    if d_emb is not None:
                        emb_sim = float(np.dot(q_emb, d_emb))
                        # 混合：词法 0.6 + 语义 0.4
                        c["score"] = c["score"] * 0.6 + emb_sim * 0.4
        except Exception:
            pass  # 嵌入失败不影响搜索结果

    return sorted(candidates, key=lambda m: m["score"], reverse=True)
