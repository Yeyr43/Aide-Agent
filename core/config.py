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
                if "llm" in user_settings:
                    llm_data.update(user_settings["llm"])
                if "app" in user_settings:
                    app_data.update(user_settings["app"])
                # active_api 可能在 app 中或顶层
                if "active_api" in user_settings:
                    app_data.setdefault("active_api", user_settings["active_api"])
            except (json.JSONDecodeError, OSError):
                pass

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
