"""Tests for ContextCompactor — session compression (/compress)."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from core.storage import JsonStore
from core.context.compactor import (
    ContextCompactor,
    get_compact_system_prompt,
    _clean_markdown_response,
    parse_overview_md,
    restore_overview_from_checkpoint,
    _is_correction,
    _classify_turn,
    _has_write_tool,
)


class TestCompactorInit:
    def test_init_stores_provider_and_store(self):
        provider = MagicMock()
        store = MagicMock()
        compactor = ContextCompactor(provider, store)
        assert compactor._provider is provider
        assert compactor._store is store


class TestCompactorCompact:
    @pytest.fixture
    def session_dir(self, tmp_path):
        d = tmp_path / "session"
        d.mkdir()
        (d / "messages").mkdir()
        return d

    @pytest.fixture
    def store(self):
        s = MagicMock(spec=JsonStore)
        s.write = AsyncMock()
        return s

    @pytest.fixture
    def provider(self):
        p = MagicMock()
        return p

    def _write_turns(self, session_dir, turns: list[dict]):
        for t in turns:
            path = session_dir / "messages" / f"turn_{t['turn']:03d}.json"
            path.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")

    @pytest.mark.asyncio
    async def test_no_messages_dir(self, session_dir, store, provider):
        (session_dir / "messages").rmdir()
        compactor = ContextCompactor(provider, store)
        result = await compactor.compact(session_dir)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_turn_files(self, session_dir, store, provider):
        compactor = ContextCompactor(provider, store)
        result = await compactor.compact(session_dir)
        assert result is None

    @pytest.mark.asyncio
    async def test_compacts_single_turn(self, session_dir, store, provider):
        self._write_turns(session_dir, [
            {"turn": 1, "user": "hello", "assistant": "Hi!", "tool_calls": []},
        ])
        provider.chat_with_tools = _make_llm_stream(
            "## 话题\n- greeting\n\n## 用户偏好\n\n## 纠正记录\n\n## 决策与结论"
        )
        compactor = ContextCompactor(provider, store)
        result = await compactor.compact(session_dir)

        assert result is not None
        assert "## 话题" in result
        assert "greeting" in result
        # 3 writes: overview.md + overview.json checkpoints + cache.json clear
        assert store.write.call_count == 3
        # First write should be overview.md
        call_args = store.write.call_args_list[0]
        assert "overview.md" in str(call_args[0][0])

    @pytest.mark.asyncio
    async def test_writes_overview_json_checkpoint(self, session_dir, store, provider):
        self._write_turns(session_dir, [
            {"turn": 1, "user": "hi", "assistant": "hey", "tool_calls": []},
        ])
        provider.chat_with_tools = _make_llm_stream(
            "## 话题\n- chat\n\n## 用户偏好\n\n## 纠正记录\n\n## 决策与结论"
        )
        compactor = ContextCompactor(provider, store)
        await compactor.compact(session_dir)

        # Second write should be overview.json checkpoints
        checkpoint_call = store.write.call_args_list[1]
        assert "overview.json" in str(checkpoint_call[0][0])
        checkpoints = checkpoint_call[0][1]
        assert isinstance(checkpoints, list)
        assert len(checkpoints) == 1
        assert checkpoints[0]["to_turn"] == 1
        assert "overview_md" in checkpoints[0]

    @pytest.mark.asyncio
    async def test_clears_cache_after_compact(self, session_dir, store, provider):
        self._write_turns(session_dir, [
            {"turn": 1, "user": "hi", "assistant": "hey", "tool_calls": []},
        ])
        provider.chat_with_tools = _make_llm_stream(
            "## 话题\n- chat\n\n## 用户偏好\n\n## 纠正记录\n\n## 决策与结论"
        )
        compactor = ContextCompactor(provider, store)
        await compactor.compact(session_dir)

        # cache.json should be cleared (written with empty list)
        cache_write = store.write.call_args_list[2]
        assert "cache" in str(cache_write[0][0])
        assert cache_write[0][1] == []

    @pytest.mark.asyncio
    async def test_includes_tool_call_info(self, session_dir, store, provider):
        self._write_turns(session_dir, [
            {
                "turn": 1,
                "user": "read file",
                "assistant": "file contents...",
                "tool_calls": [{"function": {"name": "read_file"}}],
            },
        ])
        provider.chat_with_tools = _make_llm_stream(
            "## 话题\n- file reading\n\n## 用户偏好\n\n## 纠正记录\n\n## 决策与结论"
        )
        compactor = ContextCompactor(provider, store)
        result = await compactor.compact(session_dir)
        assert result is not None

    @pytest.mark.asyncio
    async def test_truncates_long_transcript(self, session_dir, store, provider):
        """Very long transcript should be truncated to prevent token overflow."""
        turns = []
        for i in range(50):
            turns.append({
                "turn": i + 1,
                "user": "long message " * 30,
                "assistant": "long reply " * 30,
                "tool_calls": [],
            })
        self._write_turns(session_dir, turns)
        provider.chat_with_tools = _make_llm_stream(
            "## 话题\n- many topics\n\n## 用户偏好\n\n## 纠正记录\n\n## 决策与结论"
        )
        compactor = ContextCompactor(provider, store)
        result = await compactor.compact(session_dir)
        assert result is not None

    @pytest.mark.asyncio
    async def test_llm_error_returns_none(self, session_dir, store, provider):
        self._write_turns(session_dir, [
            {"turn": 1, "user": "hi", "assistant": "hey", "tool_calls": []},
        ])
        def _raise_error(messages, tools):
            raise RuntimeError("LLM unavailable")
        provider.chat_with_tools = _raise_error
        compactor = ContextCompactor(provider, store)
        result = await compactor.compact(session_dir)
        assert result is None

    @pytest.mark.asyncio
    async def test_appends_to_existing_checkpoints(self, session_dir, store, provider):
        """已有 overview.json 时追加检查点。"""
        self._write_turns(session_dir, [
            {"turn": 1, "user": "hi", "assistant": "hey", "tool_calls": []},
            {"turn": 2, "user": "more", "assistant": "ok", "tool_calls": []},
        ])
        # Pre-existing checkpoint
        provider.chat_with_tools = _make_llm_stream(
            "## 话题\n- chat\n\n## 用户偏好\n\n## 纠正记录\n\n## 决策与结论"
        )
        compactor = ContextCompactor(provider, store)

        # First compress — creates checkpoint
        await compactor.compact(session_dir)
        assert store.write.call_count == 3  # overview.md + overview.json + cache

        # Second compress — appends
        await compactor.compact(session_dir)
        checkpoint_call = store.write.call_args_list[4]  # 2nd overview.json write
        checkpoints = checkpoint_call[0][1]
        assert len(checkpoints) >= 1


class TestCleanMarkdownResponse:
    def test_plain_text_unchanged(self):
        result = _clean_markdown_response("## 话题\n- hello")
        assert result == "## 话题\n- hello"

    def test_strips_code_block(self):
        result = _clean_markdown_response("```markdown\n## 话题\n- hello\n```")
        assert result == "## 话题\n- hello"

    def test_strips_code_block_no_lang(self):
        result = _clean_markdown_response("```\n## 话题\n- hello\n```")
        assert result == "## 话题\n- hello"

    def test_strips_leading_backticks(self):
        result = _clean_markdown_response("```\n## 话题\n- hello")
        assert result == "## 话题\n- hello"


class TestParseOverviewMd:
    def test_parses_sections(self):
        text = "## 话题\n- topic 1\n- topic 2\n\n## 用户偏好\n- pref 1"
        sections = parse_overview_md(text)
        assert sections["话题"] == ["topic 1", "topic 2"]
        assert sections["用户偏好"] == ["pref 1"]

    def test_empty_text(self):
        assert parse_overview_md("") == {}

    def test_no_list_items(self):
        text = "## 话题\n\n## 结论"
        sections = parse_overview_md(text)
        assert sections == {"话题": [], "结论": []}


class TestRestoreOverviewFromCheckpoint:
    def test_restores_matching_checkpoint(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        checkpoints = [
            {"to_turn": 5, "compressed_at": "t1", "overview_md": "## Overview v1"},
            {"to_turn": 12, "compressed_at": "t2", "overview_md": "## Overview v2"},
            {"to_turn": 20, "compressed_at": "t3", "overview_md": "## Overview v3"},
        ]
        (session_dir / "overview.json").write_text(
            json.dumps(checkpoints, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        ok = restore_overview_from_checkpoint(session_dir, 10)
        assert ok
        # Should restore v2 (to_turn=12 is >10, so last <=10 is to_turn=5)
        restored = (session_dir / "overview.md").read_text(encoding="utf-8")
        assert "v1" in restored

        # overview.json should be truncated
        remaining = json.loads((session_dir / "overview.json").read_text(encoding="utf-8"))
        assert len(remaining) == 1
        assert remaining[0]["to_turn"] == 5

    def test_no_checkpoint_file(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        ok = restore_overview_from_checkpoint(session_dir, 3)
        assert not ok

    def test_no_matching_checkpoint_removes_overview_md(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / "overview.md").write_text("## stale")
        checkpoints = [
            {"to_turn": 10, "compressed_at": "t1", "overview_md": "## Overview"},
        ]
        (session_dir / "overview.json").write_text(
            json.dumps(checkpoints, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        ok = restore_overview_from_checkpoint(session_dir, 3)
        assert not ok  # No checkpoint with to_turn <= 3
        assert not (session_dir / "overview.md").exists()


class TestCompactSystemPrompt:
    def test_prompt_is_chinese(self):
        from core.locale import set_locale
        set_locale("zh")
        assert "会话压缩" in get_compact_system_prompt()

    def test_prompt_asks_for_markdown(self):
        assert "Markdown" in get_compact_system_prompt()


class TestTierClassification:
    """Tests for tiered compression helpers."""

    def test_is_correction(self):
        """纠正类消息被正确识别。"""
        assert _is_correction("不对，应该是这样") is True
        assert _is_correction("No, that's wrong") is True
        assert _is_correction("今天天气不错") is False  # "不错" not correction

    def test_classify_critical_turn(self):
        """纠正/决策轮次被分类为 critical。"""
        data = {"user": "不对，这个方案有问题", "tool_calls": []}
        assert _classify_turn(data, 5, 20) == "critical"

    def test_classify_early_turn(self):
        """窗口外的轮次被分类为 early。"""
        data = {"user": "hello", "tool_calls": []}
        # 总 50 轮，当前第 5 轮 → early (50-5 > 8)
        assert _classify_turn(data, 5, 50, window_turns=8) == "early"

    def test_classify_mid_turn(self):
        """窗口内普通轮次被分类为 mid。"""
        data = {"user": "what is this?", "tool_calls": []}
        # 总 10 轮，当前第 8 轮 → mid (10-8=2 <= 8, normal)
        assert _classify_turn(data, 8, 10, window_turns=8) == "mid"

    def test_has_write_tool(self):
        """文件写入工具检测。"""
        assert _has_write_tool({"tool_calls": [{"name": "write_file"}]}) is True
        assert _has_write_tool({"tool_calls": [{"name": "read_file"}]}) is False


def _make_llm_stream(text: str):
    """Return an async function that's compatible with chat_with_tools."""
    from core.llm_gateway.provider import TextDelta, StreamEnd

    async def stream(messages, tools):
        yield TextDelta(text)
        yield StreamEnd(finish_reason="stop", tool_calls=[])

    return stream
