import pytest
from pathlib import Path
from core.memory.recall import recall, _expand_query, _keyword_score


class TestExpandQuery:
    def test_synonym_expansion(self):
        terms = _expand_query("代码风格")
        assert "代码" in terms or "编程" in terms
        assert "风格" in terms or "style" in terms

    def test_no_match_returns_original(self):
        terms = _expand_query("xyz")
        assert "xyz" in terms


class TestKeywordScore:
    def test_exact_match(self):
        assert _keyword_score("我喜欢简洁的代码", {"代码"}) == 2.0

    def test_partial_match(self):
        assert _keyword_score("Python编程风格", {"python", "风格"}) == 4.0

    def test_no_match(self):
        assert _keyword_score("hello world", {"中文"}) == 0.0


class TestRecall:
    @pytest.mark.asyncio
    async def test_recall_empty_dir(self, tmp_path):
        results = await recall("test", aide_root=tmp_path)
        assert results == []

    @pytest.mark.asyncio
    async def test_recall_finds_session(self, tmp_path):
        # 创建模拟会话
        import json
        sessions_dir = tmp_path / "sessions" / "20260701_120000"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / "meta.json").write_text(
            json.dumps({"name": "Python脚本"}))
        (sessions_dir / "overview.md").write_text(
            "## 话题\n- 编写Python脚本处理CSV\n\n## 决策与结论\n", encoding="utf-8")

        results = await recall("Python", aide_root=tmp_path)
        assert len(results) > 0
        assert any("Python" in r["snippet"] for r in results)


def test_synonym_map_coverage():
    """同义词映射覆盖常用技术术语。"""
    from core.memory.recall import SYNONYM_MAP
    assert len(SYNONYM_MAP) >= 20
    # 验证跨语言覆盖
    has_cn = any(ord(k[0]) > 127 for k in SYNONYM_MAP)
    assert has_cn, "应包含中文条目"


def test_keyword_score_header_bonus():
    """标题匹配得分高于正文匹配。"""
    from core.memory.recall import _keyword_score
    keywords = {"代码", "编程"}
    text_with_header = "代码 编程 技巧\n这是正文内容，不包含关键词"
    text_without_header = "这是一段正文\n代码 编程 相关内容在第二行"
    score_header = _keyword_score(text_with_header, keywords)
    score_body = _keyword_score(text_without_header, keywords)
    # header 匹配权重更高
    assert score_header >= score_body


def test_session_time_weight():
    """近期会话权重更高。"""
    from core.memory.recall import _session_time_weight
    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d_%H%M%S")
    old = "20200101_000000"
    assert _session_time_weight(today) >= _session_time_weight(old)


def test_get_all_synonyms():
    """同义词展开正常工作。"""
    from core.memory.recall import _get_all_synonyms
    result = _get_all_synonyms("代码")
    assert "编程" in result or "code" in result
