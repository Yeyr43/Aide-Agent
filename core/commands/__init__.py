"""命令系统 — CommandRegistry + CommandDefinition + 路由。

P4: 命令核心从 ui/textual_app/commands/ 移至 core/commands/。
"""

from dataclasses import dataclass
from typing import Callable, Awaitable

Handler = Callable[..., Awaitable[str]]


@dataclass
class CommandDefinition:
    name: str                # "/help"
    description: str         # "显示所有可用命令"
    handler: Handler | None = None  # 处理器（kind 非 "default" 时可为 None）
    source: str = "builtin"  # "builtin" | "plugin:<id>"
    kind: str = "default"    # "default" | "maintenance" | "confirm" | "think"


class CommandRegistry:
    """指令注册中心。"""

    def __init__(self) -> None:
        self._commands: dict[str, CommandDefinition] = {}
        self._init_builtin()

    def _init_builtin(self) -> None:
        """加载内置命令。"""
        from core.commands.builtin.handlers import register_builtin_commands
        register_builtin_commands(self)

    def register(self, cmd: CommandDefinition) -> None:
        self._commands[cmd.name] = cmd

    def unregister(self, name: str) -> bool:
        return self._commands.pop(name, None) is not None

    def unregister_source(self, source: str) -> int:
        removed = 0
        for name in list(self._commands):
            if self._commands[name].source == source:
                self._commands.pop(name)
                removed += 1
        return removed

    def get(self, name: str) -> CommandDefinition | None:
        return self._commands.get(name)

    def list_all(self) -> list[CommandDefinition]:
        return sorted(self._commands.values(), key=lambda c: c.name)

    def route(self, text: str) -> tuple[CommandDefinition, str] | None:
        """解析用户输入，匹配命令。返回 (CommandDefinition, args)。"""
        text = text.strip()
        if not text.startswith("/") or text in ("/", "//"):
            return None

        for cmd in sorted(self._commands, key=len, reverse=True):
            if text == cmd or text.startswith(cmd + " "):
                args = text[len(cmd):].strip()
                return (self._commands[cmd], args)

        # 前缀匹配
        for cmd in sorted(self._commands, key=len, reverse=True):
            if cmd.startswith(text):
                args = text[len(cmd):].strip()
                return (self._commands[cmd], args)

        # 模糊匹配
        user_cmd = text.split()[0]
        for cmd in sorted(self._commands, key=len, reverse=True):
            common = sum(1 for c1, c2 in zip(user_cmd, cmd) if c1 == c2)
            if common >= len(cmd) * 0.5:
                remaining = text[len(user_cmd):].strip()
                return (self._commands[cmd], remaining)

        return None


# ── 便捷函数 ──────────────────────────────────────────────────────────


def route_command(
    text: str,
    registry: CommandRegistry | None = None,
) -> tuple[Handler, str] | None:
    """解析用户输入，匹配命令（便捷函数，复用 CommandRegistry）。

    Args:
        text: 用户输入文本
        registry: 可选的 CommandRegistry 实例。传入现有实例可匹配插件命令；
                  为 None 时创建新实例（仅内置命令）。

    Returns:
        (handler, args) 或 None
    """
    if registry is None:
        registry = CommandRegistry()
    result = registry.route(text)
    if result is not None:
        cmd_def, args = result
        return (cmd_def.handler, args)
    return None
