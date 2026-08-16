"""Tests for ContextPipeline.assemble() — six-layer context assembly."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from core.context.pipeline import ContextPipeline


@pytest.fixture(autouse=True)
def _reset_vocab():
    """每个测试前重置全局词汇索引，避免会话词汇污染测试间状态。"""
    from core.context.tokenizer import flush_vocab_cache
    flush_vocab_cache()
    yield
    flush_vocab_cache()


def _make_agent_dir(tmp_path: Path) -> Path:
    """Create a minimal agent directory with soul.md."""
    agent_root = tmp_path / "agent"
    agent_root.mkdir(parents=True)
    (agent_root / "soul.md").write_text("# Test Soul\nBe helpful.", encoding="utf-8")
    # Create empty data dir
    (agent_root / "data").mkdir(exist_ok=True)
    for fname in ["preferences.json", "workflows.json", "long_term_memory.json"]:
        (agent_root / "data" / fname).write_text("[]", encoding="utf-8")
    return agent_root


class TestAssembleMinimal:
    """Minimum-viable assemble: empty conversation, no session."""

    @pytest.mark.asyncio
    async def test_empty_conversation_no_session(self, tmp_path):
        agent_root = _make_agent_dir(tmp_path)
        pipeline = ContextPipeline(agent_root=agent_root)

        system_msgs, trimmed = await pipeline.assemble(
            session_dir=None,
            user_msg="hello",
            conversation=[],
        )

        assert len(system_msgs) == 1
        assert system_msgs[0]["role"] == "system"
        assert len(trimmed) == 0
        # System message should include the soul content
        assert "Test Soul" in system_msgs[0]["content"]
        # And tools prompt
        assert "工具" in system_msgs[0]["content"] or "Tool" in system_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_with_conversation_within_window(self, tmp_path):
        agent_root = _make_agent_dir(tmp_path)
        pipeline = ContextPipeline(agent_root=agent_root, full_text_turns=3)

        conv = [{"role": "user", "content": "prev question"}]
        system_msgs, trimmed = await pipeline.assemble(
            session_dir=None,
            user_msg="hello",
            conversation=conv,
        )

        assert len(trimmed) == 1
        assert trimmed[0]["role"] == "user"
        assert trimmed[0]["content"] == "prev question"


class TestAssembleSoulAndTools:
    """Soul + tools prompt layers."""

    @pytest.mark.asyncio
    async def test_soul_injected(self, tmp_path):
        agent_root = _make_agent_dir(tmp_path)
        pipeline = ContextPipeline(agent_root=agent_root)

        system_msgs, _ = await pipeline.assemble(
            session_dir=None, user_msg="hello",
        )

        combined = system_msgs[0]["content"]
        assert "Test Soul" in combined
        assert "Be helpful" in combined

    @pytest.mark.asyncio
    async def test_no_soul_file_graceful(self, tmp_path):
        agent_root = tmp_path / "agent"
        agent_root.mkdir(parents=True)
        (agent_root / "data").mkdir(exist_ok=True)
        for fname in ["preferences.json", "workflows.json", "long_term_memory.json"]:
            (agent_root / "data" / fname).write_text("[]", encoding="utf-8")

        pipeline = ContextPipeline(agent_root=agent_root)
        system_msgs, _ = await pipeline.assemble(
            session_dir=None, user_msg="hello",
        )
        # Should not crash — system message still generated (tools prompt at minimum)
        assert len(system_msgs) == 1

    @pytest.mark.asyncio
    async def test_tool_descriptions_injected(self, tmp_path):
        agent_root = _make_agent_dir(tmp_path)
        pipeline = ContextPipeline(agent_root=agent_root)

        tool_descs = ["**my_tool** — Does something custom."]
        system_msgs, _ = await pipeline.assemble(
            session_dir=None, user_msg="hello",
            tool_descriptions=tool_descs,
        )

        combined = system_msgs[0]["content"]
        assert "my_tool" in combined

    @pytest.mark.asyncio
    async def test_no_tool_descriptions_falls_back_to_locale(self, tmp_path):
        agent_root = _make_agent_dir(tmp_path)
        pipeline = ContextPipeline(agent_root=agent_root)

        system_msgs, _ = await pipeline.assemble(
            session_dir=None, user_msg="hello",
            tool_descriptions=None,
        )

        combined = system_msgs[0]["content"]
        # Should still have the tools heading
        assert "read_file" in combined or "工具" in combined


class TestAssembleSessionLayers:
    """Session overview + timeline layers."""

    @pytest.mark.asyncio
    async def test_overview_md_injected(self, tmp_path):
        agent_root = _make_agent_dir(tmp_path)
        session_dir = tmp_path / "sessions" / "test"
        session_dir.mkdir(parents=True)
        (session_dir / "overview.md").write_text("# 会话总览\n讨论过部署问题。", encoding="utf-8")

        pipeline = ContextPipeline(agent_root=agent_root)
        system_msgs, _ = await pipeline.assemble(
            session_dir=session_dir, user_msg="hello",
        )

        combined = system_msgs[0]["content"]
        assert "部署问题" in combined or "session overview" in combined.lower()

    @pytest.mark.asyncio
    async def test_no_overview_graceful(self, tmp_path):
        agent_root = _make_agent_dir(tmp_path)
        session_dir = tmp_path / "sessions" / "test"
        session_dir.mkdir(parents=True)

        pipeline = ContextPipeline(agent_root=agent_root)
        system_msgs, _ = await pipeline.assemble(
            session_dir=session_dir, user_msg="hello",
        )
        # Should not crash
        assert len(system_msgs) == 1

    @pytest.mark.asyncio
    async def test_timeline_summaries_injected(self, tmp_path):
        agent_root = _make_agent_dir(tmp_path)
        session_dir = tmp_path / "sessions" / "test"
        session_dir.mkdir(parents=True)
        # Add a timeline with past turns
        timeline = [
            {"turn": 1, "timestamp": "2026-01-01T00:00:00", "summary": "讨论了数据库迁移"},
            {"turn": 2, "timestamp": "2026-01-01T00:01:00", "summary": "用户要求用PostgreSQL"},
        ]
        (session_dir / "timeline.json").write_text(
            json.dumps(timeline, ensure_ascii=False), encoding="utf-8",
        )

        pipeline = ContextPipeline(agent_root=agent_root, full_text_turns=1)
        conv = [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "new question"},
        ]

        system_msgs, _ = await pipeline.assemble(
            session_dir=session_dir, user_msg="new question",
            conversation=conv,
        )

        combined = system_msgs[0]["content"]
        # Should contain recent timeline or overview
        assert "数据库" in combined or "PostgreSQL" in combined or "history" in combined.lower()


class TestAssembleContextProviders:
    """Plugin/skill context provider injection."""

    @pytest.mark.asyncio
    async def test_context_provider_injected(self, tmp_path):
        agent_root = _make_agent_dir(tmp_path)
        pipeline = ContextPipeline(agent_root=agent_root)

        mock_provider = MagicMock()
        mock_provider.provide = AsyncMock(return_value="## Skill: Test\nSkill content here.")

        system_msgs, _ = await pipeline.assemble(
            session_dir=None, user_msg="use the test skill",
            context_providers=[mock_provider],
        )

        combined = system_msgs[0]["content"]
        assert "Skill: Test" in combined
        assert "Skill content here" in combined

    @pytest.mark.asyncio
    async def test_context_provider_receives_real_user_msg(self, tmp_path):
        """回归：provide() 必须收到真实 user_msg（曾传空串导致技能上下文永不注入）。"""
        agent_root = _make_agent_dir(tmp_path)
        pipeline = ContextPipeline(agent_root=agent_root)

        mock_provider = MagicMock()
        mock_provider.provide = AsyncMock(return_value="## Skill: Test\ncontent")

        await pipeline.assemble(
            session_dir=None, user_msg="请使用 code-review 技能",
            context_providers=[mock_provider],
        )
        call_args = mock_provider.provide.await_args
        assert call_args is not None
        assert call_args.args[0] == "请使用 code-review 技能"

    @pytest.mark.asyncio
    async def test_context_provider_no_relevance_returns_empty(self, tmp_path):
        agent_root = _make_agent_dir(tmp_path)
        pipeline = ContextPipeline(agent_root=agent_root)

        mock_provider = MagicMock()
        mock_provider.provide = AsyncMock(return_value="")  # no match

        system_msgs, _ = await pipeline.assemble(
            session_dir=None, user_msg="hello",
            context_providers=[mock_provider],
        )
        # Should not crash — just no extra content
        assert len(system_msgs) == 1

    @pytest.mark.asyncio
    async def test_context_provider_exception_graceful(self, tmp_path):
        agent_root = _make_agent_dir(tmp_path)
        pipeline = ContextPipeline(agent_root=agent_root)

        mock_provider = MagicMock()
        mock_provider.provide = AsyncMock(side_effect=RuntimeError("boom"))

        system_msgs, _ = await pipeline.assemble(
            session_dir=None, user_msg="hello",
            context_providers=[mock_provider],
        )
        # Should not crash
        assert len(system_msgs) == 1


class TestAssembleDynamicPrompt:
    """Dynamic prompt injection with relevance filtering."""

    @pytest.mark.asyncio
    async def test_relevant_preferences_injected(self, tmp_path):
        agent_root = _make_agent_dir(tmp_path)
        # Add a preference file
        (agent_root / "preferences.md").write_text(
            "## 偏好\n- 用户偏好简洁回复\n- 代码用 TypeScript\n", encoding="utf-8",
        )

        pipeline = ContextPipeline(agent_root=agent_root, relevance_threshold=0.0)
        system_msgs, _ = await pipeline.assemble(
            session_dir=None, user_msg="我喜欢简洁",
        )

        combined = system_msgs[0]["content"]
        # With threshold 0.0, all sections should be included
        assert "简洁回复" in combined or "TypeScript" in combined

    @pytest.mark.asyncio
    async def test_irrelevant_preferences_filtered(self, tmp_path):
        agent_root = _make_agent_dir(tmp_path)
        (agent_root / "preferences.md").write_text(
            "## 偏好\n- 用户偏好简洁回复\n", encoding="utf-8",
        )

        # High threshold → nothing should match
        pipeline = ContextPipeline(agent_root=agent_root, relevance_threshold=0.99)
        system_msgs, _ = await pipeline.assemble(
            session_dir=None, user_msg="zxyabc123 totally unrelated",
        )

        combined = system_msgs[0]["content"]
        # Preferences should be filtered out
        assert "简洁回复" not in combined

    @pytest.mark.asyncio
    async def test_empty_prompt_file_graceful(self, tmp_path):
        agent_root = _make_agent_dir(tmp_path)
        (agent_root / "preferences.md").write_text("", encoding="utf-8")

        pipeline = ContextPipeline(agent_root=agent_root)
        system_msgs, _ = await pipeline.assemble(
            session_dir=None, user_msg="hello",
        )
        # Should not crash on empty file
        assert len(system_msgs) == 1


class TestEntryDecay:
    """记忆条目时间衰减语义（方向 1：created 而非文件 mtime，长记忆不衰减）。"""

    def _old_file(self, path: Path, days: int) -> None:
        import os
        import time
        path.write_text("x", encoding="utf-8")
        old = time.time() - days * 86400
        os.utime(path, (old, old))

    def test_ltm_never_decays(self, tmp_path):
        """long_term_memory 的条目不随时间衰减。"""
        from core.context.pipeline import _entry_decay
        from core.memory.entries import MemoryEntry
        ltm = tmp_path / "long_term_memory.md"
        self._old_file(ltm, days=120)
        entry = MemoryEntry(created="2026-01-01", file="long_term_memory.md")
        assert _entry_decay(entry, ltm) == 1.0

    def test_preference_uses_created(self, tmp_path):
        """preferences 条目按 created 衰减（而非整个文件的 mtime）。"""
        from core.context.pipeline import _entry_decay
        from core.memory.entries import MemoryEntry
        pref = tmp_path / "preferences.md"
        self._old_file(pref, days=120)  # 文件 120 天前
        entry = MemoryEntry(created="2026-06-01", file="preferences.md")  # 条目 ~2 月前
        decay = _entry_decay(entry, pref)
        assert 0 < decay < 1.0
        # 按条目 created 计算（约 75 天 → ~0.18），而非按文件 mtime（120 天 → ~0.06）
        assert decay > 0.1

    def test_no_created_falls_back_to_file_mtime(self, tmp_path):
        """无 created 字段的旧条目回退文件 mtime 衰减。"""
        from core.context.pipeline import _entry_decay
        from core.memory.entries import MemoryEntry
        pref = tmp_path / "preferences.md"
        self._old_file(pref, days=30)
        entry = MemoryEntry(file="preferences.md")  # 无 created
        assert 0 < _entry_decay(entry, pref) < 1.0


class TestScoringSystem:
    """修复后的评分体系：内容相关性为主序（衰减只是排序次要因子）。"""

    @pytest.mark.asyncio
    async def test_relevant_old_memory_injected_irrelevant_new_filtered(self, tmp_path):
        """相关旧记忆应注入，不相关新记忆被过滤（修复前相反：主序是衰减）。"""
        import os
        import time
        agent_root = _make_agent_dir(tmp_path)
        # 相关旧记忆：preferences.md 带 created（2 个月前），内容与"部署"相关
        (agent_root / "preferences.md").write_text(
            "---\nid: old_rel\ncreated: 2026-06-01\n---\n- 用户偏好用 Docker 部署\n",
            encoding="utf-8",
        )
        old = time.time() - 60 * 86400
        os.utime(agent_root / "preferences.md", (old, old))
        # 不相关新记忆：workflows.md 刚写，内容无关
        (agent_root / "workflows.md").write_text("- 用户喜欢蓝色\n", encoding="utf-8")

        pipeline = ContextPipeline(agent_root=agent_root)
        system_msgs, _ = await pipeline.assemble(
            session_dir=None, user_msg="怎么部署这个应用",
        )
        combined = system_msgs[0]["content"]
        # 相关旧记忆注入（相关性决定生死，衰减只影响顺序）
        assert "Docker" in combined
        # 不相关新记忆被过滤
        assert "蓝色" not in combined

    @pytest.mark.asyncio
    async def test_ltm_old_memory_injected(self, tmp_path):
        """长期记忆 created 很久前仍应注入（不衰减）。"""
        import os
        import time
        agent_root = _make_agent_dir(tmp_path)
        (agent_root / "long_term_memory.md").write_text(
            "---\nid: ltm_1\ncreated: 2026-01-01\n---\n- 用户偏好用 Docker 部署\n",
            encoding="utf-8",
        )
        old = time.time() - 120 * 86400
        os.utime(agent_root / "long_term_memory.md", (old, old))

        pipeline = ContextPipeline(agent_root=agent_root)
        system_msgs, _ = await pipeline.assemble(
            session_dir=None, user_msg="怎么部署这个应用",
        )
        assert "Docker" in system_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_last_memory_fragments_collected_after_scoring(self, tmp_path):
        """收集时机修复：用评分后的相关性判断，而非收集时的衰减基础分。"""
        agent_root = _make_agent_dir(tmp_path)
        (agent_root / "preferences.md").write_text(
            "---\nid: rel\ncreated: 2026-06-01\n---\n- 用户偏好用 Docker 部署\n",
            encoding="utf-8",
        )
        (agent_root / "workflows.md").write_text("- 用户喜欢蓝色\n", encoding="utf-8")

        pipeline = ContextPipeline(agent_root=agent_root)
        await pipeline.assemble(
            session_dir=None, user_msg="怎么部署这个应用",
        )
        frags = pipeline.get_last_memory_fragments()
        contents = [f.content for f in frags]
        assert "用户偏好用 Docker 部署" in contents
        assert "用户喜欢蓝色" not in contents

    def test_pack_short_first_on_tie(self, tmp_path):
        """打包：同分数时短片段优先（token 效率），且低于相关性阈值的被过滤。"""
        from core.context.pipeline import ContextFragment
        pipeline = ContextPipeline(agent_root=_make_agent_dir(tmp_path))
        frags = [
            ContextFragment(type="memory", content="y" * 10, tokens=10,
                            relevance=0.5, score=0.5),
            ContextFragment(type="memory", content="x" * 100, tokens=100,
                            relevance=0.5, score=0.5),
            ContextFragment(type="memory", content="z" * 5, tokens=5,
                            relevance=0.01, score=0.01),
        ]
        combined = pipeline._pack_fragments(frags, token_budget=1000)
        # 同分短优先：y 块(10) 在 x 块(100) 前
        assert combined.index("y" * 10) < combined.index("x" * 100)
        # 低相关片段被过滤
        assert "z" * 5 not in combined

    def test_pack_memory_budget_cap(self, tmp_path):
        """打包：memory 片段受单来源预算上限（MEMORY_BUDGET_RATIO=0.4）。

        token_budget=1000 → memory_budget=400；4 个 150-token 的 memory
        只装前 2 个（300 ≤ 400），后 2 个被跳过。
        """
        from core.context.pipeline import ContextFragment
        pipeline = ContextPipeline(agent_root=_make_agent_dir(tmp_path))
        frags = [
            ContextFragment(type="memory", content=str(i) * 150, tokens=150,
                            relevance=0.9, score=0.9)
            for i in range(4)
        ]
        combined = pipeline._pack_fragments(frags, token_budget=1000)
        assert "0" in combined and "1" in combined
        assert "2" not in combined and "3" not in combined

    def test_pack_memory_budget_does_not_affect_non_memory(self, tmp_path):
        """非 memory 片段不受 memory 预算限制（只要总预算允许）。"""
        from core.context.pipeline import ContextFragment
        pipeline = ContextPipeline(agent_root=_make_agent_dir(tmp_path))
        # 450 token 的 timeline 片段 > memory_budget(400)，但 < 总预算(1000) → 仍注入
        frags = [
            ContextFragment(type="timeline", content="T" * 450, tokens=450,
                            relevance=0.9, score=0.9),
        ]
        combined = pipeline._pack_fragments(frags, token_budget=1000)
        assert "T" in combined

    def test_pack_memory_date_prefix(self, tmp_path):
        """打包：带 created 的 memory 片段注入时带记忆日期前缀，无 created 的不带。"""
        from core.context.pipeline import ContextFragment
        pipeline = ContextPipeline(agent_root=_make_agent_dir(tmp_path))
        frags = [
            ContextFragment(type="memory", content="旧记忆", tokens=10,
                            relevance=0.9, score=0.9,
                            metadata={"created": "2026-06-01", "entry_id": "pref_001"}),
            ContextFragment(type="memory", content="无日期记忆", tokens=10,
                            relevance=0.9, score=0.9),
        ]
        combined = pipeline._pack_fragments(frags, token_budget=1000)
        # 带 created → 前缀；无 created → 原样
        assert "（记忆日期：2026-06-01）\n旧记忆" in combined
        assert "\n无日期记忆" in combined or "无日期记忆" in combined

    @pytest.mark.asyncio
    async def test_assemble_injects_memory_date_prefix(self, tmp_path):
        """集成：带 created 的记忆条目经 assemble 注入时带日期前缀。"""
        agent_root = _make_agent_dir(tmp_path)
        (agent_root / "preferences.md").write_text(
            "---\nid: rel\ncreated: 2026-06-01\n---\n- 用户偏好用 Docker 部署\n",
            encoding="utf-8",
        )
        pipeline = ContextPipeline(agent_root=agent_root)
        system_msgs, _ = await pipeline.assemble(
            session_dir=None, user_msg="怎么部署这个应用",
        )
        assert "记忆日期：2026-06-01" in system_msgs[0]["content"]
        assert "用户偏好用 Docker 部署" in system_msgs[0]["content"]


class TestHistoryBackfill:
    """方向 2：与当前问题相关的早期轮次完整内容回填到上下文。"""

    @pytest.mark.asyncio
    async def test_related_older_turn_backfilled(self, tmp_path):
        """相关早期轮次的完整 assistant 内容应注入（而非只留一句话摘要）。"""
        agent_root = _make_agent_dir(tmp_path)
        session_dir = tmp_path / "sessions" / "test"
        session_dir.mkdir(parents=True)
        pipeline = ContextPipeline(agent_root=agent_root, full_text_turns=1)
        conv = [
            {"role": "user", "content": "我们讨论过 Docker 部署方案"},
            {"role": "assistant", "content": "用 docker-compose 部署服务"},
            {"role": "user", "content": "最后用什么方案"},
            {"role": "assistant", "content": "选定了 docker-compose"},
            {"role": "user", "content": "现在有新问题"},
        ]
        system_msgs, _ = await pipeline.assemble(
            session_dir=session_dir, user_msg="Docker 怎么部署",
            conversation=conv,
        )
        combined = system_msgs[0]["content"]
        # 完整 assistant 句回填（_build_overview 只提取话题/决策，不会保留完整句）
        assert "用 docker-compose 部署服务" in combined

    @pytest.mark.asyncio
    async def test_unrelated_older_turn_not_backfilled(self, tmp_path):
        """与当前问题无关的早期轮次不注入完整内容。"""
        agent_root = _make_agent_dir(tmp_path)
        session_dir = tmp_path / "sessions" / "test"
        session_dir.mkdir(parents=True)
        pipeline = ContextPipeline(agent_root=agent_root, full_text_turns=1)
        conv = [
            {"role": "user", "content": "我们讨论了蓝色主题"},
            {"role": "assistant", "content": "建议用蓝色"},
            {"role": "user", "content": "现在有新问题"},
        ]
        system_msgs, _ = await pipeline.assemble(
            session_dir=session_dir, user_msg="Docker 怎么部署",
            conversation=conv,
        )
        combined = system_msgs[0]["content"]
        assert "建议用蓝色" not in combined


class TestVocabularyFromSessions:
    """方向 4：词汇表从会话 timeline 摘要构建（缓解冷启动分词退化）。"""

    def test_vocab_includes_session_terms(self, tmp_path):
        from core.context.tokenizer import _build_vocabulary, flush_vocab_cache
        agent_root = _make_agent_dir(tmp_path)  # 无记忆文件 → 冷启动
        sessions_root = tmp_path / "sessions"
        sd = sessions_root / "20260701_120000"
        sd.mkdir(parents=True)
        (sd / "timeline.json").write_text(
            json.dumps([
                {"turn": 1, "summary": "讨论了个人助手的设计"},
                {"turn": 2, "summary": "个人助手的功能规划"},
            ], ensure_ascii=False), encoding="utf-8",
        )

        flush_vocab_cache()
        idx = _build_vocabulary(agent_root, sessions_root)
        # 会话摘要中的领域词（出现 ≥2 次）应进入词汇表
        assert "个人助手" in idx.vocab

    def test_no_sessions_root_no_crash(self, tmp_path):
        """sessions_root 为 None 时（旧调用方）行为不变，不扫描会话。"""
        from core.context.tokenizer import _build_vocabulary, flush_vocab_cache
        agent_root = _make_agent_dir(tmp_path)
        flush_vocab_cache()
        idx = _build_vocabulary(agent_root)  # 无 sessions_root
        assert idx.built is True


class TestFlushCache:
    """Cache flush behavior."""

    def test_flush_cache_rebuilds_vocab(self, tmp_path):
        """flush_cache() 后词汇索引应立即重建（非空），而非延迟构建。"""
        agent_root = _make_agent_dir(tmp_path)
        pipeline = ContextPipeline(agent_root=agent_root)

        # flush_cache() 同步重建词汇索引（确保 pipeline / recall / capture 共享）
        pipeline.flush_cache()
        assert pipeline._vocab_index.built
        # 有 entry 数据时应构建非空词汇表
        assert pipeline._vocab_index.N > 0

    @pytest.mark.asyncio
    async def test_flush_cache_clears_file_cache(self, tmp_path):
        agent_root = _make_agent_dir(tmp_path)
        pipeline = ContextPipeline(agent_root=agent_root)

        soul_path = agent_root / "soul.md"
        _ = await pipeline._read_cached(soul_path)
        assert str(soul_path) in pipeline._cache

        pipeline.flush_cache()
        assert str(soul_path) not in pipeline._cache
