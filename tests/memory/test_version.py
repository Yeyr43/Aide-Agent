"""测试 version.py — Prompt 备份、版本日志、回滚。"""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from core.memory.version import (
    _backup_prompt,
    _append_version_log,
    rollback_prompt,
    AGENT_ROOT,
    BACKUPS_DIR,
)


class TestBackupPrompt:
    """测试 _backup_prompt 函数。"""

    def test_backup_existing_file(self, tmp_path, monkeypatch):
        """备份已存在的 prompt 文件。"""
        monkeypatch.setattr(
            "core.memory.version.BACKUPS_DIR", tmp_path / "backups"
        )
        prompt_file = tmp_path / "preferences.md"
        prompt_file.write_text("- 测试内容", encoding="utf-8")

        backup_name = _backup_prompt(prompt_file)
        assert backup_name is not None
        assert backup_name.startswith("preferences.md_")
        assert backup_name.endswith(".backup")
        assert (tmp_path / "backups" / backup_name).exists()

    def test_backup_nonexistent_file(self, tmp_path, monkeypatch):
        """不存在的文件返回 None。"""
        monkeypatch.setattr(
            "core.memory.version.BACKUPS_DIR", tmp_path / "backups"
        )
        result = _backup_prompt(tmp_path / "nonexistent.md")
        assert result is None

    def test_backup_creates_backups_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.memory.version.BACKUPS_DIR", tmp_path / "new_backups"
        )
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text("data", encoding="utf-8")
        backup_name = _backup_prompt(prompt_file)
        assert (tmp_path / "new_backups").exists()
        assert (tmp_path / "new_backups" / backup_name).exists()

    def test_backup_preserves_content(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.memory.version.BACKUPS_DIR", tmp_path / "backups"
        )
        prompt_file = tmp_path / "original.md"
        original_content = "---\nid: pref_001\n---\n- 原始内容"
        prompt_file.write_text(original_content, encoding="utf-8")

        backup_name = _backup_prompt(prompt_file)
        backup_path = tmp_path / "backups" / backup_name
        assert backup_path.read_text(encoding="utf-8") == original_content


class TestAppendVersionLog:
    """测试 _append_version_log 函数。"""

    def test_create_new_log(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.memory.version.BACKUPS_DIR", tmp_path / "backups"
        )
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir(parents=True)
        # 创建一个备份文件，这样 stat 不会失败
        backup_file = backups_dir / "preferences.md_test.backup"
        backup_file.write_text("data")

        _append_version_log("preferences.md", "preferences.md_test.backup")

        log_path = backups_dir / "version_log.json"
        assert log_path.exists()
        log = json.loads(log_path.read_text(encoding="utf-8"))
        assert "preferences.md" in log
        assert len(log["preferences.md"]) == 1
        assert log["preferences.md"][0]["backup"] == "preferences.md_test.backup"

    def test_append_to_existing_log(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.memory.version.BACKUPS_DIR", tmp_path / "backups"
        )
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir(parents=True)

        # 已有日志
        existing_log = {
            "preferences.md": [
                {"timestamp": "2026-01-01T00:00:00", "backup": "old.backup", "size": 100}
            ]
        }
        (backups_dir / "version_log.json").write_text(
            json.dumps(existing_log), encoding="utf-8"
        )

        # 创建备份文件
        (backups_dir / "new.backup").write_text("new data")

        _append_version_log("preferences.md", "new.backup")

        log = json.loads((backups_dir / "version_log.json").read_text(encoding="utf-8"))
        assert len(log["preferences.md"]) == 2

    def test_new_file_added(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.memory.version.BACKUPS_DIR", tmp_path / "backups"
        )
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir(parents=True)
        (backups_dir / "test.backup").write_text("data")

        _append_version_log("workflows.md", "test.backup")

        log = json.loads((backups_dir / "version_log.json").read_text(encoding="utf-8"))
        assert "workflows.md" in log


class TestRollbackPrompt:
    """测试 rollback_prompt 函数。"""

    def test_no_log_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.memory.version.BACKUPS_DIR", tmp_path / "nonexistent"
        )
        ok, msg = rollback_prompt("preferences")
        assert not ok
        assert "不存在" in msg

    def test_empty_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.memory.version.BACKUPS_DIR", tmp_path / "backups"
        )
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir(parents=True)
        (backups_dir / "version_log.json").write_text(
            json.dumps({"preferences.md": []}), encoding="utf-8"
        )
        ok, msg = rollback_prompt("preferences")
        assert not ok
        assert "无备份记录" in msg

    def test_invalid_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.memory.version.BACKUPS_DIR", tmp_path / "backups"
        )
        monkeypatch.setattr(
            "core.memory.version.AGENT_ROOT", tmp_path / "agent"
        )
        backups_dir = tmp_path / "backups"
        agent_dir = tmp_path / "agent"
        backups_dir.mkdir(parents=True)
        agent_dir.mkdir(parents=True)

        (agent_dir / "preferences.md").write_text("current")
        backup_path = backups_dir / "preferences.md_test.backup"
        backup_path.write_text("backup content")

        log = {
            "preferences.md": [
                {"timestamp": "2026-01-01T00:00:00", "backup": "preferences.md_test.backup", "size": 14}
            ]
        }
        (backups_dir / "version_log.json").write_text(json.dumps(log))

        ok, msg = rollback_prompt("preferences", n=5)
        assert not ok
        assert "无效的备份编号" in msg

    def test_successful_rollback(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.memory.version.BACKUPS_DIR", tmp_path / "backups"
        )
        monkeypatch.setattr(
            "core.memory.version.AGENT_ROOT", tmp_path / "agent"
        )
        backups_dir = tmp_path / "backups"
        agent_dir = tmp_path / "agent"
        backups_dir.mkdir(parents=True)
        agent_dir.mkdir(parents=True)

        (agent_dir / "preferences.md").write_text("current content")
        backup_path = backups_dir / "preferences.md_backup1.backup"
        backup_path.write_text("old content")

        log = {
            "preferences.md": [
                {"timestamp": "2026-01-01T00:00:00", "backup": "preferences.md_backup1.backup", "size": 11}
            ]
        }
        (backups_dir / "version_log.json").write_text(json.dumps(log))

        ok, msg = rollback_prompt("preferences", n=0)
        assert ok
        assert "已回滚" in msg
        assert (agent_dir / "preferences.md").read_text(encoding="utf-8") == "old content"

    def test_missing_backup_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.memory.version.BACKUPS_DIR", tmp_path / "backups"
        )
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir(parents=True)
        log = {
            "preferences.md": [
                {"timestamp": "2026-01-01T00:00:00", "backup": "missing.backup", "size": 0}
            ]
        }
        (backups_dir / "version_log.json").write_text(json.dumps(log))

        ok, msg = rollback_prompt("preferences")
        assert not ok
        assert "丢失" in msg
