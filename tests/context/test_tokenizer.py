"""测试 tokenizer.py — TF-IDF / Jaccard / bigram / 时间衰减 / 语言检测 / 同义词。"""

import math
import pytest
import time
import tempfile
from pathlib import Path

from core.context.tokenizer import (
    _detect_language,
    _bigrams,
    _jaccard,
    _tfidf_score,
    _decay_factor,
    _tokenize,
    _chinese_tokenize,
    _expand_query,
    _get_all_synonyms,
    _build_vocabulary,
    flush_vocab_cache,
    get_vocab_index,
    VocabularyIndex,
    SYNONYM_MAP,
    _ZH_STOP_WORDS,
    _EN_STOP_WORDS,
)


# ── 语言检测 ──────────────────────────────────────────────────────────────

class TestDetectLanguage:
    """测试 _detect_language 函数。"""

    def test_chinese_text_detected(self):
        assert _detect_language("用中文回复所有问题") == "zh"

    def test_chinese_with_mixed_content(self):
        # CJK 占比高
        assert _detect_language("请帮我写一个Python脚本来处理数据") == "zh"

    def test_english_text_detected(self):
        assert _detect_language("reply in English please") == "en"

    def test_english_with_code(self):
        assert _detect_language("def hello(): return 'world'") == "en"

    def test_empty_text_defaults_zh(self):
        assert _detect_language("") == "zh"

    def test_symbols_only_defaults_zh(self):
        assert _detect_language("123 !@#$%") == "zh"

    def test_cjk_threshold_boundary(self):
        """CJK 字符 ≥ 30% 判定为 zh。"""
        # 10 alpha chars: 4 CJK + 6 ASCII = 40% → zh
        text = "你好世界ab cd ef"
        result = _detect_language(text)
        assert result == "zh"

    def test_below_threshold_is_en(self):
        """CJK < 30% 为 en。"""
        # "你我ab cdefghij" → cleaned: 你我abcdefghij (2 CJK + 10 alpha = 12, 16.7%)
        text = "你我ab cdefghij"
        result = _detect_language(text)
        assert result == "en"


# ── Bigram / Jaccard ──────────────────────────────────────────────────────

class TestBigrams:
    """测试 _bigrams 函数。"""

    def test_normal_text(self):
        assert _bigrams("hello") == {"he", "el", "ll", "lo"}

    def test_short_text(self):
        assert _bigrams("ab") == {"ab"}

    def test_single_char(self):
        assert _bigrams("a") == set()

    def test_empty(self):
        assert _bigrams("") == set()

    def test_chinese_text(self):
        result = _bigrams("你好世界")
        assert "你好" in result
        assert "好世" in result
        assert "世界" in result


class TestJaccard:
    """测试 _jaccard 函数。"""

    def test_identical_sets(self):
        assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint_sets(self):
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial_overlap(self):
        result = _jaccard({"a", "b", "c"}, {"b", "c", "d"})
        assert result == 2 / 4  # intersection=2, union=4

    def test_empty_a(self):
        assert _jaccard(set(), {"a"}) == 0.0

    def test_empty_b(self):
        assert _jaccard({"a"}, set()) == 0.0

    def test_both_empty(self):
        assert _jaccard(set(), set()) == 0.0


# ── TF-IDF ────────────────────────────────────────────────────────────────

class TestTfidfScore:
    """测试 _tfidf_score 函数。"""

    def test_perfect_match(self):
        score = _tfidf_score({"a", "b"}, {"a", "b"})
        assert score > 0.0

    def test_no_overlap(self):
        score = _tfidf_score({"a", "b"}, {"c", "d"})
        assert score == 0.0

    def test_empty_query(self):
        assert _tfidf_score(set(), {"a"}) == 0.0

    def test_empty_doc(self):
        assert _tfidf_score({"a"}, set()) == 0.0

    def test_with_df_weighting(self):
        """有 DF 表时应用 IDF 权重。"""
        df = {"python": 2, "test": 5}
        score = _tfidf_score({"python", "test"}, {"python", "test", "code"}, df=df, N=10)
        assert score > 0.0
        # "python" 的 IDF = log(10/2) + 1 = log(5) + 1 ≈ 2.61
        # "test" 的 IDF = log(10/5) + 1 = log(2) + 1 ≈ 1.69
        # union = 3, score = (2.61 + 1.69) / 3 ≈ 1.43
        # Actually let me check: tf*idf for each token: 1.0 * idf
        # score = (idf_python + idf_test) / union_size
        # union_size = |{"python", "test", "code"}| = 3
        # idf_python = math.log(10/2) + 1
        # idf_test = math.log(10/5) + 1
        expected = (math.log(5) + 1 + math.log(2) + 1) / 3
        assert score == pytest.approx(expected, rel=1e-6)

    def test_without_df(self):
        """无 DF 表时使用 overlap 计数。"""
        score = _tfidf_score({"a", "b"}, {"a", "b", "c"})
        # overlap=2, union=3
        assert score == 2 / 3

    def test_single_match(self):
        score = _tfidf_score({"unique"}, {"unique", "other", "stuff"})
        assert score > 0.0


# ── 时间衰减 ──────────────────────────────────────────────────────────────

class TestDecayFactor:
    """测试 _decay_factor 函数。"""

    def test_none_path_returns_one(self):
        assert _decay_factor(None) == 1.0

    def test_recent_file(self, tmp_path):
        f = tmp_path / "recent.txt"
        f.write_text("test")
        result = _decay_factor(f)
        # 刚创建的文件，衰减应接近 1.0
        assert result >= 0.99

    def test_old_file(self, tmp_path):
        f = tmp_path / "old.txt"
        f.write_text("test")
        # 模拟 60 天前的修改时间
        old_mtime = time.time() - 60 * 86400
        os_stat = f.stat()
        import os
        os.utime(f, (old_mtime, old_mtime))
        result = _decay_factor(f, half_life_days=30)
        # 60 天 = 2 个半衰期 → 0.5^2 = 0.25
        assert result == pytest.approx(0.25, rel=0.05)

    def test_non_existent_file(self, tmp_path):
        f = tmp_path / "nonexistent.txt"
        assert _decay_factor(f) == 1.0


# ── 分词 ──────────────────────────────────────────────────────────────────

class TestChineseTokenize:
    """测试 _chinese_tokenize 函数。"""

    def test_with_vocab(self):
        vocab = frozenset({"编程", "Python", "测试"})
        tokens = _chinese_tokenize("编程测试", vocab)
        # "编程" 在 vocab 中
        assert "编程" in tokens

    def test_empty_vocab(self):
        tokens = _chinese_tokenize("你好世界", frozenset())
        assert len(tokens) >= 1  # fallback 到逐字

    def test_mixed_chinese_ascii(self):
        vocab = frozenset({"文件"})
        tokens = _chinese_tokenize("读文件abc", vocab)
        assert "文件" in tokens
        assert "a" in tokens
        assert "b" in tokens
        assert "c" in tokens


class TestTokenize:
    """测试 _tokenize 函数。"""

    def test_chinese_text(self):
        # 提供种子词汇以确保中文分词有效
        vocab = frozenset({"中文", "回复", "Python"})
        word_tokens, char_bigrams = _tokenize("用中文回复", vocab=vocab)
        assert len(char_bigrams) >= 1
        assert "中文" in word_tokens or "回复" in word_tokens

    def test_english_text(self):
        word_tokens, char_bigrams = _tokenize("hello world")
        assert "hello" in word_tokens
        assert "world" in word_tokens

    def test_mixed_text(self):
        word_tokens, char_bigrams = _tokenize("Python编程 test")
        assert "python" in word_tokens or "test" in word_tokens

    def test_empty_text(self):
        word_tokens, char_bigrams = _tokenize("")
        assert word_tokens == set()
        assert char_bigrams == set()

    def test_stop_words_filtered(self):
        """停用词应被过滤。"""
        word_tokens, _ = _tokenize("这是一个测试")
        # "这个" 是停用词
        assert "这个" not in word_tokens
        assert "一个" not in word_tokens


# ── 同义词 ────────────────────────────────────────────────────────────────

class TestSynonyms:
    """测试同义词系统。"""

    def test_get_synonyms_exact_key(self):
        synonyms = _get_all_synonyms("代码")
        assert "编程" in synonyms or "code" in synonyms

    def test_get_synonyms_alias(self):
        synonyms = _get_all_synonyms("编程")
        assert "代码" in synonyms or "code" in synonyms

    def test_get_synonyms_case_insensitive(self):
        synonyms = _get_all_synonyms("CODE")
        assert len(synonyms) > 0

    def test_unknown_word_returns_empty(self):
        assert _get_all_synonyms("xyznotexist") == set()

    def test_expand_query(self):
        expanded = _expand_query("优化代码")
        assert len(expanded) > 0
        # "优化" 可能展开为 "optimize" 等同义词
        assert "代码" in expanded or "code" in expanded or "optimize" in expanded


# ── 词汇索引 ──────────────────────────────────────────────────────────────

class TestVocabularyIndex:
    """测试 VocabularyIndex 和 _build_vocabulary。"""

    def test_default_vocab_index(self):
        vi = VocabularyIndex()
        assert vi.built is False
        assert vi.N == 0

    def test_seed_vocab_fallback(self, tmp_path, monkeypatch):
        """空记忆目录时回退到种子词汇。"""
        agent_root = tmp_path / "empty_agent"
        agent_root.mkdir()
        vi = _build_vocabulary(agent_root)
        assert vi.built is True
        assert len(vi.vocab) > 0

    def test_build_from_memory_files(self, tmp_path):
        """从记忆文件构建词汇表。"""
        agent_root = tmp_path / "agent"
        agent_root.mkdir()
        (agent_root / "preferences.md").write_text(
            "- 用户喜欢用Python编程\n- 偏好简洁的代码风格\n",
            encoding="utf-8",
        )
        (agent_root / "workflows.md").write_text(
            "- 部署前运行测试\n- 使用Docker容器\n",
            encoding="utf-8",
        )
        vi = _build_vocabulary(agent_root)
        assert vi.built is True
        # 至少应有从内容中提取的词汇
        assert len(vi.vocab) > 0

    def test_flush_and_rebuild(self, tmp_path):
        """flush_vocab_cache 后重新构建。"""
        agent_root = tmp_path / "agent2"
        agent_root.mkdir()
        (agent_root / "preferences.md").write_text("- test\n", encoding="utf-8")

        vi1 = _build_vocabulary(agent_root)
        flush_vocab_cache()
        vi2 = _build_vocabulary(agent_root)
        # 重建后 built 应为 True
        assert vi2.built is True


# ── 停用词 ────────────────────────────────────────────────────────────────

class TestStopWords:
    """验证停用词集合不为空。"""

    def test_zh_stop_words_non_empty(self):
        assert len(_ZH_STOP_WORDS) > 10
        assert "的" in _ZH_STOP_WORDS
        assert "这个" in _ZH_STOP_WORDS

    def test_en_stop_words_non_empty(self):
        assert len(_EN_STOP_WORDS) > 10
        assert "the" in _EN_STOP_WORDS
        assert "a" in _EN_STOP_WORDS


# ── 同义词映射 ────────────────────────────────────────────────────────────

class TestSynonymMap:
    """验证同义词映射结构。"""

    def test_has_core_categories(self):
        assert "代码" in SYNONYM_MAP
        assert "简洁" in SYNONYM_MAP
        assert "部署" in SYNONYM_MAP

    def test_all_values_are_lists(self):
        for key, val in SYNONYM_MAP.items():
            assert isinstance(val, list), f"{key}: expected list, got {type(val)}"
            assert len(val) > 0, f"{key}: expected non-empty list"
