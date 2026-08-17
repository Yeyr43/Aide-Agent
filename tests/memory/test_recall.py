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

    @pytest.mark.asyncio
    async def test_recall_matches_overview_content(self, tmp_path):
        """会话总览正文可被搜索命中（回归：parse_overview_md 未导入被吞）。"""
        import json
        sessions_dir = tmp_path / "sessions" / "20260702_120000"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / "meta.json").write_text(
            json.dumps({"name": "无关会话"}))
        (sessions_dir / "overview.md").write_text(
            "## 决策与结论\n- 我们决定使用 Postgres 作为主数据库\n",
            encoding="utf-8")

        # 关键词只出现在 overview 正文，meta 名无关
        results = await recall("Postgres", aide_root=tmp_path)
        assert any("Postgres" in r["snippet"] for r in results)


def test_synonym_map_coverage():
    """同义词映射覆盖常用技术术语。"""
    from core.context.relevance import SYNONYM_MAP
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
    from core.context.relevance import _get_all_synonyms
    result = _get_all_synonyms("代码")
    assert "编程" in result or "code" in result


class _FakeResult:
    """搜索索引返回的伪结果。"""

    def __init__(self, session_id, turn, summary, score):
        self.session_id = session_id
        self.turn = turn
        self.summary = summary
        self.score = score


class _FakeSearchIndex:
    """固定返回结果的伪 SearchIndex。"""

    def __init__(self, results):
        self._results = results

    async def search(self, query, top_k=20):
        return self._results


@pytest.mark.asyncio
async def test_recall_default_aide_root():
    """未传 aide_root 时回退到 aide_dir()（不崩溃、返回列表）。"""
    results = await recall("xyznonexistentkeyword")
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_recall_with_search_index(tmp_path):
    """搜索索引命中 → 注入索引结果 + 补充 meta/overview 细节（_enrich_session）。"""
    import json
    sessions_root = tmp_path / "sessions"
    sid = "20260801_120000"
    session_dir = sessions_root / sid
    session_dir.mkdir(parents=True)
    (session_dir / "meta.json").write_text(
        json.dumps({"name": "Python 项目"}), encoding="utf-8")
    (session_dir / "overview.json").write_text(
        json.dumps({"to_turn": 10, "overview_md": "## 决策与结论\n- 决定使用 Python 脚本\n"}),
        encoding="utf-8")

    # 命中一个存在的会话 + 一个不存在的会话目录（is_dir False）
    idx = _FakeSearchIndex([
        _FakeResult(sid, 5, "讨论 Python 脚本", 0.8),
        _FakeResult("20260999_000000", 1, "不存在的会话", 0.5),
    ])
    results = await recall("Python", aide_root=tmp_path, search_index=idx)
    assert any(r["source"] == f"[会话 {sid} / 轮 5]" for r in results)
    # _enrich_session 补充 meta 与 overview 细节
    assert any("Python 项目" in r["snippet"] for r in results)
    assert any("决定使用 Python 脚本" in r["snippet"] for r in results)


@pytest.mark.asyncio
async def test_recall_enrich_corrupt_meta_skipped(tmp_path):
    """_enrich_session 遇到损坏 meta.json → 跳过（except 分支）。"""
    import json
    sessions_root = tmp_path / "sessions"
    sid = "20260802_120000"
    session_dir = sessions_root / sid
    session_dir.mkdir(parents=True)
    (session_dir / "meta.json").write_text("{bad json", encoding="utf-8")

    idx = _FakeSearchIndex([_FakeResult(sid, 1, "Python 话题", 0.5)])
    results = await recall("Python", aide_root=tmp_path, search_index=idx)
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_recall_enrich_overview_parse_error(tmp_path, monkeypatch):
    """_enrich_session 解析 overview 抛异常 → 静默跳过。"""
    import json
    sessions_root = tmp_path / "sessions"
    sid = "20260801_120000"
    session_dir = sessions_root / sid
    session_dir.mkdir(parents=True)
    (session_dir / "meta.json").write_text(
        json.dumps({"name": "Python 项目"}), encoding="utf-8")
    (session_dir / "overview.md").write_text(
        "## 话题\n- Python 脚本\n", encoding="utf-8")

    def _boom(text):
        raise ValueError("parse fail")

    monkeypatch.setattr("core.memory.recall.parse_overview_md", _boom)
    idx = _FakeSearchIndex([_FakeResult(sid, 1, "Python", 0.5)])
    results = await recall("Python", aide_root=tmp_path, search_index=idx)
    # meta 仍命中，overview 异常被吞
    assert any("Python 项目" in r["snippet"] for r in results)


@pytest.mark.asyncio
async def test_recall_fallback_skips_files_and_limits(tmp_path):
    """Fallback 扫描：跳过非目录文件 + 达到 max_sessions 后 break。"""
    import json
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir(parents=True)
    (sessions_root / "zz_notes.txt").write_text("not a dir", encoding="utf-8")
    session_dir = sessions_root / "20260801_120000"
    session_dir.mkdir(parents=True)
    (session_dir / "meta.json").write_text(
        json.dumps({"name": "Python 项目"}), encoding="utf-8")

    results = await recall("Python", aide_root=tmp_path, max_sessions=1)
    assert any("Python 项目" in r["snippet"] for r in results)


@pytest.mark.asyncio
async def test_recall_corrupt_meta_skipped(tmp_path):
    """Fallback 中 meta.json 损坏 → except 跳过，不崩溃。"""
    sessions_root = tmp_path / "sessions"
    session_dir = sessions_root / "20260801_120000"
    session_dir.mkdir(parents=True)
    (session_dir / "meta.json").write_text("{bad json", encoding="utf-8")
    results = await recall("Python", aide_root=tmp_path)
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_recall_timeline_matched(tmp_path):
    """timeline.json 命中（含非 dict 条目跳过）。"""
    sessions_root = tmp_path / "sessions"
    session_dir = sessions_root / "20260801_120000"
    session_dir.mkdir(parents=True)
    (session_dir / "timeline.json").write_text(
        '{"turn": 1, "summary": "讨论 Python 脚本设计"}\n42\n',
        encoding="utf-8")
    results = await recall("Python", aide_root=tmp_path)
    assert any("讨论 Python 脚本设计" in r["snippet"] for r in results)


@pytest.mark.asyncio
async def test_recall_timeline_read_error(tmp_path):
    """timeline.json 为目录（不可读）→ except OSError 跳过。"""
    sessions_root = tmp_path / "sessions"
    session_dir = sessions_root / "20260801_120000"
    session_dir.mkdir(parents=True)
    (session_dir / "timeline.json").mkdir()
    results = await recall("Python", aide_root=tmp_path)
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_recall_session_overview_parse_error(tmp_path, monkeypatch):
    """Fallback 中 overview 解析抛异常 → 静默跳过。"""
    sessions_root = tmp_path / "sessions"
    session_dir = sessions_root / "20260801_120000"
    session_dir.mkdir(parents=True)
    (session_dir / "overview.md").write_text(
        "## 话题\n- Python 脚本\n", encoding="utf-8")

    def _boom(text):
        raise ValueError("parse fail")

    monkeypatch.setattr("core.memory.recall.parse_overview_md", _boom)
    results = await recall("Python", aide_root=tmp_path)
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_recall_memory_files_matched(tmp_path):
    """搜索 agent/*.md 记忆文件（注释/空行跳过，目录读取失败跳过）。"""
    agent_root = tmp_path / "agent"
    agent_root.mkdir(parents=True)
    (agent_root / "preferences.md").write_text(
        "# 偏好\n\n- 喜欢 Python 代码\n- 无匹配内容\n", encoding="utf-8")
    (agent_root / "workflows.md").write_text("# 工作流\n\n", encoding="utf-8")
    # 目录 → read_text 抛 OSError（except 分支）
    (agent_root / "long_term_memory.md").mkdir()
    results = await recall("Python", aide_root=tmp_path)
    assert any("喜欢 Python 代码" in r["snippet"] for r in results)


def test_keyword_score_empty_text():
    """无可用分词/大元的文本 → 0.0。"""
    assert _keyword_score("", {"x"}) == 0.0


def test_session_time_weight_invalid_name():
    """非法/过短的会话目录名 → 默认 0.5。"""
    from core.memory.recall import _session_time_weight
    assert _session_time_weight("bad_name") == 0.5
    assert _session_time_weight("20200101") == 0.5


def test_tfidf_rank_empty_query_sorts_by_score():
    """查询无可分词 → 直接按原始分数降序。"""
    from core.memory.recall import _tfidf_rank
    ranked = _tfidf_rank("!!!", [
        {"snippet": "a", "score": 1.0, "_keyword_score": 1.0},
        {"snippet": "b", "score": 2.0, "_keyword_score": 2.0},
    ])
    assert [m["snippet"] for m in ranked] == ["b", "a"]
