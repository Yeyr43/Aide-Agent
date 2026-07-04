"""工具自动发现 — 内置工具 + 插件工具统一注册。"""

from core.tools import ToolRegistry, ToolDefinition
from core.tools.builtin import (
    read_file, write_file, run_shell, search_memory, web_search,
    list_dir, clipboard, web_fetch, search_in_files, edit_file,
)
from core.locale import t


def register_builtin_tools(registry: ToolRegistry) -> int:
    """注册所有内置工具。共 10 个工具。"""
    tools = [
        ("read_file", t("tool_desc.read_file"), read_file.schema, read_file.execute),
        ("write_file", t("tool_desc.write_file"), write_file.schema, write_file.execute),
        ("run_shell", t("tool_desc.run_shell"), run_shell.schema, run_shell.execute),
        ("search_memory", t("tool_desc.search_memory"), search_memory.schema, search_memory.execute),
        ("web_search", t("tool_desc.web_search"), web_search.schema, web_search.execute),
        ("list_dir", t("tool_desc.list_dir"), list_dir.schema, list_dir.execute),
        ("clipboard", t("tool_desc.clipboard"), clipboard.schema, clipboard.execute),
        ("web_fetch", t("tool_desc.web_fetch"), web_fetch.schema, web_fetch.execute),
        ("search_in_files", t("tool_desc.search_in_files"), search_in_files.schema, search_in_files.execute),
        ("edit_file", t("tool_desc.edit_file"), edit_file.schema, edit_file.execute),
    ]
    for name, desc, params, exe in tools:
        registry.register(ToolDefinition(name=name, description=desc, parameters=params, execute=exe))
    return len(tools)


def register_plugin_tools(registry: ToolRegistry, plugin_host) -> int:
    """插件工具已在 PluginHost.load() 中注册。此函数为预留 API。"""
    return 0
