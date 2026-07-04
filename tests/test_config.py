import json
from pathlib import Path
from core.config import Config, LLMConfig, AppConfig


class TestConfigDefaults:
    def test_default_llm_provider(self):
        config = Config()
        assert config.llm.provider == ""
        assert config.llm.model == ""
        assert config.llm.supports_vision is False

    def test_default_app_settings(self):
        config = Config()
        assert config.app.max_turns == 5
        assert config.app.window_turns == 8

    def test_default_aide_root(self):
        config = Config()
        assert config.aide_root == Path.home() / ".aide"

    def test_default_properties(self):
        config = Config()
        assert config.sessions_root == Path.home() / ".aide" / "sessions"
        assert config.plugins_dir == Path.home() / ".aide" / "plugins"


class TestConfigLoad:
    def test_load_from_settings_json(self, tmp_path):
        aide_root = tmp_path / ".aide"
        config_dir = aide_root / "config"
        config_dir.mkdir(parents=True)
        settings = {
            "llm": {"provider": "ollama", "model": "llama3"},
            "app": {"max_turns": 10},
        }
        (config_dir / "settings.json").write_text(
            json.dumps(settings), encoding="utf-8"
        )

        with _patch_aide_root(tmp_path):
            config = Config.load()
        assert config.llm.provider == "ollama"
        assert config.llm.model == "llama3"
        assert config.app.max_turns == 10

    def test_env_override(self, tmp_path, monkeypatch):
        aide_root = tmp_path / ".aide"
        (aide_root / "config").mkdir(parents=True)

        monkeypatch.setenv("AIDE_MODEL", "gpt-4o")
        monkeypatch.setenv("AIDE_PROVIDER", "openai")

        with _patch_aide_root(tmp_path):
            config = Config.load()
        assert config.llm.model == "gpt-4o"

    def test_cli_override_takes_highest_priority(self, tmp_path, monkeypatch):
        aide_root = tmp_path / ".aide"
        (aide_root / "config").mkdir(parents=True)

        monkeypatch.setenv("AIDE_MODEL", "env-model")

        with _patch_aide_root(tmp_path):
            config = Config.load(cli_args={"model": "cli-model"})
        assert config.llm.model == "cli-model"


def _patch_aide_root(path: Path):
    """Context manager: 临时替换 Path.home() 使 aide_root 指向 tmp_path/.aide。"""
    import contextlib
    import unittest.mock
    return unittest.mock.patch(
        "core.config.Path.home", return_value=path
    )
