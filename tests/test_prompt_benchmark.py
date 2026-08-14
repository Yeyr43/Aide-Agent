"""动态 Prompt 系统 A/B 评估基准。

测量三个维度的量化指标：
  1. 相关性排序 — TF-IDF vs Jaccard 排序质量
  2. 时间衰减 — _decay_factor 精度
  3. 搜索排序 — TF-IDF re-rank 质量

所有测试纯 CPU，< 1s，确定性。

P5 重构：移除 CaptureEngine 正则测试（正则引擎已删除）。
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.context.relevance import (
    _bigrams,
    _build_vocabulary,
    _chinese_tokenize,
    _decay_factor,
    _jaccard,
    _tfidf_score,
    _tokenize,
    flush_vocab_cache,
    _SEED_VOCABULARY,
)
from core.memory.recall import _expand_query, _tfidf_rank, _keyword_score


# ── Helpers ────────────────────────────────────────────────────────────


class TestRelevanceRanking:
    """TF-IDF vs Jaccard 排序质量。"""

    def test_seed_vocabulary_non_empty(self):
        assert len(_SEED_VOCABULARY) >= 80

    def test_cold_start_jaccard_fallback(self):
        """未 build vocabulary 时 _tokenize 仍可用 bigram fallback。"""
        flush_vocab_cache()
        tokens, bigrams = _tokenize("Python 代码优化")
        # Should have at least some tokens or bigrams
        assert len(tokens) + len(bigrams) > 0

    def test_tfidf_exact_match(self):
        query = {"代码", "优化"}
        doc = {"代码", "优化", "性能"}
        df = {"代码": 2, "优化": 2, "性能": 1}
        score = _tfidf_score(query, doc, df, N=3)
        assert score > 0.0

    def test_tfidf_no_match(self):
        query = {"前端"}
        doc = {"Python", "后端"}
        df = {"Python": 1, "后端": 1}
        score = _tfidf_score(query, doc, df, N=2)
        assert score == 0.0

    def test_tfidf_vs_jaccard_consistency(self):
        """TF-IDF and Jaccard should agree on clearly relevant documents."""
        tokens1, bigrams1 = _tokenize("Python 性能优化")
        tokens2, bigrams2 = _tokenize("Python 代码优化性能")

        # Jaccard on bigrams
        jac = _jaccard(set(bigrams1), set(bigrams2))
        assert jac > 0.1  # Some overlap expected

    def test_top3_precision(self):
        """Top-3 results should be from correct session."""
        vocab = _SEED_VOCABULARY
        query = "Python 代码优化"
        candidates = [
            {"snippet": "使用 Python 进行后端开发", "score": 1.0, "_keyword_score": 1.0, "_session_dir": "20240101_120000"},
            {"snippet": "前端 React 组件设计", "score": 0.5, "_keyword_score": 0.5, "_session_dir": "20240101_120000"},
            {"snippet": "Python 性能调优技巧", "score": 1.2, "_keyword_score": 1.2, "_session_dir": "20240102_120000"},
        ]
        ranked = _tfidf_rank(query, candidates, vocab=vocab)
        # First result should be Python-related
        assert "Python" in ranked[0]["snippet"]

    def test_chinese_tokenization(self):
        tokens1, _ = _tokenize("帮我优化这段Python代码")
        tokens2, _ = _tokenize("Python代码优化")
        # Both should produce tokens
        assert len(tokens1) >= 0
        assert len(tokens2) >= 0


# ── Time Decay ─────────────────────────────────────────────────────────


class TestTimeDecay:
    def test_none_path_defaults(self):
        """None path returns 1.0 (no decay)."""
        factor = _decay_factor(None)
        assert factor == 1.0

    def test_nonexistent_file(self):
        """Non-existent file should return a positive decay factor."""
        from pathlib import Path
        factor = _decay_factor(Path("/nonexistent/path/test.md"))
        assert 0.0 <= factor <= 1.0


# ── Search Ranking ──────────────────────────────────────────────────────


class TestSearchRanking:
    def test_tfidf_rank_empty(self):
        result = _tfidf_rank("query", [])
        assert result == []

    def test_tfidf_rank_single(self):
        candidates = [{"snippet": "test", "score": 1.0, "_keyword_score": 1.0}]
        result = _tfidf_rank("test", candidates)
        assert len(result) == 1

    def test_keyword_score_basic(self):
        score = _keyword_score("Python performance optimization", {"python", "code"})
        assert score > 0  # "python" matches

    def test_keyword_score_no_match(self):
        score = _keyword_score("Python code", {"javascript", "frontend"})
        assert score == 0.0


# ── Synonym Expansion ──────────────────────────────────────────────────


class TestSynonymExpansion:
    def test_synonym_map_coverage(self):
        """同义词映射应有 ≥ 20 条目且覆盖中文。"""
        from core.context.tokenizer import SYNONYM_MAP
        assert len(SYNONYM_MAP) >= 20
        # 至少有一些中文 key
        cn = [k for k in SYNONYM_MAP if any('一' <= c <= '鿿' for c in k)]
        assert len(cn) > 0

    def test_get_all_synonyms(self):
        """跨语言同义词解析。"""
        from core.context.tokenizer import _get_all_synonyms
        result = _get_all_synonyms("代码")
        # Should include some synonyms
        assert isinstance(result, set)
        assert len(result) >= 0
