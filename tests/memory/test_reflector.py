"""测试 ReflectEngine — 备份、版本日志、回滚。"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from core.memory.version import (
    _backup_prompt, _append_version_log, rollback_prompt,
    BACKUPS_DIR, AGENT_ROOT,
)
from core.memory.reflector import ReflectEngine


class TestBackupPrompt:
    """测试 _backup_prompt()。"""

    def test_creates_backup(self, tmp_path):
        prompt = tmp_path / "preferences.md"
        prompt.write_text("test content", encoding="utf-8")
        with patch('core.memory.version.BACKUPS_DIR', tmp_path):
            backup_name = _backup_prompt(prompt)
            assert backup_name is not None
            assert backup_name.startswith("preferences.md_")
            assert backup_name.endswith(".backup")
            backup_file = tmp_path / backup_name
            assert backup_file.exists()
            assert backup_file.read_text(encoding="utf-8") == "test content"

    def test_returns_none_for_missing_file(self, tmp_path):
        prompt = tmp_path / "nonexistent.md"
        with patch('core.memory.version.BACKUPS_DIR', tmp_path):
            result = _backup_prompt(prompt)
            assert result is None


class TestVersionLog:
    """测试 _append_version_log()。"""

    def test_creates_new_log(self, tmp_path):
        with patch('core.memory.version.BACKUPS_DIR', tmp_path):
            _append_version_log("preferences.md", "preferences.md_test.backup")
            log_path = tmp_path / "version_log.json"
            assert log_path.exists()
            log = json.loads(log_path.read_text(encoding="utf-8"))
            assert "preferences.md" in log
            assert len(log["preferences.md"]) == 1
            assert log["preferences.md"][0]["backup"] == "preferences.md_test.backup"

    def test_appends_to_existing_log(self, tmp_path):
        log_path = tmp_path / "version_log.json"
        log_path.write_text(json.dumps({"preferences.md": [{"old": "entry"}]}), encoding="utf-8")
        with patch('core.memory.version.BACKUPS_DIR', tmp_path):
            _append_version_log("preferences.md", "preferences.md_test.backup")
            log = json.loads(log_path.read_text(encoding="utf-8"))
            assert len(log["preferences.md"]) == 2


class TestRollbackPrompt:
    """测试 rollback_prompt()。"""

    def test_no_log_returns_error(self, tmp_path):
        with patch('core.memory.version.BACKUPS_DIR', tmp_path):
            success, msg = rollback_prompt("preferences", 0)
            assert not success
            assert "无版本历史" in msg or "does not exist" in msg.lower()

    def test_invalid_n_returns_error(self, tmp_path):
        log_path = tmp_path / "version_log.json"
        log_path.write_text(json.dumps({"preferences.md": []}), encoding="utf-8")
        with patch('core.memory.version.BACKUPS_DIR', tmp_path):
            success, msg = rollback_prompt("preferences", 0)
            assert not success
            assert "无备份记录" in msg or "No backup" in msg

    def test_restores_content(self, tmp_path):
        # Setup backup
        backup_name = "preferences.md_test.backup"
        backup_file = tmp_path / backup_name
        backup_file.write_text("restored content", encoding="utf-8")

        # Setup version log
        log_path = tmp_path / "version_log.json"
        log_path.write_text(json.dumps({
            "preferences.md": [{
                "timestamp": "2024-01-01T00:00:00+00:00",
                "backup": backup_name,
                "size": len("restored content"),
            }]
        }), encoding="utf-8")

        # Setup prompt
        prompt_path = tmp_path / "preferences.md"
        prompt_path.write_text("old content", encoding="utf-8")

        with patch('core.memory.version.BACKUPS_DIR', tmp_path), \
             patch('core.memory.version.AGENT_ROOT', tmp_path):
            success, msg = rollback_prompt("preferences", 0)
            assert success
            assert prompt_path.read_text(encoding="utf-8") == "restored content"

    def test_missing_backup_returns_error(self, tmp_path):
        log_path = tmp_path / "version_log.json"
        log_path.write_text(json.dumps({
            "preferences.md": [{
                "timestamp": "2024-01-01T00:00:00+00:00",
                "backup": "missing.backup",
                "size": 100,
            }]
        }), encoding="utf-8")
        with patch('core.memory.version.BACKUPS_DIR', tmp_path):
            success, msg = rollback_prompt("preferences", 0)
            assert not success
            assert "丢失" in msg or "lost" in msg.lower()


class TestReflectDiff:
    """回归测试：reflect() 的 key 归一化 + prompt 示例头格式。

    audit 发现：_parse_reflection_output 返回无 .md 后缀的 key，而
    _compute_diff / changes 用 .md key，导致 changes_detected 恒 True、
    diff 算成"整删"；且 prompt 示例用 "### ## Preferences" 头，
    split_sections 只认 "## " 前缀，LLM 照示例输出会静默丢失记忆更新。
    """

    @staticmethod
    def _setup(agent_root: Path, session_dir: Path) -> None:
        agent_root.mkdir()
        (agent_root / "preferences.md").write_text("# 偏好\n\n- 用户喜欢简洁\n", encoding="utf-8")
        (agent_root / "workflows.md").write_text("# 工作流\n\n- 用中文回复\n", encoding="utf-8")
        (agent_root / "long_term_memory.md").write_text("# 长记忆\n", encoding="utf-8")
        (session_dir / "messages").mkdir(parents=True)
        (session_dir / "messages" / "turn_001.json").write_text(json.dumps({
            "turn": 1,
            "messages": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好，有什么可以帮你？"},
            ],
        }), encoding="utf-8")
        (session_dir / "meta.json").write_text(json.dumps({"last_reflected_turn": 0}), encoding="utf-8")

    def test_no_change_returns_false_changes_and_empty_diff(self, tmp_path, monkeypatch):
        agent_root = tmp_path / "agent"
        session_dir = tmp_path / "sess"
        self._setup(agent_root, session_dir)
        engine = ReflectEngine(provider=object(), agent_root=agent_root,
                               sessions_root=tmp_path)

        no_change = (
            "## Preferences\n(无变更)\n"
            "## Workflows\n(无变更)\n"
            "## Long-Term Memory\n(无变更)\n"
        )
        async def _fake(*a, **k): return no_change
        monkeypatch.setattr(engine, "_call_llm_for_reflection", _fake)
        result = self._run(engine, session_dir)
        assert result is not None
        assert result.changes_detected is False, "无变更时不应报告有变更"
        assert result.diff == ""

    def test_change_parsed_from_section_format(self, tmp_path, monkeypatch):
        """LLM 按示例格式输出新增条目 → 能被解析进 proposed_files（防静默丢记忆）。"""
        agent_root = tmp_path / "agent"
        session_dir = tmp_path / "sess"
        self._setup(agent_root, session_dir)
        engine = ReflectEngine(provider=object(), agent_root=agent_root,
                               sessions_root=tmp_path)

        llm_out = (
            "## Preferences\n"
            "---\nid: pref_001\ncreated: 2026-08-16\n"
            "---\n- 用户喜欢中文\n"
            "## Workflows\n(无变更)\n"
            "## Long-Term Memory\n(无变更)\n"
        )
        async def _fake2(*a, **k): return llm_out
        monkeypatch.setattr(engine, "_call_llm_for_reflection", _fake2)
        result = self._run(engine, session_dir)
        assert result is not None
        assert result.changes_detected is True
        assert "用户喜欢中文" in result.proposed_files["preferences.md"]
        assert "pref_001" in result.proposed_files["preferences.md"]
        assert result.diff != ""

    def test_system_prompt_uses_parseable_section_headers(self):
        """prompt 示例头必须是 "## Preferences"（split_sections 可解析），不能是 "### ##"。"""
        from core.memory.reflector import ReflectEngine
        engine = ReflectEngine(provider=object())
        prompt = engine._build_system_prompt()
        assert "## Preferences" in prompt
        assert "### ##" not in prompt
        assert "## Workflows" in prompt

    @staticmethod
    def _run(engine, session_dir):
        import asyncio
        return asyncio.run(engine.reflect(session_dir, current_turn=1))
