"""Aide 启动引导 — ~/.aide/ 目录初始化 + 冷启动判断。

每次启动时运行，确保数据目录和基础文件存在。
不创建 session（session 在首条消息发出时延后创建）。

P4: migrate_config() 已移除（config/config.json 已删除）。
旧配置迁移逻辑内联至 ensure_aide_root()。

P5: aide_dir() 公开化，支持 AIDE_HOME 环境变量。
"""

import json
import logging
import os
import shutil
from pathlib import Path
from datetime import datetime, timezone

from core.locale import t, build_soul, set_locale

logger = logging.getLogger(__name__)


# ── 目录结构 ─────────────────────────────────────────────────────────

def aide_dir() -> Path:
    """Aide 数据根目录。

    优先级：AIDE_HOME 环境变量 > ~/.aide
    支持 ~ 展开和相对路径解析。
    """
    env = os.environ.get("AIDE_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".aide"



def _ensure_dirs(aide: Path) -> None:
    """创建所有子目录。"""
    dirs = [
        aide / "agent" / "data",
        aide / "config",
        aide / "sessions",
        aide / "plugins",
        aide / "backups",
        aide / "logs",
        aide / "archives",
        aide / "mcp",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def _install_builtin_plugins(aide: Path) -> None:
    """将内置模板插件安装到 ~/.aide/plugins/（仅首次 — 删除后不自动恢复）。"""
    sentinel = aide / ".plugins_installed"
    if sentinel.exists():
        return

    from core.resources import get_resource_path
    templates_dir = get_resource_path("core/plugins/templates")
    if not templates_dir.exists():
        return

    plugins_dir = aide / "plugins"
    for template_path in templates_dir.iterdir():
        if not template_path.is_dir():
            continue
        # 检查是否为有效插件模板（有 aide.plugin.json 或 SKILL.md）
        if not (template_path / "aide.plugin.json").exists() and \
           not (template_path / "SKILL.md").exists():
            continue
        dest = plugins_dir / template_path.name
        if not dest.exists():
            try:
                shutil.copytree(template_path, dest)
            except OSError:
                logger.debug("Failed to copy plugin template %s, skipping", template_path.name)

    sentinel.write_text("", encoding="utf-8")


def _seed_mcp_config(aide: Path) -> None:
    """将 bundle 中的默认 MCP 配置复制到 ~/.aide/mcp/（仅首次）。"""
    mcp_servers = aide / "mcp" / "servers.json"
    if mcp_servers.exists():
        return

    from core.resources import get_bundle_dir

    bundled_servers = get_bundle_dir() / "mcp" / "servers.json"
    if bundled_servers.exists():
        try:
            shutil.copy2(bundled_servers, mcp_servers)
        except OSError:
            logger.debug("Failed to copy bundled MCP servers.json, skipping")
def _ensure_file(path: Path, content: str) -> bool:
    """如果文件不存在则创建，返回 True 表示新创建。"""
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        return True
    return False


def _ensure_json(path: Path, default) -> bool:
    """如果 JSON 文件不存在则创建，返回 True 表示新创建。"""
    if not path.exists():
        path.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    return False


# ── 公开 API ─────────────────────────────────────────────────────────

def ensure_aide_root() -> Path:
    """确保 ~/.aide/ 目录结构完整。

    每次启动调用，幂等——已存在的文件不会覆盖。
    内含一次性旧配置迁移（~/.aide/agent/config.json → ~/.aide/config/settings.json）。
    支持 AIDE_HOME 环境变量指定自定义数据目录。

    Returns:
        ~/.aide/ 目录路径
    """
    aide = aide_dir()
    _ensure_dirs(aide)
    _install_builtin_plugins(aide)
    _seed_mcp_config(aide)

    agent_dir = aide / "agent"
    data_dir = agent_dir / "data"
    config_dir = aide / "config"

    # ── Soul 和 prompt 文件 ──
    _ensure_file(agent_dir / "soul.md", build_soul("{name}"))
    _ensure_file(agent_dir / "preferences.md", t("tmpl.preferences"))
    _ensure_file(agent_dir / "workflows.md", t("tmpl.workflows"))
    _ensure_file(agent_dir / "long_term_memory.md", t("tmpl.long_term_memory"))

    # ── 条目目录 ──
    _ensure_json(data_dir / "preferences.json", [])
    _ensure_json(data_dir / "workflows.json", [])
    _ensure_json(data_dir / "long_term_memory.json", [])
    _ensure_json(data_dir / "topic_frequency.json", {})

    # ── 默认配置文件 ──
    defaults_path = config_dir / "defaults.json"
    if not defaults_path.exists():
        default_config = {
            "llm": {
                "provider": "",
                "model": "",
                "base_url": "",
                "api_key": "",
                "temperature": 0.7,
                "max_tokens": 4096,
                "supports_vision": False,
            },
            "app": {
                "locale": "zh",
                "max_turns": 5,
                "window_turns": 8,
                "relevance_threshold": 0.15,
                "context_window": 128000,
            },
        }
        defaults_path.write_text(
            json.dumps(default_config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── API 配置目录 ──
    (config_dir / "api").mkdir(parents=True, exist_ok=True)

    # ── 一次性旧配置迁移 ──
    settings_path = config_dir / "settings.json"
    legacy_config = aide / "agent" / "config.json"

    # 旧 agent/config.json → config/settings.json
    if not settings_path.exists() and legacy_config.exists():
        try:
            shutil.copy2(legacy_config, settings_path)
        except OSError:
            logger.debug("Failed to copy legacy config.json to settings.json, skipping")

    # API 配置迁移：将 settings.json 中的 api_keys 迁移到 config/api/*.json
    if settings_path.exists():
        try:
            from core.config import Config
            n = Config.migrate_api_configs()
            if n > 0:
                import logging
                logging.getLogger(__name__).info(
                    "Migrated %d API config(s) to config/api/", n)
        except Exception:
            logger.debug("API config migration failed, skipping")

    # ── settings.json 不存在时创建（确保 locale 等新字段可用）──
    if not settings_path.exists():
        try:
            settings_path.write_text(
                json.dumps({
                    "llm": {
                        "provider": "",
                        "model": "",
                        "base_url": "",
                        "api_key": "",
                        "temperature": 0.7,
                        "max_tokens": 4096,
                        "supports_vision": False,
                    },
                    "app": {
                        "locale": "zh",
                        "max_turns": 5,
                        "window_turns": 8,
                        "relevance_threshold": 0.15,
                        "context_window": 128000,
                    },
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            logger.debug("Failed to create default settings.json, skipping")

    return aide


def is_cold_start(aide: Path | None = None) -> bool:
    """判断是否需要冷启动引导。

    条件：agent/data/ 下三个条目 JSON 文件全为空数组。

    Returns:
        True 表示需要冷启动引导
    """
    if aide is None:
        aide = aide_dir()

    data_dir = aide / "agent" / "data"
    entry_files = ["preferences.json", "workflows.json", "long_term_memory.json"]

    for fname in entry_files:
        path = data_dir / fname
        if not path.exists():
            return True

        try:
            content = path.read_text(encoding="utf-8")
            data = json.loads(content)
            if len(data) > 0:
                return False
        except (json.JSONDecodeError, OSError):
            return True

    return True


def has_existing_config() -> bool:
    """检查是否已有可用的 LLM 配置（跳过冷启动向导的条件）。

    优先检查 API 配置文件，其次检查 settings.json 的 llm 字段。

    Returns:
        True 表示已配置 provider + model，无需向导
    """
    # 1. 检查 API 配置文件
    api_dir = aide_dir() / "config" / "api"
    if api_dir.is_dir():
        for f in api_dir.glob("*.json"):
            try:
                cfg = json.loads(f.read_text(encoding="utf-8"))
                if cfg.get("provider") and cfg.get("model"):
                    return True
            except (json.JSONDecodeError, OSError):
                pass

    # 2. 检查 settings.json 的 llm 字段（兼容旧格式）
    settings_path = aide_dir() / "config" / "settings.json"
    if not settings_path.exists():
        return False
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        llm = data.get("llm", {})
        return bool(llm.get("provider") and llm.get("model"))
    except (json.JSONDecodeError, OSError):
        return False
