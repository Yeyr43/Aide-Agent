"""测试 context_manager — ingester, assembler, compactor。"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from core.storage import JsonStore
from core.context.ingester import ContextIngester, _turn_summary
from core.context.pipeline import ContextPipeline as ContextAssembler
from core.context.relevance import _bigrams, _jaccard


class TestBigramJaccard:
    """测试 bigram 和 Jaccard 工具函数。"""

    def test_bigrams_chinese(self):
        result = _bigrams("我喜欢简洁")
        assert "我喜" in result
        assert "喜欢" in result
        assert "欢简" in result
        assert "简洁" in result
        assert len(result) == 4

    def test_bigrams_english(self):
        result = _bigrams("hello")
        assert "he" in result
        assert "el" in result
        assert "ll" in result
        assert "lo" in result

    def test_bigrams_short(self):
        assert _bigrams("a") == set()
        assert _bigrams("") == set()

    def test_jaccard_identical(self):
        a = _bigrams("简洁回复")
        assert _jaccard(a, a) == 1.0

    def test_jaccard_similar(self):
        a = _bigrams("我喜欢简洁的回复风格")
        b = _bigrams("我喜欢简洁回复")
        score = _jaccard(a, b)
        assert 0.4 < score < 0.8  # 部分重叠

    def test_jaccard_different(self):
        a = _bigrams("简洁回复")
        b = _bigrams("文件读取工具")
        assert _jaccard(a, b) == 0.0

    def test_jaccard_empty(self):
        assert _jaccard(set(), {"a"}) == 0.0
        assert _jaccard({"a"}, set()) == 0.0
        assert _jaccard(set(), set()) == 0.0


class TestTurnSummary:
    """测试一句话摘要生成。"""

    def test_normal_message(self):
        summary = _turn_summary("帮我读一下文件", "好的，文件内容是...")
        assert "帮我读一下文件" in summary

    def test_with_tool_calls(self):
        summary = _turn_summary(
            "搜索今天天气",
            "我来搜索一下",
            [{"function": {"name": "web"}}]
        )
        assert "[工具调用]" in summary
        assert "web" in summary

    def test_long_message_truncated(self):
        long_msg = "这是一条非常长的消息" * 10
        summary = _turn_summary(long_msg, "回复")
        assert len(summary) < 200
        assert "…" in summary


class TestContextIngester:
    """测试 ContextIngester。"""

    @pytest.fixture
    async def store(self):
        s = JsonStore()
        await s.start()
        yield s
        await s.close()

    @pytest.fixture
    def session_dir(self, tmp_path):
        d = tmp_path / "sessions" / "test_session"
        d.mkdir(parents=True)
        (d / "messages").mkdir()
        return d

    @pytest.fixture
    def sessions_root(self, tmp_path):
        root = tmp_path / "sessions"
        root.mkdir()
        return root

    async def test_set_session_sets_dir(self, store, sessions_root):
        """set_session 绑定已存在的 session 目录。"""
        # Session 目录由 SessionManager 预先创建
        session_dir = sessions_root / "20260701_120000"
        session_dir.mkdir(parents=True)

        ingester = ContextIngester(store, sessions_root=sessions_root)
        result = ingester.set_session("20260701_120000")
        assert result == session_dir
        assert (result / "messages").exists()  # messages/ 子目录自动创建
        assert ingester.session_id == "20260701_120000"

    async def test_set_session_idempotent(self, store, sessions_root):
        """重复 set_session 同一 ID 不重复创建。"""
        session_dir = sessions_root / "sess1"
        session_dir.mkdir(parents=True)

        ingester = ContextIngester(store, sessions_root=sessions_root)
        d1 = ingester.set_session("sess1")
        d2 = ingester.set_session("sess1")
        assert d1 == d2
        assert ingester.session_id == "sess1"

    async def test_set_session(self, store, sessions_root):
        """set_session 绑定会话。"""
        session_dir = sessions_root / "test"
        session_dir.mkdir(parents=True)

        ingester = ContextIngester(store, sessions_root=sessions_root)
        d = ingester.set_session("test")
        assert d == session_dir

    async def test_ingest_writes_files(self, store, sessions_root):
        """ingest 应写入 timeline、messages。"""
        session_dir = sessions_root / "test"
        session_dir.mkdir(parents=True)

        ingester = ContextIngester(store, sessions_root=sessions_root)
        ingester.set_session("test")

        await ingester.ingest(
            turn=1,
            user_msg="你好",
            assistant_msg="你好！有什么可以帮你的？",
            turn_messages=[{"role": "user", "content": "你好"}],
        )

        # timeline.json
        from core.storage import read_jsonl
        timeline = read_jsonl(session_dir / "timeline.json")
        assert len(timeline) == 1
        assert timeline[0]["turn"] == 1
        assert "你好" in timeline[0]["summary"]

        # messages/turn_001.json —— 顶层不再冗余 user/assistant
        msg = json.loads(
            (session_dir / "messages" / "turn_001.json").read_text(encoding="utf-8"))
        assert msg["turn"] == 1
        assert "user" not in msg
        assert "assistant" not in msg
        assert any(
            m.get("role") == "user" and "你好" in m.get("content", "")
            for m in msg["messages"]
        )

    async def test_ingest_without_session_raises(self, store, sessions_root):
        """未 set_session 就 ingest 应抛错。"""
        ingester = ContextIngester(store, sessions_root=sessions_root)
        with pytest.raises(RuntimeError, match="session 未创建"):
            await ingester.ingest(1, "hi", "hello")


class TestContextAssembler:
    """测试 ContextAssembler。"""

    @pytest.mark.asyncio
    async def test_read_cached(self, tmp_path):
        """应缓存 Soul 文件内容。"""
        soul = tmp_path / "soul.md"
        soul.write_text("test soul", encoding="utf-8")

        assembler = ContextAssembler(agent_root=tmp_path)
        content = await assembler._read_cached(soul)
        assert content == "test soul"
        # 修改文件
        soul.write_text("modified", encoding="utf-8")
        # 缓存不应更新
        content2 = await assembler._read_cached(soul)
        assert content2 == "test soul"

    @pytest.mark.asyncio
    async def test_flush_cache(self, tmp_path):
        """flush_cache 后应重新读取。"""
        soul = tmp_path / "soul.md"
        soul.write_text("v1", encoding="utf-8")

        assembler = ContextAssembler(agent_root=tmp_path)
        assert await assembler._read_cached(soul) == "v1"
        soul.write_text("v2", encoding="utf-8")
        assembler.flush_cache()
        assert await assembler._read_cached(soul) == "v2"

    async def test_assemble_includes_soul(self, tmp_path):
        """Soul 内容应注入到 system message。"""
        soul = tmp_path / "soul.md"
        soul.write_text("我是 Aide，你的智能管家", encoding="utf-8")

        assembler = ContextAssembler(agent_root=tmp_path)
        for f in ["preferences.md", "workflows.md", "long_term_memory.md"]:
            (tmp_path / f).write_text("", encoding="utf-8")

        messages, _ = await assembler.assemble(None, "你好")
        assert len(messages) >= 1
        assert messages[0]["role"] == "system"
        assert "Aide" in messages[0]["content"]

    async def test_assemble_empty_session(self, tmp_path):
        """无 session 时仍应返回 system messages。"""
        assembler = ContextAssembler(agent_root=tmp_path)
        for f in ["soul.md", "preferences.md", "workflows.md", "long_term_memory.md"]:
            (tmp_path / f).write_text("", encoding="utf-8")

        messages, _ = await assembler.assemble(None, "你好")
        # TOOLS_PROMPT 始终注入，至少 1 条 system message
        assert len(messages) == 1

    async def test_relevance_filter(self, tmp_path):
        """高相关 prompt 展开，低相关折叠。"""
        soul = tmp_path / "soul.md"
        soul.write_text("test soul", encoding="utf-8")
        prefs = tmp_path / "preferences.md"
        prefs.write_text("用户偏好简洁回复\n\n用户喜欢 Python 编程", encoding="utf-8")
        wf = tmp_path / "workflows.md"
        wf.write_text("用户是后端工程师", encoding="utf-8")
        lt = tmp_path / "long_term_memory.md"
        lt.write_text("", encoding="utf-8")

        assembler = ContextAssembler(agent_root=tmp_path)
        # 与"简洁回复"相关
        messages, _ = await assembler.assemble(None, "简洁回复很重要")
        system = messages[0]["content"]
        assert "偏好" in system.lower()
