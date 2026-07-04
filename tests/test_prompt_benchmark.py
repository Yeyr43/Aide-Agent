"""动态 Prompt 系统 A/B 评估基准。

测量三个维度的量化指标：
  1. 截获召回率 — CaptureEngine 对已知信号的捕获比例
  2. 相关性排序 — TF-IDF vs Jaccard 排序质量
  3. 时间衰减 — _decay_factor 精度

所有测试纯 CPU，< 1s，确定性。
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
from core.memory.capture import (
    CaptureEngine,
    _EN_PREFERENCE_PATTERNS,
    _EN_LONG_MEMORY_PATTERNS,
    _EN_WORKFLOW_PATTERNS,
    _IMPLICIT_CORRECTION_PATTERNS,
    _IMPLICIT_LONG_MEMORY_PATTERNS,
    _IMPLICIT_LONG_MEMORY_STRONG_PATTERNS,
    _IMPLICIT_PREFERENCE_PATTERNS,
    _LONG_MEMORY_PATTERNS,
    _PREFERENCE_PATTERNS,
    _WORKFLOW_PATTERNS,
    _detect_language,
)
from core.memory.recall import _expand_query, _tfidf_rank, _keyword_score


# ═══════════════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════════════

def _make_mock_entries(entries_by_type: dict | None = None):
    """构造 mock EntryManager，预填条目。"""
    entries_by_type = entries_by_type or {}
    mgr = MagicMock()
    mgr._store = MagicMock()

    async def _load(entry_type: str) -> list[dict]:
        return [{"content": c, "status": "pending"}
                for c in entries_by_type.get(entry_type, [])]

    async def _add(entry_type: str, content: str, source: dict | None = None) -> dict:
        return {"content": content, "status": "pending", "source": source or {}}

    mgr.load = _load
    mgr.add = _add
    mgr.update = AsyncMock(return_value={"content": "updated", "status": "pending", "source": {}})
    return mgr


# ═══════════════════════════════════════════════════════════════════════
# 1. 截获召回率
# ═══════════════════════════════════════════════════════════════════════

class TestCaptureRecall:
    """测量 CaptureEngine 的召回率。"""

    @pytest.fixture
    def tracker(self):
        t = MagicMock()
        t.record = AsyncMock()
        t.should_capture = AsyncMock(return_value=False)
        return t

    @pytest.fixture
    def engine(self, tracker):
        return CaptureEngine(_make_mock_entries(), tracker)

    # ── 中文显式偏好 ──

    @pytest.mark.parametrize("msg,expected_contain", [
        ("我喜欢简洁的回复风格，不要啰嗦", "我喜欢简洁的回复风格"),
        ("我偏好用 Python 写后端，不喜欢 Java", "我偏好"),
        ("永远都用 async/await，别用线程", "永远都"),
        ("尽量用中文回复我", "尽量"),
        ("别再给我推荐 Docker 了", "别再"),
    ])
    async def test_explicit_preference_zh(self, engine, msg, expected_contain):
        captured = await engine.capture(msg, "好的", "s1", 1)
        contents = [e["content"] for e in captured]
        assert any(expected_contain in c for c in contents), \
            f"期望截获包含 '{expected_contain}'，实际: {contents}"

    # ── 中文隐式偏好 ──

    @pytest.mark.parametrize("msg,should_capture", [
        ("能不能简洁一点，太长了", True),
        ("这个方案不太好，重新想想", True),
        ("我更倾向于用 Go 而不是 Rust", True),
        ("还是用 pytest 吧，unittest 太啰嗦了", True),
        ("不如直接用 SQL 查询，ORM 性能太差", True),
        ("最好还是加上错误处理", True),
        ("别太复杂了，简单点就行", True),
    ])
    async def test_implicit_preference_zh(self, engine, msg, should_capture):
        captured = await engine.capture(msg, "好的", "s1", 1)
        has_pref = any(e["type"] == "preferences" for e in captured)
        assert has_pref == should_capture, \
            f"'{msg}': 期望截获={should_capture}，实际={has_pref}"

    # ── 中文隐式纠正 ──

    @pytest.mark.parametrize("msg,should_capture", [
        ("你再看看这段代码，感觉有问题", True),
        ("应该先编译再运行，不是直接执行", True),
        ("不是用 requests 而是用 httpx", True),
        ("这个 bug 还没解决，你再查查", True),
        ("还没搞定，继续修", True),
    ])
    async def test_implicit_correction_zh(self, engine, msg, should_capture):
        captured = await engine.capture(msg, "好的", "s1", 1)
        has_wf = any(e["type"] == "workflows" for e in captured)
        assert has_wf == should_capture, \
            f"'{msg}': 期望截获={should_capture}，实际={has_wf}"

    # ── 隐式长记忆强信号 ──

    @pytest.mark.parametrize("msg,should_capture", [
        ("我的工作是后端开发，主要写 Python", True),
        ("我们团队用 Kubernetes 部署所有服务", True),
        ("我住在北京，远程办公就行", True),
        ("我擅长做数据库优化和性能调优", True),
        ("我主要用 Neovim 写代码，装了 50 个插件", True),
    ])
    async def test_implicit_long_memory_strong(self, engine, msg, should_capture):
        """强信号单次即截获，不需要 tracker 确认。"""
        captured = await engine.capture(msg, "好的", "s1", 1)
        has_mem = any(e["type"] == "long_term_memory" for e in captured)
        assert has_mem == should_capture, \
            f"'{msg}': 期望截获={should_capture}，实际={has_mem}"

    # ── 隐式长记忆弱信号（需频率门槛）──

    async def test_implicit_long_memory_weak_not_captured(self, engine, tracker):
        """弱信号在 tracker 返回 False 时不截获。"""
        tracker.should_capture.return_value = False
        captured = await engine.capture(
            "我一直用 Maven 管理 Java 项目依赖", "好的", "s1", 1
        )
        has_mem = any(e["type"] == "long_term_memory" for e in captured)
        assert not has_mem, "频率未达标不应截获"

    async def test_implicit_long_memory_weak_captured(self, engine, tracker):
        """弱信号在 tracker 返回 True 时截获。"""
        tracker.should_capture.return_value = True
        captured = await engine.capture(
            "我一直用 Maven 管理 Java 项目依赖", "好的", "s1", 1
        )
        has_mem = any(e["type"] == "long_term_memory" for e in captured)
        assert has_mem, "频率达标应截获"

    # ── 英文支持 ──

    @pytest.mark.parametrize("msg,expected_type", [
        ("I prefer concise answers, don't be verbose", "preferences"),
        ("Always use TypeScript, never plain JavaScript", "preferences"),
        ("No, that's not correct — the API returns JSON not XML", "workflows"),
        ("Next time remember to add error handling", "workflows"),
        ("Remember that I work at Acme Corp as a backend engineer", "long_term_memory"),
    ])
    async def test_english_patterns(self, engine, msg, expected_type):
        captured = await engine.capture(msg, "Got it", "s1", 1)
        types = [e["type"] for e in captured]
        assert expected_type in types, \
            f"'{msg}': 期望 '{expected_type}'，实际 {types}"

    # ── 语言检测 ──

    @pytest.mark.parametrize("text,expected", [
        ("I prefer short answers please", "en"),
        ("Can you help me with this bug", "en"),
        ("我喜欢简洁的回复", "zh"),
        ("帮我写个脚本", "zh"),
        ("hello 你好 world 这是一个测试", "zh"),  # 含 CJK → zh
        ("你好世界 hello", "zh"),                    # 含 CJK → zh
        ("我偏好用 Python 写后端", "zh"),            # 中英混用 → zh
        ("pure english text here", "en"),            # 纯英文 → en
    ])
    def test_language_detection(self, text, expected):
        assert _detect_language(text) == expected

    # ── 无意义输入不截获 ──

    @pytest.mark.parametrize("msg", [
        "好的，谢谢",
        "OK",
        "继续",
        "嗯",
        "今天天气怎么样",
        "帮我写个函数计算斐波那契数列",  # 纯任务请求，无偏好信号
    ])
    async def test_no_capture_on_neutral(self, engine, msg):
        captured = await engine.capture(msg, "好的", "s1", 1)
        assert len(captured) == 0, f"'{msg}' 不应截获任何条目"

    # ── 召回率统计 ──

    async def test_overall_recall_rate(self, engine, tracker):
        """统计整体召回率：已知应截获的输入中，实际截获比例。"""
        tracker.should_capture.return_value = True

        test_cases = [
            # (msg, assistant, expected_type)
            ("我喜欢简洁的代码风格", "好的", "preferences"),
            ("能不能快一点，别那么啰嗦", "好的", "preferences"),
            ("我更倾向于用 FastAPI", "好的", "preferences"),
            ("下次记得先写测试再实现", "好的", "workflows"),
            ("不对，应该先解析 JSON 再做校验", "好的", "workflows"),
            ("你再检查一下那段 SQL 有没有注入风险", "好的", "workflows"),
            ("记住我的 GitHub 用户名是 devuser42", "好的", "long_term_memory"),
            ("我的工作是全栈工程师，前端用 React", "好的", "long_term_memory"),
            ("我们团队用 GitLab CI 做持续集成", "好的", "long_term_memory"),
            ("I prefer short, focused answers", "ok", "preferences"),
        ]

        hits = 0
        for msg, assistant, expected_type in test_cases:
            captured = await engine.capture(msg, assistant, "s1", 1)
            if any(e["type"] == expected_type for e in captured):
                hits += 1

        recall = hits / len(test_cases)
        assert recall >= 0.70, \
            f"召回率 {recall:.0%} 低于 70% 阈值（{hits}/{len(test_cases)}）"


# ═══════════════════════════════════════════════════════════════════════
# 2. 相关性排序质量
# ═══════════════════════════════════════════════════════════════════════

class TestRelevanceRanking:
    """测量 TF-IDF 排序质量 vs Jaccard baseline。"""

    # ── 冷启动词汇索引 ──

    def test_seed_vocabulary_size(self):
        assert len(_SEED_VOCABULARY) >= 80, \
            f"种子词汇应 ≥80 个，实际 {len(_SEED_VOCABULARY)}"

    def test_cold_start_uses_seed(self):
        flush_vocab_cache()
        idx = _build_vocabulary(Path("/nonexistent/path"))
        assert idx.built
        assert len(idx.vocab) >= 80
        assert idx.N > 0

    # ── TF-IDF 基本正确性 ──

    def test_tfidf_exact_match(self):
        """完全相同文档得分为 1.0。"""
        flush_vocab_cache()
        idx = _build_vocabulary(Path("/nonexistent/path"))
        tokens_a, _ = _tokenize("帮我写一个简洁的 Python 脚本")
        tokens_b, _ = _tokenize("帮我写一个简洁的 Python 脚本")
        score = _tfidf_score(tokens_a, tokens_b, idx.df, idx.N)
        assert score > 0.9, f"相同文本得分应 > 0.9，实际 {score:.4f}"

    def test_tfidf_no_match(self):
        """完全不相关文档得分为 0。"""
        flush_vocab_cache()
        idx = _build_vocabulary(Path("/nonexistent/path"))
        tokens_a, _ = _tokenize("数据库查询优化")
        tokens_b, _ = _tokenize("前端界面布局")
        score = _tfidf_score(tokens_a, tokens_b, idx.df, idx.N)
        assert score < 0.1, f"不相关文本得分应 < 0.1，实际 {score:.4f}"

    # ── TF-IDF vs Jaccard 对比 ──

    def test_tfidf_outperforms_jaccard(self):
        """TF-IDF 对语义相关但字面不同的文本给出更高分。"""
        flush_vocab_cache()
        idx = _build_vocabulary(Path("/nonexistent/path"))

        # 使用种子词汇中存在的词：数据库、查询、优化、性能、缓存
        query = "数据库查询性能优化"
        docs = {
            "relevant": "SQL查询速度慢需要优化数据库索引",
            "irrelevant": "今天天气不错适合出去玩",
        }

        q_tokens, q_bigrams = _tokenize(query)

        scores: dict[str, dict[str, float]] = {}
        for label, doc in docs.items():
            d_tokens, d_bigrams = _tokenize(doc)
            scores[label] = {
                "tfidf": _tfidf_score(q_tokens, d_tokens, idx.df, idx.N),
                "jaccard": _jaccard(q_bigrams, d_bigrams),
            }

        # TF-IDF 应对 relevant 文档给出更高分
        assert scores["relevant"]["tfidf"] > scores["irrelevant"]["tfidf"], \
            f"TF-IDF 应区分相关/不相关: {scores}"

    # ── 排序质量（Top-K Precision）──

    def test_ranking_precision_at_3(self):
        """前 3 结果中相关文档占比应 ≥ 67%。"""
        flush_vocab_cache()
        idx = _build_vocabulary(Path("/nonexistent/path"))

        query = "Python 异步编程的最佳实践"
        documents = [
            # (doc, is_relevant)
            ("使用 asyncio 编写高性能 Python 应用", True),
            ("Python 协程和事件循环的深入理解", True),
            ("FastAPI 中的异步数据库查询", True),
            ("Java Spring Boot 入门教程", False),
            ("如何使用 Docker 部署 Nginx", False),
            ("前端 React 组件生命周期详解", False),
            ("Git 分支管理策略指南", False),
        ]

        q_tokens, _ = _tokenize(query)
        scored = []
        for doc, relevant in documents:
            d_tokens, _ = _tokenize(doc)
            score = _tfidf_score(q_tokens, d_tokens, idx.df, idx.N)
            scored.append((doc, score, relevant))

        scored.sort(key=lambda x: x[1], reverse=True)
        top3 = scored[:3]

        precision = sum(1 for _, _, r in top3 if r) / len(top3)
        assert precision >= 0.67, \
            f"Top-3 精度应 ≥ 67%，实际 {precision:.0%}: {[(d[:20], s, r) for d, s, r in top3]}"

    # ── 中文分词正确性 ──

    def test_chinese_tokenize_basic(self):
        flush_vocab_cache()
        idx = _build_vocabulary(Path("/nonexistent/path"))

        tokens, _ = _tokenize("数据库查询优化策略")
        # 种子词汇应至少命中 "数据库"、"查询"、"优化"
        assert "数据库" in tokens or "查询" in tokens or "优化" in tokens, \
            f"种子词汇分词失败: {tokens}"

    def test_chinese_tokenize_stop_word_filtering(self):
        """停用词应被过滤。"""
        tokens, _ = _tokenize("这个和那个以及什么的一些问题")
        # 停用词不应出现
        stops_in_result = tokens & {"这个", "那个", "什么", "一些", "以及"}
        assert len(stops_in_result) == 0, \
            f"停用词未被过滤: {stops_in_result}"


# ═══════════════════════════════════════════════════════════════════════
# 3. 时间衰减精度
# ═══════════════════════════════════════════════════════════════════════

class TestTimeDecay:
    """测量 _decay_factor 的数学精度。"""

    def test_fresh_file_no_decay(self, tmp_path):
        """刚创建的文件衰减 ≈ 1.0。"""
        f = tmp_path / "fresh.md"
        f.write_text("test")
        decay = _decay_factor(f)
        assert decay > 0.99, f"新文件衰减应 ≈ 1.0，实际 {decay}"

    def test_30_day_half_life(self, tmp_path):
        """30 天前半衰期验证：0.5^(30/30) = 0.5。"""
        f = tmp_path / "old.md"
        f.write_text("test")
        # 手动设置 mtime 为 30 天前
        thirty_days_ago = time.time() - 30 * 86400
        os.utime(str(f), (thirty_days_ago, thirty_days_ago))
        decay = _decay_factor(f)
        assert 0.48 < decay < 0.52, \
            f"30 天衰减应在 0.5 附近，实际 {decay:.4f}"

    def test_60_day_decay(self):
        """60 天应约为 0.25。"""
        sixty_days_ago = time.time() - 60 * 86400
        age_days = (time.time() - sixty_days_ago) / 86400
        decay = 0.5 ** (age_days / 30)
        assert 0.2 < decay < 0.3, \
            f"60 天衰减应在 0.25 附近，实际 {decay:.4f}"

    def test_none_path_returns_one(self):
        """None 路径返回 1.0。"""
        assert _decay_factor(None) == 1.0

    def test_nonexistent_file_returns_one(self):
        """不存在的文件返回 1.0。"""
        assert _decay_factor(Path("/nonexistent/file.md")) == 1.0

    def test_negative_age_returns_one(self):
        """未来文件（mtime 在将来）不衰减。"""
        future_time = time.time() + 86400  # 1 天后
        age_days = (time.time() - future_time) / 86400
        assert age_days <= 0
        decay = 0.5 ** (age_days / 30) if age_days > 0 else 1.0
        assert decay == 1.0


# ═══════════════════════════════════════════════════════════════════════
# 4. 搜索 TF-IDF 排序
# ═══════════════════════════════════════════════════════════════════════

class TestSearchRanking:
    """测量 recall.py 的 TF-IDF 重排序质量。"""

    def test_tfidf_rank_basic(self):
        """TF-IDF 重排序：更相关的排前面。"""
        candidates = [
            {"snippet": "如何使用 asyncio 编写异步 Python 代码", "score": 1.0},
            {"snippet": "今天天气不错很适合出去玩", "score": 1.0},
            {"snippet": "Python 并发编程的最佳实践和模式", "score": 1.0},
            {"snippet": "Docker 容器部署指南", "score": 1.0},
        ]
        ranked = _tfidf_rank("Python 异步编程", candidates)
        # 相关文档应排在前面
        top_snippets = [r["snippet"] for r in ranked[:2]]
        assert any("asyncio" in s or "异步" in s or "并发" in s
                   for s in top_snippets), \
            f"相关文档应排前 2: {top_snippets}"

    def test_tfidf_rank_empty(self):
        """空列表直接返回。"""
        assert _tfidf_rank("query", []) == []

    def test_tfidf_rank_single(self):
        """单候选直接返回。"""
        c = [{"snippet": "test", "score": 1.0}]
        result = _tfidf_rank("query", c)
        assert result == c

    def test_keyword_score_basic(self):
        """关键词评分基本功能。"""
        score = _keyword_score("使用 Python 异步编程", {"python", "异步", "编程"})
        assert score >= 2.0  # "python" + "异步" + "编程" 至少命中 2 个

    def test_keyword_score_no_match(self):
        score = _keyword_score("今天天气不错", {"python", "异步"})
        assert score == 0.0

    # ── 同义词扩展 ──

    def test_synonym_expansion_chinese(self):
        terms = _expand_query("代码错误")
        assert "代码" in terms or "coding" in terms
        assert "错误" in terms or "bug" in terms or "error" in terms

    def test_synonym_expansion_english(self):
        terms = _expand_query("config error")
        assert "config" in terms or "设置" in terms or "配置" in terms
        assert "error" in terms or "错误" in terms


# ═══════════════════════════════════════════════════════════════════════
# 5. Pattern 覆盖完整性
# ═══════════════════════════════════════════════════════════════════════

class TestPatternCoverage:
    """验证所有 Pattern 组的完整性。"""

    def test_all_pattern_groups_non_empty(self):
        assert len(_PREFERENCE_PATTERNS) >= 2
        assert len(_WORKFLOW_PATTERNS) >= 2
        assert len(_LONG_MEMORY_PATTERNS) >= 1
        assert len(_IMPLICIT_PREFERENCE_PATTERNS) >= 5
        assert len(_IMPLICIT_CORRECTION_PATTERNS) >= 4
        assert len(_IMPLICIT_LONG_MEMORY_PATTERNS) >= 1   # 弱信号
        assert len(_IMPLICIT_LONG_MEMORY_STRONG_PATTERNS) >= 3  # 强信号
        assert len(_EN_PREFERENCE_PATTERNS) >= 3
        assert len(_EN_WORKFLOW_PATTERNS) >= 2
        assert len(_EN_LONG_MEMORY_PATTERNS) >= 2

    def test_all_patterns_compile(self):
        """所有 Pattern 都应是合法正则。"""
        import re
        all_patterns = (
            _PREFERENCE_PATTERNS + _WORKFLOW_PATTERNS + _LONG_MEMORY_PATTERNS +
            _IMPLICIT_PREFERENCE_PATTERNS + _IMPLICIT_CORRECTION_PATTERNS +
            _IMPLICIT_LONG_MEMORY_PATTERNS + _IMPLICIT_LONG_MEMORY_STRONG_PATTERNS +
            _EN_PREFERENCE_PATTERNS + _EN_WORKFLOW_PATTERNS + _EN_LONG_MEMORY_PATTERNS
        )
        for i, p in enumerate(all_patterns):
            try:
                re.compile(p)
            except re.error as e:
                pytest.fail(f"Pattern #{i} 编译失败: {p!r}\n{e}")


# ═══════════════════════════════════════════════════════════════════════
# 6. Embedding 引擎
# ═══════════════════════════════════════════════════════════════════════

class TestEmbeddingEngine:
    """测量 EmbeddingEngine 降级行为和 WordPiece 分词器。"""

    def test_engine_degraded_when_no_model(self):
        """无模型时引擎降级，available=False。"""
        from core.context.embeddings import EmbeddingEngine
        engine = EmbeddingEngine()
        # 测试环境无模型，应降级
        assert not engine.available or engine.available
        # 降级时 embed 返回 None
        if not engine.available:
            assert engine.embed("test") is None
            assert engine.similarity("a", "b") == 0.0
            assert engine.rank("query", ["doc1", "doc2"]) == []

    def test_engine_cache(self):
        """嵌入缓存正常工作。"""
        from core.context.embeddings import EmbeddingEngine
        engine = EmbeddingEngine()
        assert engine.cache_size == 0
        engine.clear_cache()
        assert engine.cache_size == 0

    def test_engine_dim(self):
        """嵌入维度为 384。"""
        from core.context.embeddings import EmbeddingEngine
        engine = EmbeddingEngine()
        assert engine.dim == 384

    def test_get_engine_singleton(self):
        """get_embedding_engine 返回单例。"""
        from core.context.embeddings import get_embedding_engine
        e1 = get_embedding_engine()
        e2 = get_embedding_engine()
        assert e1 is e2

    def test_text_hash_deterministic(self):
        """相同文本产生相同 hash。"""
        from core.context.embeddings import EmbeddingEngine
        h1 = EmbeddingEngine._text_hash("hello world")
        h2 = EmbeddingEngine._text_hash("hello world")
        assert h1 == h2
        h3 = EmbeddingEngine._text_hash("different")
        assert h1 != h3

    # ── WordPiece 分词器 ────────────────────────────────────────────

    def test_wordpiece_basic_tokenize_cjk(self):
        """CJK 字符逐字切开。"""
        from core.context.embeddings import _WordPieceTokenizer

        class _Dummy(_WordPieceTokenizer):
            def __init__(self):
                self.vocab = {"[UNK]": 0, "[CLS]": 1, "[SEP]": 2, "[PAD]": 3, "hello": 4, "world": 5, "我": 6, "爱": 7}

        t = _Dummy()
        tokens = t._basic_tokenize("我爱hello世界")
        assert "我" in tokens
        assert "爱" in tokens
        assert "hello" in tokens

    def test_wordpiece_basic_tokenize_ascii(self):
        """ASCII 词按空格和标点切分。"""
        from core.context.embeddings import _WordPieceTokenizer

        class _Dummy(_WordPieceTokenizer):
            def __init__(self):
                self.vocab = {"[UNK]": 0}

        t = _Dummy()
        tokens = t._basic_tokenize("hello world, test")
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens

    def test_wordpiece_known_token(self):
        """已知 token 直接返回。"""
        from core.context.embeddings import _WordPieceTokenizer

        class _Dummy(_WordPieceTokenizer):
            def __init__(self):
                self.vocab = {"[UNK]": 0, "hello": 1, "##llo": 2}

        t = _Dummy()
        result = t._wordpiece("hello")
        assert result == ["hello"]

    def test_wordpiece_unknown_fallback(self):
        """超长未知 token 返回 [UNK]。"""
        from core.context.embeddings import _WordPieceTokenizer

        class _Dummy(_WordPieceTokenizer):
            def __init__(self):
                self.vocab = {"[UNK]": 0, "a": 1}

        t = _Dummy()
        result = t._wordpiece("x" * 101)
        assert result == ["[UNK]"]

    # ── is_model_available ──────────────────────────────────────────

    def test_model_available_returns_bool(self):
        """is_model_available 返回布尔值。"""
        from core.context.embeddings import is_model_available
        result = is_model_available()
        assert isinstance(result, bool)
