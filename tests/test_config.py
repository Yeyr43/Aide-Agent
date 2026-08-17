import json
from pathlib import Path
from core.config import Config, LLMConfig, AppConfig


class TestConfigDefaults:
    def test_default_llm_provider(self):
        config = Config()
        assert config.llm.provider == ""
        assert config.llm.model == ""
        assert config.llm.supports_vision is None

    def test_default_app_settings(self):
        config = Config()
        assert config.app.max_turns == 10
        assert config.app.full_text_turns == 3
        assert config.app.summary_turns == 15

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


class TestConfigLoadPriority:
    """分层加载优先级：cli > env > settings.json > defaults。"""

    def test_settings_overrides_defaults(self, tmp_path):
        """settings.json 覆盖内置 defaults。"""
        aide_root = tmp_path / ".aide"
        config_dir = aide_root / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.json").write_text(
            json.dumps({"llm": {"model": "settings-model"}, "app": {"max_turns": 7}}),
            encoding="utf-8",
        )
        with _patch_aide_root(tmp_path):
            config = Config.load()
        assert config.llm.model == "settings-model"
        assert config.app.max_turns == 7

    def test_env_overrides_settings(self, tmp_path, monkeypatch):
        """环境变量覆盖 settings.json 的 llm。"""
        aide_root = tmp_path / ".aide"
        config_dir = aide_root / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.json").write_text(
            json.dumps({"llm": {"model": "settings-model"}}),
            encoding="utf-8",
        )
        monkeypatch.setenv("AIDE_MODEL", "env-model")
        with _patch_aide_root(tmp_path):
            config = Config.load()
        assert config.llm.model == "env-model"

    def test_cli_overrides_settings_and_env(self, tmp_path, monkeypatch):
        """cli_args 优先级最高。"""
        aide_root = tmp_path / ".aide"
        config_dir = aide_root / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.json").write_text(
            json.dumps({"llm": {"model": "settings-model"}}),
            encoding="utf-8",
        )
        monkeypatch.setenv("AIDE_MODEL", "env-model")
        with _patch_aide_root(tmp_path):
            config = Config.load(cli_args={"model": "cli-model"})
        assert config.llm.model == "cli-model"

    def test_cli_args_all_llm_keys(self, tmp_path):
        """cli_args 覆盖 provider/base_url/api_key/supports_vision。"""
        with _patch_aide_root(tmp_path):
            config = Config.load(cli_args={
                "provider": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-cli",
                "supports_vision": True,
            })
        assert config.llm.provider == "openai"
        assert config.llm.base_url == "https://api.example.com/v1"
        assert config.llm.api_key == "sk-cli"
        assert config.llm.supports_vision is True

    def test_env_overrides_all_llm_fields(self, tmp_path, monkeypatch):
        """AIDE_BASE_URL / AIDE_API_KEY 环境变量映射。"""
        monkeypatch.setenv("AIDE_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("AIDE_API_KEY", "sk-env")
        with _patch_aide_root(tmp_path):
            config = Config.load()
        assert config.llm.base_url == "http://localhost:11434/v1"
        assert config.llm.api_key == "sk-env"


class TestConfigLoadingEdgeCases:
    """settings.json 边界：损坏文件、旧字段、active_api 位置。"""

    def test_corrupt_settings_json_uses_defaults(self, tmp_path):
        """损坏的 settings.json 应被静默跳过，使用默认值。"""
        aide_root = tmp_path / ".aide"
        config_dir = aide_root / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.json").write_text("{not valid json", encoding="utf-8")
        with _patch_aide_root(tmp_path):
            config = Config.load()
        assert config.llm.provider == ""
        assert config.llm.model == ""
        assert config.app.max_turns == 10

    def test_load_settings_corrupt_returns_empty(self, tmp_path):
        """Config.load_settings() 对损坏文件返回空 dict。"""
        config_dir = tmp_path / ".aide" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.json").write_text("{bad json", encoding="utf-8")
        with _patch_aide_root(tmp_path):
            assert Config.load_settings() == {}

    def test_load_settings_missing_returns_empty(self, tmp_path):
        """settings.json 不存在时 load_settings 返回空 dict。"""
        with _patch_aide_root(tmp_path):
            assert Config.load_settings() == {}

    def test_window_turns_deprecated_silently_ignored(self, tmp_path):
        """旧版 window_turns 字段被静默移除，不影响新配置。"""
        aide_root = tmp_path / ".aide"
        config_dir = aide_root / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.json").write_text(
            json.dumps({"app": {"window_turns": 99, "max_turns": 6}}),
            encoding="utf-8",
        )
        with _patch_aide_root(tmp_path):
            config = Config.load()
        assert config.app.max_turns == 6
        assert not hasattr(config.app, "window_turns")

    def test_active_api_in_app_section(self, tmp_path):
        """active_api 放在 app 段内也能读取。"""
        aide_root = tmp_path / ".aide"
        config_dir = aide_root / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.json").write_text(
            json.dumps({"app": {"active_api": "myapi"}}),
            encoding="utf-8",
        )
        with _patch_aide_root(tmp_path):
            config = Config.load()
        assert config.app.active_api == "myapi"


class TestConfigApiFiles:
    """API 配置文件解析：优先于 settings.json 的 llm。"""

    def test_active_api_from_top_level_settings(self, tmp_path):
        """顶层 active_api + API 文件 → llm 从 API 文件解析。"""
        aide_root = tmp_path / ".aide"
        api_dir = aide_root / "config" / "api"
        api_dir.mkdir(parents=True)
        (aide_root / "config" / "settings.json").write_text(
            json.dumps({"active_api": "myapi"}), encoding="utf-8"
        )
        (api_dir / "myapi.json").write_text(
            json.dumps({
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": "sk-test",
                "base_url": "https://api.example.com/v1",
                "supports_vision": False,
                "thinking": True,
            }),
            encoding="utf-8",
        )
        with _patch_aide_root(tmp_path):
            config = Config.load()
        assert config.app.active_api == "myapi"
        assert config.llm.provider == "openai"
        assert config.llm.model == "gpt-4o"
        assert config.llm.api_key == "sk-test"
        assert config.llm.base_url == "https://api.example.com/v1"
        assert config.llm.supports_vision is False
        assert config.llm.thinking is True

    def test_api_config_overrides_settings_llm(self, tmp_path):
        """API 文件覆盖 settings.json 的 llm 字段。"""
        aide_root = tmp_path / ".aide"
        api_dir = aide_root / "config" / "api"
        api_dir.mkdir(parents=True)
        (aide_root / "config" / "settings.json").write_text(
            json.dumps({
                "active_api": "myapi",
                "llm": {"provider": "ollama", "model": "llama3"},
            }),
            encoding="utf-8",
        )
        (api_dir / "myapi.json").write_text(
            json.dumps({"provider": "openai", "model": "gpt-4o"}),
            encoding="utf-8",
        )
        with _patch_aide_root(tmp_path):
            config = Config.load()
        assert config.llm.provider == "openai"
        assert config.llm.model == "gpt-4o"

    def test_api_config_skips_empty_fields(self, tmp_path):
        """API 文件中空字段不覆盖默认值。"""
        aide_root = tmp_path / ".aide"
        api_dir = aide_root / "config" / "api"
        api_dir.mkdir(parents=True)
        (aide_root / "config" / "settings.json").write_text(
            json.dumps({"active_api": "myapi"}), encoding="utf-8"
        )
        (api_dir / "myapi.json").write_text(
            json.dumps({"provider": "", "model": ""}), encoding="utf-8"
        )
        with _patch_aide_root(tmp_path):
            config = Config.load()
        assert config.llm.provider == ""
        assert config.llm.model == ""

    def test_list_api_configs_empty_without_dir(self, tmp_path):
        """API 目录不存在 → 空 dict。"""
        with _patch_aide_root(tmp_path):
            assert Config.list_api_configs() == {}

    def test_list_api_configs_returns_configs(self, tmp_path):
        """列出所有 API 配置。"""
        api_dir = tmp_path / ".aide" / "config" / "api"
        api_dir.mkdir(parents=True)
        (api_dir / "a.json").write_text(json.dumps({"provider": "openai"}), encoding="utf-8")
        (api_dir / "b.json").write_text(json.dumps({"provider": "ollama"}), encoding="utf-8")
        with _patch_aide_root(tmp_path):
            result = Config.list_api_configs()
        assert result == {"a": {"provider": "openai"}, "b": {"provider": "ollama"}}

    def test_list_api_configs_skips_corrupt(self, tmp_path):
        """损坏的 API 配置文件被跳过。"""
        api_dir = tmp_path / ".aide" / "config" / "api"
        api_dir.mkdir(parents=True)
        (api_dir / "a.json").write_text(json.dumps({"provider": "openai"}), encoding="utf-8")
        (api_dir / "bad.json").write_text("{corrupt", encoding="utf-8")
        with _patch_aide_root(tmp_path):
            result = Config.list_api_configs()
        assert result == {"a": {"provider": "openai"}}

    def test_load_api_config_missing_returns_none(self, tmp_path):
        """不存在的 API 配置 → None。"""
        with _patch_aide_root(tmp_path):
            assert Config.load_api_config("nope") is None

    def test_load_api_config_returns_config(self, tmp_path):
        """加载单个 API 配置。"""
        api_dir = tmp_path / ".aide" / "config" / "api"
        api_dir.mkdir(parents=True)
        (api_dir / "a.json").write_text(json.dumps({"provider": "openai"}), encoding="utf-8")
        with _patch_aide_root(tmp_path):
            assert Config.load_api_config("a") == {"provider": "openai"}

    def test_load_api_config_corrupt_returns_none(self, tmp_path):
        """损坏的 API 配置 → None。"""
        api_dir = tmp_path / ".aide" / "config" / "api"
        api_dir.mkdir(parents=True)
        (api_dir / "bad.json").write_text("{corrupt", encoding="utf-8")
        with _patch_aide_root(tmp_path):
            assert Config.load_api_config("bad") is None

    def test_save_api_config_writes_file(self, tmp_path):
        """save_api_config 原子写入 API 配置文件。"""
        with _patch_aide_root(tmp_path):
            Config.save_api_config("myapi", {"provider": "openai", "model": "gpt-4o"})
            saved = json.loads(
                (tmp_path / ".aide" / "config" / "api" / "myapi.json").read_text(encoding="utf-8")
            )
        assert saved == {"provider": "openai", "model": "gpt-4o"}

    def test_delete_api_config_removes_file(self, tmp_path):
        """delete_api_config 删除文件并返回 True。"""
        api_dir = tmp_path / ".aide" / "config" / "api"
        api_dir.mkdir(parents=True)
        (api_dir / "a.json").write_text("{}", encoding="utf-8")
        with _patch_aide_root(tmp_path):
            assert Config.delete_api_config("a") is True
        assert not (api_dir / "a.json").exists()

    def test_delete_api_config_missing_returns_true(self, tmp_path):
        """删除不存在的配置（missing_ok）→ True。"""
        with _patch_aide_root(tmp_path):
            assert Config.delete_api_config("ghost") is True

    def test_delete_api_config_oserror_returns_false(self, tmp_path):
        """unlink 失败 → False。"""
        import unittest.mock
        with _patch_aide_root(tmp_path), unittest.mock.patch(
            "core.config.Path.unlink", side_effect=OSError("boom")
        ):
            assert Config.delete_api_config("a") is False


class TestConfigActiveApi:
    """active_api 读写。"""

    def test_get_active_api_name_from_settings(self, tmp_path):
        """从 settings.json 读取 active_api。"""
        config_dir = tmp_path / ".aide" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.json").write_text(
            json.dumps({"active_api": "myapi"}), encoding="utf-8"
        )
        with _patch_aide_root(tmp_path):
            assert Config.get_active_api_name() == "myapi"

    def test_get_active_api_name_empty(self, tmp_path):
        """无 active_api → 空字符串。"""
        with _patch_aide_root(tmp_path):
            assert Config.get_active_api_name() == ""

    def test_set_active_api_name(self, tmp_path):
        """set_active_api_name 写入 settings.json。"""
        config_dir = tmp_path / ".aide" / "config"
        config_dir.mkdir(parents=True)
        with _patch_aide_root(tmp_path):
            Config.set_active_api_name("myapi")
            settings = Config.load_settings()
        assert settings["active_api"] == "myapi"


class TestConfigMigrate:
    """API 配置迁移（api_keys / llm → config/api/*.json）。"""

    def test_migrate_from_api_keys(self, tmp_path):
        """settings.json 的 api_keys 迁移到独立文件。"""
        config_dir = tmp_path / ".aide" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.json").write_text(
            json.dumps({
                "api_keys": {
                    "k1": {"provider": "openai", "model": "gpt-4o", "api_key": "sk"},
                    "k2": {"provider": "ollama", "model": "llama3"},
                }
            }),
            encoding="utf-8",
        )
        with _patch_aide_root(tmp_path):
            migrated = Config.migrate_api_configs()
            k1 = Config.load_api_config("k1")
            k2 = Config.load_api_config("k2")
        assert migrated == 2
        assert k1["provider"] == "openai"
        assert k1["model"] == "gpt-4o"
        assert k1["api_key"] == "sk"
        assert k1["supports_vision"] is False
        assert k2["model"] == "llama3"

    def test_migrate_skips_existing_files(self, tmp_path):
        """已存在的 API 配置不重复迁移。"""
        api_dir = tmp_path / ".aide" / "config" / "api"
        api_dir.mkdir(parents=True)
        (api_dir / "k1.json").write_text("{}", encoding="utf-8")
        (tmp_path / ".aide" / "config" / "settings.json").write_text(
            json.dumps({
                "api_keys": {
                    "k1": {"provider": "openai"},
                    "k2": {"provider": "ollama", "model": "llama3"},
                }
            }),
            encoding="utf-8",
        )
        with _patch_aide_root(tmp_path):
            assert Config.migrate_api_configs() == 1

    def test_migrate_from_llm_for_active_api(self, tmp_path):
        """llm + active_api 指向不存在文件时从 llm 创建。"""
        config_dir = tmp_path / ".aide" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.json").write_text(
            json.dumps({
                "active_api": "myapi",
                "llm": {"provider": "openai", "model": "gpt-4o", "api_key": "sk"},
            }),
            encoding="utf-8",
        )
        with _patch_aide_root(tmp_path):
            assert Config.migrate_api_configs() == 1
            cfg = Config.load_api_config("myapi")
        assert cfg["provider"] == "openai"
        assert cfg["model"] == "gpt-4o"


class TestConfigProperties:
    """Config 路径属性。"""

    def test_agent_and_backups_dirs(self, tmp_path):
        """agent_dir / backups_dir 基于 aide_root。"""
        with _patch_aide_root(tmp_path):
            config = Config()
        assert config.agent_dir == tmp_path / ".aide" / "agent"
        assert config.backups_dir == tmp_path / ".aide" / "backups"


class TestConfigAideHome:
    """AIDE_HOME 环境变量加载配置。"""

    def test_load_with_aide_home(self, tmp_path, monkeypatch):
        """AIDE_HOME 指向自定义根目录时读取其配置。"""
        home = tmp_path / "custom_aide"
        config_dir = home / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.json").write_text(
            json.dumps({"llm": {"provider": "openai", "model": "gpt-4o"}}),
            encoding="utf-8",
        )
        monkeypatch.setenv("AIDE_HOME", str(home))
        config = Config.load()
        assert config.aide_root == home.resolve()
        assert config.llm.provider == "openai"
        assert config.llm.model == "gpt-4o"
