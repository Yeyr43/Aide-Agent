"""Plugin contract — manifest model, PluginAPI, PluginSlot, ContextProvider."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from core.tools import ToolDefinition
from core.commands import CommandDefinition


@dataclass
class PluginManifest:
    """插件 manifest — Aide 原生字段。

    支持两种格式（优先级降序）：
      1. aide.plugin.json — Aide 原生 JSON manifest（Python 插件）
      2. SKILL.md — Claude Code skill 格式（YAML frontmatter + Markdown body）
    """

    id: str
    name: str = ""
    version: str = "0.1.0"
    description: str = ""
    kind: str = "composite"          # "tool" | "command" | "provider" | "composite" | "skill"
    entry: str = "__init__.py"       # Python 入口模块（skill 类型为 "SKILL.md"）
    config_schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    requires: dict = field(default_factory=dict)   # {"aide": ">=0.4.0"}
    slots: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    root_dir: Path = field(default_factory=Path)    # 插件根目录

    @classmethod
    def from_dir(cls, plugin_dir: Path) -> "PluginManifest | None":
        """从目录加载 manifest（优先级：aide.plugin.json > SKILL.md）。"""
        # 1. Aide JSON manifest
        for fname in ["aide.plugin.json"]:
            path = plugin_dir / fname
            if path.exists():
                import json
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    return cls(
                        id=data.get("id", plugin_dir.name),
                        name=data.get("name", data.get("id", plugin_dir.name)),
                        version=data.get("version", "0.1.0"),
                        description=data.get("description", ""),
                        kind=data.get("kind", "composite"),
                        entry=data.get("entry", "__init__.py"),
                        config_schema=data.get("configSchema", {"type": "object", "properties": {}}),
                        requires=data.get("requires", {}),
                        slots=data.get("slots", []),
                        provides=data.get("provides", []),
                        root_dir=plugin_dir,
                    )
                except (json.JSONDecodeError, OSError):
                    return None

        # 2. Claude Code skill 格式 (SKILL.md)
        skill_md = plugin_dir / "SKILL.md"
        if skill_md.exists():
            try:
                metadata = cls._parse_skill_frontmatter(skill_md)
                if metadata:
                    return cls(
                        id=metadata.get("id") or metadata.get("name", plugin_dir.name),
                        name=metadata.get("name", plugin_dir.name),
                        version=metadata.get("version", "1.0.0"),
                        description=metadata.get("description", ""),
                        kind="skill",
                        entry="SKILL.md",
                        root_dir=plugin_dir,
                    )
            except (OSError, ValueError):
                pass

        return None

    @staticmethod
    def _parse_skill_frontmatter(skill_md: Path) -> dict | None:
        """解析 SKILL.md 的 YAML frontmatter（仅提取 name/description）。

        委托给 core.memory.entries._parse_simple_frontmatter，统一解析逻辑。
        """
        from core.memory.entries import _parse_simple_frontmatter
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            return None
        meta, _ = _parse_simple_frontmatter(text)
        if "name" not in meta:
            return None
        return {k: meta[k] for k in ("name", "description", "version", "id") if k in meta}


@runtime_checkable
class ContextProvider(Protocol):
    """上下文提供者 Protocol — 插件可动态注入 system prompt。"""

    async def provide(self, user_msg: str, session_dir: Path | None) -> str:
        """返回要注入 system prompt 的文本。"""
        ...


@dataclass
class PluginSlot:
    """扩展点定义 — 一个命名的能力槽位。"""
    name: str
    description: str = ""
    filled_by: str | None = None  # plugin_id
    implementation: object = None


class PluginAPI:
    """插件运行时 API — 在 register(api) 中暴露给插件。

    P7: 新增 register_hook / requires / require_api_key / on_status_change。
    """

    def __init__(self, plugin_id: str) -> None:
        self._plugin_id = plugin_id
        self._tools: list[ToolDefinition] = []
        self._commands: list[CommandDefinition] = []
        self._context_providers: list[ContextProvider] = []
        self._filled_slots: list[str] = []
        self._provided_slots: list[str] = []
        self._startup_hooks: list[Callable] = []
        self._shutdown_hooks: list[Callable] = []
        self._hooks: list[dict] = []           # P7: 插件注册的 hook 定义
        self._requirements: dict = {}          # P7: api_keys / system_packages
        self._status_callbacks: list[Callable] = []  # P7: 状态变更回调

    def register_tool(self, tool: ToolDefinition) -> None:
        self._tools.append(tool)

    def register_command(self, cmd: CommandDefinition) -> None:
        cmd.source = f"plugin:{self._plugin_id}"
        # 命名空间隔离：插件命令统一用 //plugin-name:cmd 格式
        if not cmd.name.startswith("//"):
            ns_name = f"//{self._plugin_id}:{cmd.name.lstrip('/')}"
            cmd.name = ns_name
        self._commands.append(cmd)

    def register_context_provider(self, provider: ContextProvider) -> None:
        self._context_providers.append(provider)

    def register_hook(self, event: str, matcher: str = "*",
                      command: str = "", timeout: int = 60) -> None:
        """P7: 注册一个生命周期 hook。"""
        self._hooks.append({
            "event": event,
            "matcher": matcher,
            "type": "command",
            "command": command,
            "timeout": timeout,
        })

    def requires(self, *, api_keys: list[str] | None = None,
                 system_packages: list[str] | None = None,
                 python_packages: list[str] | None = None) -> None:
        """P7: 声明插件依赖。"""
        self._requirements = {
            "api_keys": api_keys or [],
            "system_packages": system_packages or [],
            "python_packages": python_packages or [],
        }

    def on_status_change(self, callback: Callable[[str, str], None]) -> None:
        """P7: 注册状态变更回调。callback(old_status, new_status)。"""
        self._status_callbacks.append(callback)

    def provide_slot(self, slot_name: str) -> None:
        self._provided_slots.append(slot_name)

    def on_startup(self, callback: Callable[[], None]) -> None:
        self._startup_hooks.append(callback)

    def on_shutdown(self, callback: Callable[[], None]) -> None:
        self._shutdown_hooks.append(callback)
