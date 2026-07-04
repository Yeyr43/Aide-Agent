import json
import asyncio
import pytest
from pathlib import Path
from core.config import Config
from core.tools import ToolRegistry
from core.commands import CommandRegistry
from core.plugins.host import PluginHost
from core.plugins.slots import SlotRegistry


@pytest.fixture
def host(tmp_path):
    config = Config(aide_root=tmp_path / ".aide")
    config.plugins_dir.mkdir(parents=True)
    tool_reg = ToolRegistry()
    cmd_reg = CommandRegistry()
    return PluginHost(config, tool_reg, cmd_reg)


class TestPluginHost:
    def test_discover_empty_dir(self, host):
        assert host.discover() == []

    def test_discover_finds_manifest(self, host, tmp_path):
        plugin_dir = host._config.plugins_dir / "test-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "aide.plugin.json").write_text(
            json.dumps({"id": "test-plugin"}))
        manifests = host.discover()
        assert len(manifests) == 1
        assert manifests[0].id == "test-plugin"

    def test_discover_skill_md(self, host, tmp_path):
        plugin_dir = host._config.plugins_dir / "skill-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: A test skill\n---\n# Skill\n")
        manifests = host.discover()
        assert len(manifests) == 1
        assert manifests[0].id == "my-skill"
        assert manifests[0].kind == "skill"

    def test_unload_nonexistent(self, host):
        # unload is async — use asyncio.run to await it
        result = asyncio.run(host.unload("nonexistent"))
        assert not result

    def test_slot_registry_default(self, host):
        assert isinstance(host.slot_registry, SlotRegistry)

    def test_slot_registry_injected(self, tmp_path):
        config = Config(aide_root=tmp_path / ".aide")
        config.plugins_dir.mkdir(parents=True)
        sr = SlotRegistry()
        host = PluginHost(config, ToolRegistry(), CommandRegistry(), slot_registry=sr)
        assert host.slot_registry is sr

    def test_list_loaded_empty_initially(self, host):
        assert host.list_loaded() == []

    def test_is_loaded_false_initially(self, host):
        assert not host.is_loaded("any-plugin")

    def test_load_plugin_with_tool(self, host, tmp_path):
        """End-to-end: load a plugin module that registers a tool."""
        plugin_dir = host._config.plugins_dir / "demo"
        plugin_dir.mkdir()

        # manifest
        (plugin_dir / "aide.plugin.json").write_text(json.dumps({
            "id": "demo",
            "name": "Demo Plugin",
            "entry": "main.py",
        }))

        # plugin code
        (plugin_dir / "main.py").write_text(r'''
from core.plugins.sdk import define_plugin
from core.tools import ToolDefinition

@define_plugin("demo")
def register(api):
    api.register_tool(ToolDefinition(
        name="demo_hello",
        description="Say hello",
        parameters={"type": "object", "properties": {}},
    ))
''')

        info = asyncio.run(host.load("demo"))
        assert info is not None
        assert info.id == "demo"
        assert info.loaded is True
        assert host.is_loaded("demo")
        assert "demo_hello" in host._tool_registry.list_names()

        # unload
        result = asyncio.run(host.unload("demo"))
        assert result is True
        assert not host.is_loaded("demo")

    def test_load_missing_manifest(self, host, tmp_path):
        plugin_dir = host._config.plugins_dir / "no-manifest"
        plugin_dir.mkdir()
        info = asyncio.run(host.load("no-manifest"))
        assert info is None

    def test_load_invalid_entry(self, host, tmp_path):
        plugin_dir = host._config.plugins_dir / "bad-entry"
        plugin_dir.mkdir()
        (plugin_dir / "aide.plugin.json").write_text(json.dumps({
            "id": "bad-entry",
            "entry": "missing.py",
        }))
        info = asyncio.run(host.load("bad-entry"))
        assert info is None

    def test_reload_plugin(self, host, tmp_path):
        plugin_dir = host._config.plugins_dir / "reload-test"
        plugin_dir.mkdir()

        (plugin_dir / "aide.plugin.json").write_text(json.dumps({
            "id": "reload-test",
            "entry": "main.py",
        }))

        (plugin_dir / "main.py").write_text(r'''
from core.plugins.sdk import define_plugin
from core.tools import ToolDefinition

@define_plugin("reload-test")
def register(api):
    api.register_tool(ToolDefinition(
        name="reload_tool",
        description="For reload test",
        parameters={"type": "object", "properties": {}},
    ))
''')

        info = asyncio.run(host.load("reload-test"))
        assert info is not None
        assert "reload_tool" in host._tool_registry.list_names()

        # reload
        info2 = asyncio.run(host.reload("reload-test"))
        assert info2 is not None
        assert host.is_loaded("reload-test")

    def test_load_plugin_with_command(self, host, tmp_path):
        """End-to-end: load a plugin that registers a command."""
        plugin_dir = host._config.plugins_dir / "cmd-plugin"
        plugin_dir.mkdir()

        (plugin_dir / "aide.plugin.json").write_text(json.dumps({
            "id": "cmd-plugin",
            "entry": "main.py",
        }))

        (plugin_dir / "main.py").write_text(r'''
from core.plugins.sdk import define_plugin
from core.commands import CommandDefinition

async def my_handler(app, args):
    return "ok"

@define_plugin("cmd-plugin")
def register(api):
    api.register_command(CommandDefinition(
        name="/demo-cmd",
        description="A demo command",
        handler=my_handler,
    ))
''')

        info = asyncio.run(host.load("cmd-plugin"))
        assert info is not None
        assert info.id == "cmd-plugin"
        cmd = host._command_registry.get("//demo-cmd")
        assert cmd is not None
        assert cmd.source == "plugin:cmd-plugin"

        # unload removes the command
        result = asyncio.run(host.unload("cmd-plugin"))
        assert result is True
        assert host._command_registry.get("//demo-cmd") is None
