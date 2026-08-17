"""工具自动发现 — 内置工具 + 插件工具统一注册。"""

from core.tools import ToolRegistry
from core.tools import (
    read_file, write_file, run_shell, search_memory, web, search_in_files,
    search_chat, delegate, plugin_manager,
)


# 声明式清单：每个工具模块自包含 definition（name/description/schema/execute），
# 这里只负责收集注册。新增工具 = 新模块里加 definition + 在此清单加一行。
BUILTIN_TOOLS = (
    read_file.definition,
    write_file.definition,
    run_shell.definition,
    search_memory.definition,
    web.definition,
    search_in_files.definition,
    search_chat.definition,
    delegate.definition,
    plugin_manager.definition,
)


def register_builtin_tools(registry: ToolRegistry) -> int:
    """注册所有内置工具（声明式清单）。共 8 个工具。"""
    for tool_def in BUILTIN_TOOLS:
        registry.register(tool_def)
    return len(BUILTIN_TOOLS)


def register_plugin_tools(registry: ToolRegistry, plugin_host) -> int:
    """注册插件提供的工具。

    插件工具在 PluginHost.load() 中自动注册；
    此函数用于手动触发（如插件热重载后重新注册）。
    """
    registered = 0
    for skill_id, skill in getattr(plugin_host, '_skills', {}).items():
        if hasattr(skill, 'tools'):
            for tool_def in skill.tools:
                registry.register(tool_def)
                registered += 1
    return registered
