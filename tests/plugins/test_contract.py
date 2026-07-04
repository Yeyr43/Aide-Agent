import json
from pathlib import Path
from core.plugins.contract import PluginManifest, PluginAPI, PluginSlot


class TestPluginManifest:
    def test_from_dir_aide_manifest(self, tmp_path):
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()
        manifest = {
            "id": "my-plugin",
            "name": "My Plugin",
            "version": "1.0.0",
            "description": "Test plugin",
            "kind": "tool",
        }
        (plugin_dir / "aide.plugin.json").write_text(json.dumps(manifest))

        m = PluginManifest.from_dir(plugin_dir)
        assert m is not None
        assert m.id == "my-plugin"
        assert m.name == "My Plugin"
        assert m.kind == "tool"

    def test_from_dir_skill_md(self, tmp_path):
        plugin_dir = tmp_path / "skill-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "SKILL.md").write_text(
            "---\nname: pptx\ndescription: Create presentations\n---\n"
            "# PPTX Skill\n\nContent here.\n"
        )

        m = PluginManifest.from_dir(plugin_dir)
        assert m is not None
        assert m.id == "pptx"
        assert m.name == "pptx"
        assert m.kind == "skill"
        assert m.entry == "SKILL.md"

    def test_from_dir_no_manifest(self, tmp_path):
        plugin_dir = tmp_path / "empty"
        plugin_dir.mkdir()
        assert PluginManifest.from_dir(plugin_dir) is None

    def test_aide_manifest_over_skill_md(self, tmp_path):
        plugin_dir = tmp_path / "dual"
        plugin_dir.mkdir()
        (plugin_dir / "aide.plugin.json").write_text(json.dumps({"id": "aide-python", "kind": "tool"}))
        (plugin_dir / "SKILL.md").write_text(
            "---\nname: skill-version\ndescription: Skill version\n---\n# Skill\n"
        )

        m = PluginManifest.from_dir(plugin_dir)
        assert m is not None
        assert m.id == "aide-python"  # aide.plugin.json 优先于 SKILL.md


class TestPluginAPI:
    def test_register_tool(self):
        from core.tools import ToolDefinition
        api = PluginAPI("test")
        tool = ToolDefinition(name="test_tool", description="test", parameters={})
        api.register_tool(tool)
        assert api._tools == [tool]

    def test_register_command_sets_source(self):
        from core.commands import CommandDefinition
        api = PluginAPI("my-plugin")
        cmd = CommandDefinition(name="/test", description="test",
                               handler=lambda _: None)  # noqa
        api.register_command(cmd)
        assert cmd.source == "plugin:my-plugin"
        assert cmd.name == "//test"  # auto-normalized to // prefix

    def test_startup_shutdown_hooks(self):
        api = PluginAPI("test")
        called = []
        api.on_startup(lambda: called.append("start"))
        api.on_shutdown(lambda: called.append("stop"))
        api._startup_hooks[0]()
        api._shutdown_hooks[0]()
        assert called == ["start", "stop"]
