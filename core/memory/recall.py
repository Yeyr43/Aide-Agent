"""记忆召回 — 跨会话搜索 + 相关性排序。

复用 relevance.py 的 tokenizer + TF-IDF + 同义词扩展。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from core.context.overview import parse_overview_md, read_current_overview
from core.context.relevance import _tokenize, _tfidf_score, _expand_query, get_vocab_index, time_decay
from core.locale import t
from core.memory import MEMORY_FILES
from core.sessions.manager import read_session_meta
from core.setup import aide_dir

logger = logging.getLogger(__name__)


async def recall(
    query: str,
    aide_root: Path | None = None,
    agent_root: Path | None = None,
    max_results: int = 10,
    max_sessions: int = 50,
    search_index: object | None = None,
) -> list[dict]:
    """搜索记忆数据，返回相关结果。

    两阶段搜索：
      1. 全局搜索索引（SearchIndex，从 timeline 重建）快速筛选候选会话
      2. 对候选会话读 meta.json + overview.md 补充细节
      3. 搜索 agent/*.md 记忆文件

    Args:
        query: 搜索关键词
        aide_root: ~/.aide/ 根目录
        agent_root: agent/ 目录（用于搜索 .md 记忆文件）
        max_results: 最大返回条数（默认 10）
        max_sessions: 搜索索引无结果时的 fallback 扫描上限
        search_index: SearchIndex 实例（由 ToolContext 注入）。

    Returns:
        匹配结果列表，每项: {"source": str, "snippet": str, "score": float}
    """
    if aide_root is None:
        aide_root = aide_dir()
    if agent_root is None:
        agent_root = aide_root / "agent"

    vocab = get_vocab_index()
    keywords = _expand_query(query, vocab=vocab.vocab)
    matches: list[dict] = []
    sessions_root = aide_root / "sessions"

    # 1. 搜索索引快速筛选 → 获取候选会话
    matched_session_ids: set[str] = set()
    if sessions_root.exists():
        try:
            search_idx = search_index
            idx_results = await search_idx.search(query, top_k=20)
            for r in idx_results:
                matches.append({
                    "source": f"[会话 {r.session_id} / 轮 {r.turn}]",
                    "snippet": r.summary[:200],
                    "score": r.score + 1.0,
                    "_session_dir": r.session_id,
                })
                matched_session_ids.add(r.session_id)
        except Exception:
            logger.debug("搜索索引不可用，回退到目录扫描")

    # 2. 对匹配到的会话补充 meta.json + overview.md 细节
    for session_id in matched_session_ids:
        session_dir = sessions_root / session_id
        if session_dir.is_dir():
            _enrich_session(session_dir, keywords, matches)

    # 3. Fallback：索引为空时扫描最近 N 个会话目录
    if not matched_session_ids and sessions_root.exists():
        session_count = 0
        for session_dir in sorted(sessions_root.iterdir(), reverse=True):
            if not session_dir.is_dir():
                continue
            _search_session(session_dir, keywords, matches)
            session_count += 1
            if session_count >= max_sessions:
                break

    # 4. 搜索记忆文件
    _search_memory_files(agent_root, keywords, matches)

    # 5. 保存原始关键词分数 → TF-IDF 重排序 → 清理内部字段 → 截断
    for m in matches:
        m["_keyword_score"] = m["score"]
    matches = _tfidf_rank(query, matches, vocab=vocab.vocab)
    for m in matches:
        m.pop("_keyword_score", None)
        m.pop("_session_dir", None)
    return matches[:max_results]


def _search_session(session_dir: Path, keywords: set[str], matches: list[dict]) -> None:
    """搜索一个会话目录（meta.json + timeline.json + overview.md）。"""
    # meta.json（统一走 read_session_meta，损坏自动回退空 dict）
    meta = read_session_meta(session_dir)
    name = meta.get("name", "")
    score = _keyword_score(name, keywords)
    if score > 0:
        matches.append({
            "source": f"[会话 {session_dir.name}]",
            "snippet": f"会话：{name}",
            "score": score * 1.5,  # meta 加权
            "_session_dir": session_dir.name,
        })

    # timeline.json
    timeline_path = session_dir / "timeline.json"
    if timeline_path.exists():
        try:
            from core.storage import read_jsonl
            data = read_jsonl(timeline_path)
            _search_timeline(data, keywords, session_dir.name, matches)
        except (json.JSONDecodeError, OSError):
            logger.debug("Failed to read timeline.json for session %s, skipping", session_dir.name)

    # overview（overview.json 当前版，兼容旧 overview.md）
    text = _read_session_overview(session_dir)
    if text:
        try:
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
        except Exception:
            logger.debug("Failed to read/parse overview for session %s, skipping", session_dir.name)


def _read_session_overview(session_dir: Path) -> str:
    """读取会话当前总览（overview.json 最后一条检查点，兼容旧 overview.md）。"""
    return read_current_overview(session_dir)


def _enrich_session(session_dir: Path, keywords: set[str], matches: list[dict]) -> None:
    """补充 meta.json + overview.md 细节（timeline 已由搜索索引覆盖）。"""
    # meta.json（统一走 read_session_meta）
    meta = read_session_meta(session_dir)
    name = meta.get("name", "")
    score = _keyword_score(name, keywords)
    if score > 0:
        matches.append({
            "source": f"[会话 {session_dir.name}]",
            "snippet": f"会话：{name}",
            "score": score * 1.5,
            "_session_dir": session_dir.name,
        })

    # overview（overview.json 当前版，兼容旧 overview.md）
    text = _read_session_overview(session_dir)
    if text:
        try:
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
        except Exception:
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


def _search_memory_files(agent_root: Path, keywords: set[str],
                         matches: list[dict]) -> None:
    """搜索 agent/*.md 记忆文件（偏好、工作流、长记忆）。"""
    for key, fname in MEMORY_FILES.items():
        label = t(f"mem.label_{key}")
        path = agent_root / fname
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8")
            for line in content.split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                score = _keyword_score(line, keywords)
                if score > 0:
                    matches.append({
                        "source": f"[{label}]",
                        "snippet": line[:200],
                        "score": score,
                    })
        except OSError:
            logger.debug("Failed to read memory file %s, skipping", fname)


def _keyword_score(text: str, keywords: set[str], vocab: frozenset[str] | None = None) -> float:
    """Score text against query keywords using word-level token + bigram overlap.

    Uses the shared tokenizer from core.context.relevance for consistent
    word-level matching (CJK max-forward-match + ASCII word extraction),
    with char 2-gram fallback for out-of-vocabulary terms.
    """
    text_tokens, text_bigrams = _tokenize(text, vocab=vocab)
    keywords_lower = {k.lower() for k in keywords}

    # Collect all matchable terms: word tokens + char bigrams
    text_terms = {t.lower() for t in text_tokens} | {b.lower() for b in text_bigrams}
    if not text_terms:
        return 0.0

    overlap = text_terms & keywords_lower
    if not overlap:
        return 0.0

    score = float(len(overlap))

    # Header bonus: first-line matches count extra
    lines = text.split("\n")
    if lines and lines[0]:
        header_tokens, header_bigrams = _tokenize(lines[0])
        header_terms = {t.lower() for t in header_tokens} | {b.lower() for b in header_bigrams}
        header_matches = len(header_terms & keywords_lower)
        score += header_matches * 1.0

    return score


def _session_time_weight(session_dir_name: str) -> float:
    """Decay weight based on session age. 30-day half-life（统一走 time_decay 公式）。"""
    try:
        ts = datetime.strptime(session_dir_name[:15], "%Y%m%d_%H%M%S")
        age_days = (datetime.now() - ts).days
        return time_decay(age_days)
    except (ValueError, IndexError):
        return 0.5


def _tfidf_rank(query: str, candidates: list[dict], vocab: frozenset[str] | None = None) -> list[dict]:
    """用 word-level TF-IDF 重新排序候选结果。

    从候选 snippet 中动态构建词汇表 + DF 表，
    对每个候选计算 TF-IDF 分数并与原始关键词分数混合。

    Args:
        query: 原始搜索查询
        candidates: 候选结果列表，每项需有 "snippet" 和 "score" 字段
        vocab: 词汇表（None 时使用模块级全局索引）

    Returns:
        按混合分数降序排列的结果列表
    """
    if not candidates or len(candidates) <= 1:
        return candidates

    # Tokenize query
    query_tokens, _ = _tokenize(query, vocab=vocab)
    if not query_tokens:
        return sorted(candidates, key=lambda m: m["score"], reverse=True)

    # 从候选 snippet 动态构建 mini 词汇表 + DF 表
    all_snippets = [c["snippet"] for c in candidates]
    N = len(all_snippets)
    df: dict[str, int] = {}
    for snippet in all_snippets:
        tokens, _ = _tokenize(snippet, vocab=vocab)
        for tok in tokens:
            df[tok] = df.get(tok, 0) + 1

    # TF-IDF 重新评分 + 与原始关键词分数混合
    for c in candidates:
        doc_tokens, _ = _tokenize(c["snippet"], vocab=vocab)
        tfidf = _tfidf_score(query_tokens, doc_tokens, df, N)
        # 混合：TF-IDF 权重 0.7 + 原始关键词分数权重 0.3
        c["score"] = tfidf * 0.7 + c.get("_keyword_score", c["score"]) * 0.3
        # 时间衰减：近期会话权重更高
        session_dir = c.get("_session_dir", "")
        if session_dir:
            c["score"] *= _session_time_weight(session_dir)

    return sorted(candidates, key=lambda m: m["score"], reverse=True)
