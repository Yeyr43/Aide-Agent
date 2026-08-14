from core.tools import ToolRegistry
from core.tools.discovery import register_builtin_tools, register_plugin_tools


class TestDiscovery:
    def test_register_builtin_tools_adds_eight(self):
        registry = ToolRegistry()
        count = register_builtin_tools(registry)
        assert count == 8
        names = registry.list_names()
        assert "read_file" in names
        assert "write_file" in names
        assert "run_shell" in names
        assert "search_memory" in names
        assert "web" in names
        assert "search_in_files" in names
        assert "search_chat" in names
        assert "delegate" in names

    def test_register_plugin_tools_noop(self):
        registry = ToolRegistry()
        count = register_plugin_tools(registry, None)
        assert count == 0
