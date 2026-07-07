"""工具自动发现 — 内置工具 + 插件工具统一注册。"""

from core.tools import ToolRegistry, ToolDefinition
from core.tools.builtin import (
    read_file, write_file, run_shell, search_memory, web, search_in_files,
    search_chat,
)
from core.locale import t


def register_builtin_tools(registry: ToolRegistry) -> int:
    """注册所有内置工具。共 7 个工具。"""
    tools = [
        ("read_file", t("tool_desc.read_file"), read_file.schema, read_file.execute),
        ("write_file", t("tool_desc.write_file"), write_file.schema, write_file.execute),
        ("run_shell", t("tool_desc.run_shell"), run_shell.schema, run_shell.execute),
        ("search_memory", t("tool_desc.search_memory"), search_memory.schema, search_memory.execute),
        ("web", t("tool_desc.web"), web.schema, web.execute),
        ("search_in_files", t("tool_desc.search_in_files"), search_in_files.schema, search_in_files.execute),
        ("search_chat", t("tool_desc.search_chat"), search_chat.schema, search_chat.execute),
    ]
    for name, desc, params, exe in tools:
        registry.register(ToolDefinition(name=name, description=desc, parameters=params, execute=exe))
    return len(tools)


def register_plugin_tools(registry: ToolRegistry, plugin_host) -> int:
    """预留 API — 允许显式触发插件工具注册。

    当前插件工具在 PluginHost.load() 中自动注册，此函数返回 0。
    未来若支持延迟加载或独立插件工具注册流程，可在此实现。
    """
    return 0
