"""Tests for ContextPipeline.assemble() — six-layer context assembly."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from core.context.pipeline import ContextPipeline


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
