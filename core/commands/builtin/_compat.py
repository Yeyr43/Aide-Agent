"""向后兼容层 — COMMANDS dict + route_command + _cmd 装饰器。

P4 Batch 2: 从 handlers.py 拆分，保持旧代码兼容。
新代码应使用 CommandRegistry。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.setup import aide_dir

AIDE_ROOT = aide_dir()
AGENT_ROOT = AIDE_ROOT / "agent"

COMMANDS: dict[str, tuple[callable, str]] = {}


def _register_to_commands(name: str, handler: callable, desc: str) -> None:
    """同时注册到模块级 COMMANDS dict（向后兼容）。"""
    COMMANDS[f"/{name}"] = (handler, desc)


def route_command(
    text: str,
    registry: Any | None = None,
) -> tuple[callable, str] | None:
    """解析用户输入，匹配命令（向后兼容包装）。

    委托给 CommandRegistry。接受可选的 registry 参数以复用实例
    （包含插件命令），否则创建新实例（仅内置命令）。

    Args:
        text: 用户输入文本
        registry: 可选的 CommandRegistry 实例。传入现有实例可匹配插件命令。
    """
    if registry is None:
        from core.commands import CommandRegistry
        registry = CommandRegistry()
    result = registry.route(text)
    if result is not None:
        cmd_def, args = result
        return (cmd_def.handler, args)
    return None


def _cmd(name: str, desc: str):
    """装饰器：注册命令到 COMMANDS dict（向后兼容）。"""
    def wrapper(fn):
        _register_to_commands(name, fn, desc)
        return fn
    return wrapper
