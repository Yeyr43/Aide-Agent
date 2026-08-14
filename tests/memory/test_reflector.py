"""测试 ReflectEngine — 备份、版本日志、回滚。"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from core.memory.version import (
    _backup_prompt, _append_version_log, rollback_prompt,
    BACKUPS_DIR, AGENT_ROOT,
)


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
