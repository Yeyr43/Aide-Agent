"""测试 setup — 目录初始化、冷启动判断。"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from core.setup import ensure_aide_root, is_cold_start
from core.locale import t, build_soul, set_locale


class TestEnsureAideRoot:
    """测试目录初始化（幂等）。"""

    def test_creates_directory_tree(self, tmp_path):
        """P5: 首次调用应创建完整目录树（不含 data/ 和 archives/）。"""
        with patch('core.setup.aide_dir', return_value=tmp_path):
            result = ensure_aide_root()
            assert result == tmp_path
            assert (tmp_path / "agent").is_dir()
            assert (tmp_path / "sessions").is_dir()
            assert (tmp_path / "plugins").is_dir()
            assert (tmp_path / "logs").is_dir()
            # P5: data/ 和 archives/ 已移除
            assert not (tmp_path / "agent" / "data").is_dir()
            assert not (tmp_path / "archives").is_dir()

    def test_creates_soul_template(self, tmp_path):
        """应创建 soul.md 模板文件。"""
        set_locale("zh")
        with patch('core.setup.aide_dir', return_value=tmp_path):
            ensure_aide_root()
            soul = tmp_path / "agent" / "soul.md"
            assert soul.exists()
            content = soul.read_text(encoding="utf-8")
            assert "Aide" in content
            assert "本地助手" in content

    def test_creates_prompt_files(self, tmp_path):
        """应创建三个空的 prompt 文件。"""
        with patch('core.setup.aide_dir', return_value=tmp_path):
            ensure_aide_root()
            for fname in ["preferences.md", "workflows.md", "long_term_memory.md"]:
                path = tmp_path / "agent" / fname
                assert path.exists()

    def test_creates_data_json_files(self, tmp_path):
        """P5: 条目 JSON 文件不再创建（改用 .md 文件）。"""
        with patch('core.setup.aide_dir', return_value=tmp_path):
            ensure_aide_root()
            # P5: data/ 目录不存在
            data_dir = tmp_path / "agent" / "data"
            assert not data_dir.exists()
            # P5: .md 文件存在
            agent_dir = tmp_path / "agent"
            assert (agent_dir / "preferences.md").read_text(encoding="utf-8")

    def test_idempotent(self, tmp_path):
        """重复调用不应报错，不覆盖已有文件。"""
        with patch('core.setup.aide_dir', return_value=tmp_path):
            ensure_aide_root()
            # 修改 soul.md
            soul = tmp_path / "agent" / "soul.md"
            soul.write_text("custom soul", encoding="utf-8")
            # 再次调用
            ensure_aide_root()
            assert soul.read_text(encoding="utf-8") == "custom soul"

    def test_legacy_config_migration(self, tmp_path):
        """旧 config.json 存在时自动迁移到 settings.json。"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir(parents=True)
        (tmp_path / "config").mkdir()
        legacy = agent_dir / "config.json"
        legacy.write_text('{"llm":{"provider":"ollama"}}', encoding="utf-8")

        with patch('core.setup.aide_dir', return_value=tmp_path):
            ensure_aide_root()
            settings = tmp_path / "config" / "settings.json"
            assert settings.exists()
            data = json.loads(settings.read_text(encoding="utf-8"))
            assert data["llm"]["provider"] == "ollama"


class TestIsColdStart:
    """测试冷启动判断（P5: 基于 soul.md + settings.json）。"""

    def test_no_soul_is_cold_start(self, tmp_path):
        """soul.md 不存在 → 冷启动。"""
        import json
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        settings = {"llm": {"provider": "openai", "model": "gpt-4o"}}
        (config_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

        with patch('core.setup.aide_dir', return_value=tmp_path):
            assert is_cold_start() is True

    def test_no_settings_is_cold_start(self, tmp_path):
        """settings.json 不存在 → 冷启动。"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "soul.md").write_text("# Test Soul", encoding="utf-8")

        with patch('core.setup.aide_dir', return_value=tmp_path):
            assert is_cold_start() is True

    def test_no_provider_is_cold_start(self, tmp_path):
        """settings.json 无 provider → 冷启动。"""
        import json
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "soul.md").write_text("# Test Soul", encoding="utf-8")
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.json").write_text('{"app": {"locale": "zh"}}', encoding="utf-8")

        with patch('core.setup.aide_dir', return_value=tmp_path):
            assert is_cold_start() is True

    def test_fully_configured_not_cold_start(self, tmp_path):
        """soul.md + 有效 settings.json → 非冷启动。"""
        import json
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "soul.md").write_text("# Test Soul", encoding="utf-8")
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        settings = {"llm": {"provider": "openai", "model": "gpt-4o"}}
        (config_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

        with patch('core.setup.aide_dir', return_value=tmp_path):
            assert is_cold_start() is False

    def test_missing_files_is_cold_start(self, tmp_path):
        """条目文件不存在 → 冷启动。"""
        data_dir = tmp_path / "agent" / "data"
        data_dir.mkdir(parents=True)

        with patch('core.setup.aide_dir', return_value=tmp_path):
            assert is_cold_start() is True
