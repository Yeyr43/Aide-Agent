"""Tests for reflection/overview functions (P5: migrated from compactor)."""

import json
import pytest
from pathlib import Path

from core.context.overview import (
    parse_overview_md, restore_overview_from_checkpoint, read_current_overview,
)
from core.memory.reflector import _clean_markdown_response


class TestParseOverviewMd:
    def test_empty_text(self):
        assert parse_overview_md("") == {}

    def test_parses_sections(self):
        text = "## 话题\n- topic 1\n- topic 2\n\n## 决策与结论\n- decided X"
        result = parse_overview_md(text)
        assert result == {"话题": ["topic 1", "topic 2"], "决策与结论": ["decided X"]}

    def test_ignores_non_list_lines(self):
        text = "## 话题\n- item 1\nSome paragraph text\n- item 2"
        result = parse_overview_md(text)
        assert result == {"话题": ["item 1", "item 2"]}

    def test_english_sections(self):
        text = "## Topics\n- topic A\n\n## Decisions & Conclusions\n- decided B"
        result = parse_overview_md(text)
        assert result == {"Topics": ["topic A"], "Decisions & Conclusions": ["decided B"]}


class TestRestoreOverview:
    def test_no_checkpoint_file_returns_false(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        assert restore_overview_from_checkpoint(session_dir, 5) is False

    def test_restores_from_checkpoint(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        overview_json = session_dir / "overview.json"
        overview_json.write_text(json.dumps([
            {"to_turn": 3, "compressed_at": "2024-01-01T00:00:00Z", "overview_md": "## Topics\n- v1"},
            {"to_turn": 5, "compressed_at": "2024-01-01T01:00:00Z", "overview_md": "## Topics\n- v2"},
        ]), encoding="utf-8")

        assert restore_overview_from_checkpoint(session_dir, 3) is True
        # 当前生效版 = 截断后 overview.json 最后一条检查点（不再写 overview.md）
        assert not (session_dir / "overview.md").exists()
        checkpoints = json.loads((session_dir / "overview.json").read_text(encoding="utf-8"))
        assert len(checkpoints) == 1
        assert checkpoints[0]["to_turn"] == 3
        assert "v1" in read_current_overview(session_dir)


class TestReadCurrentOverview:
    def test_empty_when_no_file(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        assert read_current_overview(session_dir) == ""

    def test_reads_latest_checkpoint(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / "overview.json").write_text(json.dumps([
            {"to_turn": 3, "overview_md": "## Topics\n- v1"},
            {"to_turn": 5, "overview_md": "## Topics\n- v2"},
        ]), encoding="utf-8")
        assert "v2" in read_current_overview(session_dir)

    def test_fallback_to_legacy_md(self, tmp_path):
        """旧会话只有 overview.md → 兼容回退。"""
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / "overview.md").write_text("## Topics\n- legacy", encoding="utf-8")
        assert "legacy" in read_current_overview(session_dir)


class TestCleanMarkdownResponse:
    def test_strips_code_fence(self):
        text = "```markdown\n# Title\n- item\n```"
        result = _clean_markdown_response(text)
        assert result == "# Title\n- item"

    def test_no_fence_unchanged(self):
        text = "# Title\n- item"
        result = _clean_markdown_response(text)
        assert result == text.strip()

    def test_strips_leading_trailing_backticks(self):
        text = "```\n# Title\n- item\n```"
        result = _clean_markdown_response(text)
        assert result == "# Title\n- item"
