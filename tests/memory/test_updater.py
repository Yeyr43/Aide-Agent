"""测试 PromptUpdater — 备份、版本日志、回滚。"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from core.memory.updater import (
    _backup_prompt, _append_version_log, rollback_prompt,
    BACKUPS_DIR, AGENT_ROOT,
)


class TestBackupPrompt:
    """测试 _backup_prompt()。"""

    def test_creates_backup(self, tmp_path):
        prompt = tmp_path / "preferences.md"
        prompt.write_text("test content", encoding="utf-8")
        with patch('core.memory.updater.BACKUPS_DIR', tmp_path):
            backup_name = _backup_prompt(prompt)
            assert backup_name is not None
            assert backup_name.startswith("preferences.md_")
            assert (tmp_path / backup_name).exists()

    def test_file_not_exists_returns_none(self, tmp_path):
        prompt = tmp_path / "nonexistent.md"
        with patch('core.memory.updater.BACKUPS_DIR', tmp_path):
            result = _backup_prompt(prompt)
            assert result is None

    def test_backup_contains_same_content(self, tmp_path):
        prompt = tmp_path / "workflows.md"
        prompt.write_text("original workflows", encoding="utf-8")
        with patch('core.memory.updater.BACKUPS_DIR', tmp_path):
            backup_name = _backup_prompt(prompt)
            backup_content = (tmp_path / backup_name).read_text(encoding="utf-8")
            assert backup_content == "original workflows"


class TestVersionLog:
    """测试 _append_version_log()。"""

    def test_creates_new_log(self, tmp_path):
        with patch('core.memory.updater.BACKUPS_DIR', tmp_path):
            # 创建一个假的 backup 文件
            (tmp_path / "pref_backup").write_text("dummy")
            _append_version_log("preferences.md", "pref_backup")
            log_path = tmp_path / "version_log.json"
            assert log_path.exists()
            data = json.loads(log_path.read_text(encoding="utf-8"))
            assert "preferences.md" in data
            assert len(data["preferences.md"]) == 1

    def test_appends_to_existing_log(self, tmp_path):
        with patch('core.memory.updater.BACKUPS_DIR', tmp_path):
            (tmp_path / "b1").write_text("v1")
            (tmp_path / "b2").write_text("v2")
            _append_version_log("preferences.md", "b1")
            _append_version_log("preferences.md", "b2")
            data = json.loads(
                (tmp_path / "version_log.json").read_text(encoding="utf-8")
            )
            assert len(data["preferences.md"]) == 2


class TestRollbackPrompt:
    """测试 rollback_prompt()。"""

    def test_no_log_returns_error(self, tmp_path):
        with patch('core.memory.updater.BACKUPS_DIR', tmp_path):
            success, msg = rollback_prompt("preferences")
            assert not success
            assert "无版本历史" in msg or "不存在" in msg

    def test_invalid_n_returns_error(self, tmp_path):
        """无效的 N 返回错误。"""
        with patch('core.memory.updater.BACKUPS_DIR', tmp_path):
            # 创建版本日志但无历史记录
            log = {"preferences.md": []}
            (tmp_path / "version_log.json").write_text(
                json.dumps(log), encoding="utf-8"
            )
            success, msg = rollback_prompt("preferences", n=0)
            assert not success

    def test_rollback_restores_content(self, tmp_path):
        """回滚恢复正确的内容。"""
        with patch('core.memory.updater.BACKUPS_DIR', tmp_path), \
             patch('core.memory.updater.AGENT_ROOT', tmp_path):
            # 创建 backup 和 version log
            backup_name = "preferences.md_20260704_120000.backup"
            (tmp_path / backup_name).write_text("old preferences", encoding="utf-8")
            log = {"preferences.md": [
                {"timestamp": "2026-07-04T12:00:00Z",
                 "backup": backup_name, "size": 16}
            ]}
            (tmp_path / "version_log.json").write_text(
                json.dumps(log), encoding="utf-8"
            )

            success, msg = rollback_prompt("preferences", n=0)
            assert success
            # 验证恢复的内容
            current = (tmp_path / "preferences.md").read_text(encoding="utf-8")
            assert current == "old preferences"

    def test_backup_file_missing(self, tmp_path):
        """备份文件丢失时返回错误。"""
        with patch('core.memory.updater.BACKUPS_DIR', tmp_path):
            log = {"preferences.md": [
                {"timestamp": "2026-07-04T12:00:00Z",
                 "backup": "missing.backup", "size": 100}
            ]}
            (tmp_path / "version_log.json").write_text(
                json.dumps(log), encoding="utf-8"
            )
            success, msg = rollback_prompt("preferences", n=0)
            assert not success
            assert "丢失" in msg
