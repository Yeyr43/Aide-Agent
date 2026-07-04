"""命令系统 — CommandRegistry + CommandDefinition + 路由。

P4: 命令核心从 ui/textual_app/commands/ 移至 core/commands/。
"""

from dataclasses import dataclass, field
from typing import Callable, Awaitable, Any

Handler = Callable[..., Awaitable[str]]


@dataclass
class CommandDefinition:
    name: str                # "/help"
    description: str         # "显示所有可用命令"
    handler: Handler         # async (app, args) -> str
    source: str = "builtin"  # "builtin" | "plugin:<id>"
    kind: str = "default"    # "default" | "maintenance" | "confirm"


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
