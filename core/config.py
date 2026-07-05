"""Aide 配置 — dataclass + 分层加载。

优先级: cli_args > 环境变量 (AIDE_*) > ~/.aide/config/settings.json > defaults.json
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from core.setup import aide_dir


@dataclass
class LLMConfig:
    provider: str = ""
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    supports_vision: bool = False  # 手动在 settings.json 中配置


@dataclass
class AppConfig:
    locale: str = "zh"            # UI 语言：zh / en
    active_api: str = ""          # 当前 API 配置名
    max_turns: int = 5
    window_turns: int = 8
    relevance_threshold: float = 0.15
    context_window: int = 128000  # token 窗口大小，0 表示不限制（状态栏仅显示 token 数）


DEFAULT_LLM = {
    "provider": "",
    "model": "",
    "base_url": "",
    "api_key": "",
    "temperature": 0.7,
    "max_tokens": 4096,
    "supports_vision": False,
}

DEFAULT_APP = {
    "locale": "zh",
    "max_turns": 5,
    "window_turns": 8,
    "relevance_threshold": 0.15,
    "context_window": 128000,
}


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    app: AppConfig = field(default_factory=AppConfig)
    aide_root: Path = field(default_factory=aide_dir)

    @classmethod
    def load(cls, cli_args: dict | None = None) -> "Config":
        """分层加载配置。

        Args:
            cli_args: 命令行参数 dict，如 {"model": "gpt-4o"}

        Returns:
            Config 实例
        """
        aide_root = aide_dir()

        # 1. defaults.json（内置默认值）
        llm_data: dict = dict(DEFAULT_LLM)
        app_data: dict = dict(DEFAULT_APP)

        # 2. ~/.aide/config/settings.json
        settings_path = aide_root / "config" / "settings.json"
        if settings_path.exists():
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    user_settings = json.load(f)
                if "app" in user_settings:
                    app_data.update(user_settings["app"])
                # active_api 可能在 app 中或顶层（优先 app）
                if "active_api" in user_settings:
                    app_data.setdefault("active_api", user_settings["active_api"])
                # llm 字段作为默认值（API 文件优先）
                if "llm" in user_settings:
                    llm_data.update(user_settings["llm"])
            except (json.JSONDecodeError, OSError):
                pass

        # 2.5 从 API 配置文件解析 LLM（优先于 settings.json 的 llm 字段）
        active_api_name = app_data.get("active_api", "")
        if active_api_name:
            api_cfg = Config.load_api_config(active_api_name)
            if api_cfg:
                # API 文件中的字段覆盖 settings.json 的 llm
                for key in ("provider", "model", "api_key", "base_url", "supports_vision"):
                    if key in api_cfg and api_cfg[key]:
                        llm_data[key] = api_cfg[key]

        # 3. 环境变量 (AIDE_*)
        env_map = {
            "AIDE_PROVIDER": ("llm", "provider"),
            "AIDE_MODEL": ("llm", "model"),
            "AIDE_BASE_URL": ("llm", "base_url"),
            "AIDE_API_KEY": ("llm", "api_key"),
        }
        for env_var, (section, key) in env_map.items():
            val = os.environ.get(env_var, "")
            if val:
                if section == "llm":
                    llm_data[key] = val

        # 4. 命令行参数
        cli_args = cli_args or {}
        llm_cli_keys = {"provider", "model", "base_url", "api_key", "supports_vision"}
        for key in llm_cli_keys:
            if key in cli_args:
                llm_data[key] = cli_args[key]

        return cls(
            llm=LLMConfig(**llm_data),
            app=AppConfig(**app_data),
            aide_root=aide_root,
        )

    # ── settings.json 持久化 ──

    @staticmethod
    def settings_path() -> Path:
        """返回 settings.json 的路径。"""
        return aide_dir() / "config" / "settings.json"

    @staticmethod
    def load_settings() -> dict:
        """读取完整 settings.json 为 dict，不存在时返回空 dict。"""
        sp = Config.settings_path()
        if sp.exists():
            try:
                return json.loads(sp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    @staticmethod
    def save_settings(settings: dict) -> None:
        """原子写入 settings.json。"""
        sp = Config.settings_path()
        sp.parent.mkdir(parents=True, exist_ok=True)
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=sp.parent, suffix=".json")
        try:
            with open(fd, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
            os.replace(tmp, sp)
        finally:
            try:
                Path(tmp).unlink(missing_ok=True)
            except OSError:
                pass

    # ── API 配置文件（每个 API 一个文件：config/api/<name>.json）──

    @staticmethod
    def api_dir() -> Path:
        """返回 API 配置目录路径。"""
        return aide_dir() / "config" / "api"

    @staticmethod
    def list_api_configs() -> dict[str, dict]:
        """列出所有 API 配置，返回 {name: config_dict}。"""
        result: dict[str, dict] = {}
        api_d = Config.api_dir()
        if not api_d.is_dir():
            return result
        for f in sorted(api_d.glob("*.json")):
            try:
                cfg = json.loads(f.read_text(encoding="utf-8"))
                result[f.stem] = cfg
            except (json.JSONDecodeError, OSError):
                pass
        return result

    @staticmethod
    def load_api_config(name: str) -> dict | None:
        """加载单个 API 配置，不存在返回 None。"""
        f = Config.api_dir() / f"{name}.json"
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def save_api_config(name: str, config: dict) -> None:
        """保存单个 API 配置文件（原子写入）。"""
        import tempfile
        api_d = Config.api_dir()
        api_d.mkdir(parents=True, exist_ok=True)
        target = api_d / f"{name}.json"
        fd, tmp = tempfile.mkstemp(dir=api_d, suffix=".json")
        try:
            with open(fd, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            os.replace(tmp, target)
        finally:
            try:
                Path(tmp).unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def delete_api_config(name: str) -> bool:
        """删除单个 API 配置文件，返回是否成功。"""
        f = Config.api_dir() / f"{name}.json"
        try:
            f.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    @staticmethod
    def get_active_api_name() -> str:
        """获取当前活跃 API 名称。"""
        settings = Config.load_settings()
        return settings.get("active_api", "") or ""

    @staticmethod
    def set_active_api_name(name: str) -> None:
        """设置当前活跃 API 名称。"""
        settings = Config.load_settings()
        settings["active_api"] = name
        Config.save_settings(settings)

    @staticmethod
    def api_config_exists(name: str) -> bool:
        """检查 API 配置名是否已存在。"""
        return (Config.api_dir() / f"{name}.json").exists()

    @staticmethod
    def migrate_api_configs() -> int:
        """将 settings.json 中的 api_keys / llm 迁移到 config/api/<name>.json。

        非破坏性：旧数据保留在 settings.json 中。

        Returns:
            迁移的 API 数量。
        """
        settings = Config.load_settings()
        migrated = 0

        # 迁移 api_keys
        api_keys: dict = settings.get("api_keys", {})
        for name, cfg in api_keys.items():
            if not Config.api_config_exists(name):
                # 确保有序
                ordered = {
                    "provider": cfg.get("provider", ""),
                    "model": cfg.get("model", ""),
                    "api_key": cfg.get("api_key", ""),
                    "base_url": cfg.get("base_url", ""),
                    "supports_vision": cfg.get("supports_vision", False),
                }
                Config.save_api_config(name, ordered)
                migrated += 1

        # 如果 settings.json 有 llm 配置但没有 api_keys，
        # 且 active_api 指向一个不存在的文件，则从 llm 创建
        llm = settings.get("llm", {})
        active = settings.get("active_api", "")
        if active and llm.get("provider") and llm.get("model"):
            if not Config.api_config_exists(active):
                ordered = {
                    "provider": llm.get("provider", ""),
                    "model": llm.get("model", ""),
                    "api_key": llm.get("api_key", ""),
                    "base_url": llm.get("base_url", ""),
                    "supports_vision": llm.get("supports_vision", False),
                }
                Config.save_api_config(active, ordered)
                migrated += 1

        return migrated

    @property
    def sessions_root(self) -> Path:
        return self.aide_root / "sessions"

    @property
    def plugins_dir(self) -> Path:
        return self.aide_root / "plugins"

    @property
    def agent_dir(self) -> Path:
        return self.aide_root / "agent"

    @property
    def backups_dir(self) -> Path:
        return self.aide_root / "backups"

    @property
    def logs_dir(self) -> Path:
        return self.aide_root / "logs"
