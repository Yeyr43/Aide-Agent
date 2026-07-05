"""测试 AIDE_HOME 环境变量和 aide_dir() 统一入口。"""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch

from core.setup import aide_dir, has_existing_config
from core.config import Config


class TestAideDir:
    """测试 aide_dir() 环境变量支持。"""

    def test_default_returns_home_aide(self):
        """未设置 AIDE_HOME 时返回 ~/.aide。"""
        with patch.dict(os.environ, {"AIDE_HOME": ""}):
            result = aide_dir()
            assert result == Path.home() / ".aide"

    def test_env_var_takes_priority(self, tmp_path):
        """AIDE_HOME 环境变量覆盖默认路径。"""
        with patch.dict(os.environ, {"AIDE_HOME": str(tmp_path)}):
            result = aide_dir()
            assert result == tmp_path.resolve()

    def test_env_var_expands_tilde(self):
        """AIDE_HOME 中的 ~ 应被展开。"""
        with patch.dict(os.environ, {"AIDE_HOME": "~/my_aide_data"}):
            result = aide_dir()
            assert result == Path.home() / "my_aide_data"

    def test_env_var_resolves_relative(self, tmp_path, monkeypatch):
        """相对路径应被解析为绝对路径。"""
        monkeypatch.chdir(tmp_path)
        with patch.dict(os.environ, {"AIDE_HOME": "relative_data"}):
            result = aide_dir()
            assert result.is_absolute()

    def test_env_var_empty_string_falls_back(self):
        """空字符串 AIDE_HOME 回退到默认。"""
        with patch.dict(os.environ, {"AIDE_HOME": ""}):
            result = aide_dir()
            assert result == Path.home() / ".aide"


class TestHasExistingConfig:
    """测试 has_existing_config()。"""

    def test_no_settings_file(self, tmp_path):
        """settings.json 不存在 → False。"""
        with patch('core.setup.aide_dir', return_value=tmp_path):
            assert has_existing_config() is False

    def test_empty_settings(self, tmp_path):
        """settings.json 存在但 llm 为空 → False。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.json").write_text(
            json.dumps({"llm": {"provider": "", "model": ""}}), encoding="utf-8"
        )
        with patch('core.setup.aide_dir', return_value=tmp_path):
            assert has_existing_config() is False

    def test_valid_config(self, tmp_path):
        """settings.json 有 provider + model → True。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.json").write_text(
            json.dumps({"llm": {"provider": "openai", "model": "gpt-4o"}}), encoding="utf-8"
        )
        with patch('core.setup.aide_dir', return_value=tmp_path):
            assert has_existing_config() is True

    def test_only_provider_no_model(self, tmp_path):
        """只有 provider 没有 model → False。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.json").write_text(
            json.dumps({"llm": {"provider": "openai", "model": ""}}), encoding="utf-8"
        )
        with patch('core.setup.aide_dir', return_value=tmp_path):
            assert has_existing_config() is False

    def test_corrupt_json(self, tmp_path):
        """损坏的 JSON → False。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.json").write_text("not valid json", encoding="utf-8")
        with patch('core.setup.aide_dir', return_value=tmp_path):
            assert has_existing_config() is False


class TestConfigAideHome:
    """测试 Config 类与 AIDE_HOME 的集成。

    Config 模块通过 from core.setup import aide_dir 导入，
    所以需要 patch core.config.aide_dir 而非 core.setup.aide_dir。
    """

    def test_config_uses_aide_dir(self, tmp_path):
        """Config.load() 的 aide_root 应使用 aide_dir()。"""
        with patch('core.config.aide_dir', return_value=tmp_path):
            config = Config.load()
            assert config.aide_root == tmp_path

    def test_settings_path_uses_aide_dir(self, tmp_path):
        """Config.settings_path() 应使用 aide_dir()。"""
        with patch('core.config.aide_dir', return_value=tmp_path):
            sp = Config.settings_path()
            assert sp == tmp_path / "config" / "settings.json"
