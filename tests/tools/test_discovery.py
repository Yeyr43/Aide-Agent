from core.tools import ToolRegistry
from core.tools.definition import ToolDefinition
from core.tools.discovery import (
    BUILTIN_TOOLS, register_builtin_tools, register_plugin_tools,
)


class _FakeSkill:
    """带 tools 列表的假技能。"""

    def __init__(self, tools):
        self.tools = tools


class _FakeSkillNoTools:
    """没有 tools 属性的假技能。"""


class _FakePluginHost:
    """带 _skills dict 的假 plugin_host。"""

    def __init__(self, skills):
        self._skills = skills


def _make_definition(name: str) -> ToolDefinition:
    return ToolDefinition(name=name, description=name, parameters={}, execute=None)


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

    def test_builtin_tools_definitions_are_named(self):
        """BUILTIN_TOOLS 是 8 个含 name 的 ToolDefinition。"""
        assert len(BUILTIN_TOOLS) == 8
        assert all(isinstance(t, ToolDefinition) for t in BUILTIN_TOOLS)
        assert all(t.name for t in BUILTIN_TOOLS)
        names = [t.name for t in BUILTIN_TOOLS]
        assert "delegate" in names

    def test_register_plugin_tools_noop(self):
        registry = ToolRegistry()
        count = register_plugin_tools(registry, None)
        assert count == 0

    def test_register_plugin_tools_registers_skills(self):
        """有 tools 的技能被注册，返回注册数量。"""
        registry = ToolRegistry()
        defs = [
            _make_definition("plugin_skill_a"),
            _make_definition("plugin_skill_b"),
        ]
        host = _FakePluginHost({
            "skill_a": _FakeSkill([defs[0]]),
            "skill_b": _FakeSkill([defs[1]]),
        })
        count = register_plugin_tools(registry, host)
        assert count == 2
        assert "plugin_skill_a" in registry.list_names()
        assert "plugin_skill_b" in registry.list_names()

    def test_register_plugin_tools_skips_skills_without_tools(self):
        """无 tools 属性的技能被跳过，不中断其余注册。"""
        registry = ToolRegistry()
        host = _FakePluginHost({
            "with_tools": _FakeSkill([_make_definition("plugin_skill_c")]),
            "no_tools": _FakeSkillNoTools(),
        })
        count = register_plugin_tools(registry, host)
        assert count == 1
        assert "plugin_skill_c" in registry.list_names()

    def test_register_plugin_tools_empty_skills(self):
        """空 _skills → 返回 0。"""
        registry = ToolRegistry()
        count = register_plugin_tools(registry, _FakePluginHost({}))
        assert count == 0
        assert registry.list_names() == []
