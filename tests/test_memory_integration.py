"""集成测试：ReflectEngine 解析 + 写入 + 回滚。"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from core.memory.reflector import (
    ReflectEngine, ReflectResult, _clean_markdown_response,
)
from core.memory.version import (
    _backup_prompt, _append_version_log, rollback_prompt,
)
from core.storage import JsonStore


class TestReflectEngineParsing:
    def test_parse_reflection_output_all_sections(self):
        engine = ReflectEngine(MagicMock())
        raw = """## Session Overview
- Discussed project architecture

## Preferences
- 喜欢简洁代码

## Workflows
- 修改前先读文件

## Long-Term Memory
- 用户是 Python 开发者"""
        current = {"preferences.md": "", "workflows.md": "", "long_term_memory.md": ""}
        result = engine._parse_reflection_output(raw, current)
        assert "喜欢简洁代码" in result["preferences"]
        assert "修改前先读文件" in result["workflows"]
        assert "Python 开发者" in result["long_term_memory"]
        assert "project architecture" in result["overview"]

    def test_parse_reflection_output_no_changes(self):
        engine = ReflectEngine(MagicMock())
        raw = """## Session Overview
- One topic

## Preferences
(无变更)

## Workflows
(no changes)

## Long-Term Memory
(unchanged)"""
        current = {
            "preferences.md": "- 原偏好",
            "workflows.md": "- 原工作流",
            "long_term_memory.md": "- 原记忆",
        }
        result = engine._parse_reflection_output(raw, current)
        assert result["preferences"] == current["preferences.md"]
        assert result["workflows"] == current["workflows.md"]
        assert result["long_term_memory"] == current["long_term_memory.md"]

    def test_split_sections(self):
        engine = ReflectEngine(MagicMock())
        sections = engine._split_sections("## A\ncontent a\n\n## B\ncontent b")
        assert sections == {"A": "content a", "B": "content b"}


class TestReflectEngineComputeDiff:
    def test_no_changes_empty_diff(self):
        engine = ReflectEngine(MagicMock())
        current = {"preferences.md": "- same", "workflows.md": "- same", "long_term_memory.md": "- same"}
        proposed = {"preferences.md": "- same", "workflows.md": "- same", "long_term_memory.md": "- same"}
        diff = engine._compute_diff(current, proposed)
        assert diff == ""

    def test_changes_produce_diff(self):
        engine = ReflectEngine(MagicMock())
        current = {"preferences.md": "- old", "workflows.md": "", "long_term_memory.md": ""}
        proposed = {"preferences.md": "- new", "workflows.md": "", "long_term_memory.md": ""}
        diff = engine._compute_diff(current, proposed)
        assert "old" in diff
        assert "new" in diff


class TestReflectEngineReadRecentTurns:
    def test_empty_messages_dir(self, tmp_path):
        engine = ReflectEngine(MagicMock())
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        result = engine._read_recent_turns(session_dir, 0, 5)
        assert result == ""

    def test_reads_turns_in_range(self, tmp_path):
        engine = ReflectEngine(MagicMock())
        session_dir = tmp_path / "session"
        messages_dir = session_dir / "messages"
        messages_dir.mkdir(parents=True)
        turn = {"turn": 1, "user": "Hello", "assistant": "Hi!", "tool_calls": []}
        (messages_dir / "turn_001.json").write_text(json.dumps(turn, ensure_ascii=False), encoding="utf-8")

        result = engine._read_recent_turns(session_dir, 0, 1)
        assert "Turn 1" in result
        assert "Hello" in result
        assert "Hi!" in result


class TestReflectEngineMemory:
    def test_read_empty_memory(self, tmp_path):
        agent_root = tmp_path / "agent"
        agent_root.mkdir()
        engine = ReflectEngine(MagicMock(), agent_root=agent_root)
        mem = engine._read_current_memory()
        assert mem["preferences.md"] == ""
        assert mem["workflows.md"] == ""
        assert mem["long_term_memory.md"] == ""

    def test_read_existing_memory(self, tmp_path):
        agent_root = tmp_path / "agent"
        agent_root.mkdir()
        (agent_root / "preferences.md").write_text("- test pref", encoding="utf-8")
        engine = ReflectEngine(MagicMock(), agent_root=agent_root)
        mem = engine._read_current_memory()
        assert "- test pref" in mem["preferences.md"]


class TestReflectResult:
    def test_dataclass_fields(self):
        result = ReflectResult(
            overview="test overview",
            proposed_files={"preferences.md": "- new"},
            current_files={"preferences.md": "- old"},
            diff="diff content",
            changes_detected=True,
        )
        assert result.changes_detected is True
        assert result.overview == "test overview"
        assert "diff content" in result.diff
