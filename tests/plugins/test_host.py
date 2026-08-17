import json
import asyncio
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, MagicMock
from core.config import Config
from core.tools import ToolRegistry
from core.commands import CommandRegistry
from core.plugins.host import PluginHost, PluginInfo, ExternalSkillProvider
from core.plugins.contract import PluginManifest
from core.plugins.adapter import ExtractedSkill, ExtractedCommand, ExtractedHook
from core.plugins.state import PluginStatus
from core.plugins.manifest_v2 import PluginManifestV2
from core.plugins.security import PreflightResult, PreflightWarning
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
        # P7: namespace-prefixed command name
        cmd = host._command_registry.get("//cmd-plugin:demo-cmd")
        assert cmd is not None
        assert cmd.source == "plugin:cmd-plugin"

        # unload removes the command
        result = asyncio.run(host.unload("cmd-plugin"))
        assert result is True
        assert host._command_registry.get("//cmd-plugin:demo-cmd") is None


class TestPluginDisabled:
    """回归：DISABLED 插件必须真正不加载/卸载（曾只在注册后改状态，'已禁用'仍可被调用）。"""

    def _make_plugin(self, host, tmp_path, pid="demo") -> None:
        plugin_dir = host._config.plugins_dir / pid
        plugin_dir.mkdir()
        (plugin_dir / "aide.plugin.json").write_text(json.dumps({
            "id": pid, "name": pid, "entry": "main.py",
        }))
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

    def test_disabled_plugin_not_loaded(self, host, tmp_path):
        self._make_plugin(host, tmp_path)
        host.state_manager.disable("demo")
        info = asyncio.run(host.load("demo"))
        assert info is None
        assert not host.is_loaded("demo")
        assert "demo_hello" not in host._tool_registry.list_names()

    def test_disable_plugin_unloads_and_unregisters(self, host, tmp_path):
        self._make_plugin(host, tmp_path)
        info = asyncio.run(host.load("demo"))
        assert info is not None
        assert "demo_hello" in host._tool_registry.list_names()

        asyncio.run(host.disable_plugin("demo"))
        assert not host.is_loaded("demo")
        assert "demo_hello" not in host._tool_registry.list_names()
        assert host.state_manager.get("demo").status.value == "disabled"

    def test_enable_plugin_reloads(self, host, tmp_path):
        self._make_plugin(host, tmp_path)
        asyncio.run(host.disable_plugin("demo"))
        assert not host.is_loaded("demo")

        asyncio.run(host.enable_plugin("demo"))
        assert host.is_loaded("demo")
        assert "demo_hello" in host._tool_registry.list_names()


# ── 补充：未覆盖行针对性测试 ─────────────────────────────────────────


def _write_python_plugin(host, pid, body, entry="main.py"):
    """写一个 Aide 原生 Python 插件目录。body 中 @PID@ 会被替换为 pid。"""
    plugin_dir = host._config.plugins_dir / pid
    plugin_dir.mkdir()
    (plugin_dir / "aide.plugin.json").write_text(json.dumps({
        "id": pid, "name": pid, "entry": entry,
    }))
    (plugin_dir / entry).write_text(body.replace("@PID@", pid))
    return plugin_dir


_BASIC_PLUGIN = '''
from core.plugins.sdk import define_plugin
from core.tools import ToolDefinition

@define_plugin("@PID@")
def register(api):
    api.register_tool(ToolDefinition(
        name="basic_tool",
        description="basic",
        parameters={"type": "object", "properties": {}},
    ))
'''


class _FakeAdapter:
    """Stub 适配器 — 驱动 host.load 的外部技能分支。"""

    FINGERPRINT = "fake"

    def __init__(self, skills=None, commands=None, mcp=None, settings=None,
                 hooks_error=False, skills_error=False, commands_error=False,
                 mcp_error=False, settings_error=False):
        self._skills = skills if skills is not None else [
            ExtractedSkill(name="s", description="d", content="c",
                           references={"ref.md": "R"})]
        self._commands = commands if commands is not None else []
        self._mcp = mcp if mcp is not None else []
        self._settings = settings if settings is not None else {}
        self._hooks_error = hooks_error
        self._skills_error = skills_error
        self._commands_error = commands_error
        self._mcp_error = mcp_error
        self._settings_error = settings_error

    async def extract_hooks(self):
        if self._hooks_error:
            raise RuntimeError("hooks boom")
        return []

    async def extract_skills(self):
        if self._skills_error:
            raise RuntimeError("skills boom")
        return self._skills

    async def extract_commands(self):
        if self._commands_error:
            raise RuntimeError("commands boom")
        return self._commands

    async def extract_mcp_servers(self):
        if self._mcp_error:
            raise RuntimeError("mcp boom")
        return self._mcp

    async def extract_settings(self):
        if self._settings_error:
            raise RuntimeError("settings boom")
        return self._settings


def _force_external_fmt(host, adapter, pid, fmt="claude_code"):
    """让 host 的格式检测返回给定适配器（绕过真实目录扫描）。"""
    host._format_detector.detect = lambda d: (
        fmt, adapter, PluginManifestV2(name=pid, version="1.0"))


def _write_claude_plugin(host, pid="cc"):
    """写一个真实 Claude Code 插件目录（skills/commands/hooks/mcp/settings）。"""
    plugin_dir = host._config.plugins_dir / pid
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(json.dumps({
        "name": pid,
        "version": "1.2.3",
        "description": "A claude code plugin",
        "components": {
            "skills": ["review"],
            "commands": ["check"],
            "hooks": ["hooks/hooks.json"],
        },
    }))
    skill_dir = plugin_dir / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: Review code\n---\n# Review\n\nreview body")
    commands_dir = plugin_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "check.md").write_text(
        "---\ndescription: Check things\n---\n# Check\n\ncheck body")
    hooks_dir = plugin_dir / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(json.dumps({
        "hooks": {
            "PreToolUse": [{"matcher": "Read", "command": "echo hi", "timeout": 30}],
        },
    }))
    (plugin_dir / ".mcp.json").write_text(json.dumps({
        "mcpServers": [{"name": "srv", "command": "python", "args": ["-m", "srv"]}],
    }))
    (plugin_dir / "settings.json").write_text(json.dumps({"apiKey": "x"}))
    return plugin_dir


class TestPluginInfoName:
    def test_name_falls_back_to_id(self):
        info = PluginInfo(manifest=PluginManifest(id="x"))
        assert info.name == "x"

    def test_name_uses_manifest_name(self):
        info = PluginInfo(manifest=PluginManifest(id="x", name="Display"))
        assert info.name == "Display"


class TestExternalSkillProvider:
    def test_init_fields(self):
        p = ExternalSkillProvider("s", "desc", "content", {"a.md": "A"})
        assert p._name == "s"
        assert p._description == "desc"
        assert p._content == "content"
        assert p._references == {"a.md": "A"}

    def test_init_no_references(self):
        p = ExternalSkillProvider("s", "desc", "content")
        assert p._references == {}

    async def test_provide_empty_message(self):
        p = ExternalSkillProvider("s", "desc", "content")
        assert await p.provide("", None) == ""

    async def test_provide_empty_content(self):
        p = ExternalSkillProvider("s", "desc", "")
        assert await p.provide("hello s", None) == ""

    async def test_provide_no_match(self):
        p = ExternalSkillProvider("s", "desc", "content")
        assert await p.provide("unrelated", None) == ""

    async def test_provide_match_with_references(self):
        p = ExternalSkillProvider("my-skill", "desc", "content", {"ref.md": "R"})
        out = await p.provide("use my-skill please", None)
        assert "## 技能: my-skill" in out
        assert "content" in out
        assert "### ref.md" in out
        assert "R" in out

    async def test_provide_match_colon_as_space(self):
        p = ExternalSkillProvider("my:skill", "desc", "content")
        assert "content" in await p.provide("use my skill now", None)


class TestDiscoverEdgeCases:
    def test_discover_missing_dir(self, tmp_path):
        config = Config(aide_root=tmp_path / "x" / ".aide")
        h = PluginHost(config, ToolRegistry(), CommandRegistry())
        assert h.discover() == []

    def test_discover_skips_non_dir(self, host):
        (host._config.plugins_dir / "a-file.txt").write_text("x")
        assert host.discover() == []

    def test_discover_fallback_from_dir(self, host):
        plugin_dir = host._config.plugins_dir / "legacy"
        plugin_dir.mkdir()
        (plugin_dir / "aide.plugin.json").write_text(json.dumps({"id": "legacy"}))
        with patch.object(host._format_detector, "detect", return_value=None):
            manifests = host.discover()
        assert len(manifests) == 1
        assert manifests[0].id == "legacy"


class TestLoadSafetyAndFallback:
    async def test_load_path_escape_rejected(self, host, tmp_path):
        assert await host.load("../escape") is None

    async def test_load_absolute_escape_rejected(self, host, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        assert await host.load(str(outside)) is None

    async def test_load_fallback_from_dir(self, host, tmp_path):
        _write_python_plugin(host, "legacy-load", _BASIC_PLUGIN)
        with patch.object(host._format_detector, "detect", return_value=None):
            info = await host.load("legacy-load")
        assert info is not None
        assert host.is_loaded("legacy-load")

    async def test_preflight_blocked(self, host, tmp_path):
        _write_python_plugin(host, "pb-p", _BASIC_PLUGIN)
        with patch("core.plugins.security.PluginPreflightCheck") as MockCheck:
            MockCheck.return_value.check = AsyncMock(return_value=PreflightResult(
                passed=False, blocked=True,
                warnings=[PreflightWarning(level="error", category="installer",
                                           message="unsafe")]))
            info = await host.load("pb-p")
        assert info is None
        assert not host.is_loaded("pb-p")

    async def test_preflight_warnings_only(self, host, tmp_path):
        _write_python_plugin(host, "pw-p", _BASIC_PLUGIN)
        with patch("core.plugins.security.PluginPreflightCheck") as MockCheck:
            MockCheck.return_value.check = AsyncMock(return_value=PreflightResult(
                passed=False, blocked=False,
                warnings=[PreflightWarning(level="warning", category="url",
                                           message="http://x")]))
            info = await host.load("pw-p")
        assert info is not None  # 警告不阻止加载

    async def test_world_writable_rejected(self, host, tmp_path, monkeypatch):
        _write_python_plugin(host, "ww-p", _BASIC_PLUGIN)
        monkeypatch.setattr("sys.platform", "linux")
        with patch("pathlib.Path.stat", return_value=SimpleNamespace(st_mode=0o777)):
            info = await host.load("ww-p")
        assert info is None

    async def test_stat_oserror_continues(self, host, tmp_path, monkeypatch):
        _write_python_plugin(host, "stat-p", _BASIC_PLUGIN)
        monkeypatch.setattr("sys.platform", "linux")
        # 触发 _load_python_plugin 世界可写检查（host.py:247）的 except OSError 分支。
        # 三个 mock 缺一不可：
        #  - preflight.check：跳过它遍历文件调 is_file()
        #  - Path.exists=True：绕过 240 行入口存在检查（Python 3.13 的 exists 内部
        #    直接 self.stat()，会被 fake_stat 拦截且不吞 OSError）
        #  - Path.stat fake：只对入口文件抛错，命中 247 行
        entry = host._config.plugins_dir / "stat-p" / "main.py"
        real_stat = Path.stat

        def fake_stat(self, *a, **k):
            if self == entry:
                raise OSError("boom")
            return real_stat(self, *a, **k)

        with patch("core.plugins.security.PluginPreflightCheck.check",
                   new=AsyncMock(return_value=PreflightResult(
                       passed=True, warnings=[], blocked=False))), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.stat", fake_stat):
            info = await host.load("stat-p")
        assert info is not None

    async def test_spec_from_file_none(self, host, tmp_path):
        _write_python_plugin(host, "spec-p", _BASIC_PLUGIN)
        with patch("importlib.util.spec_from_file_location", return_value=None):
            info = await host.load("spec-p")
        assert info is None


class TestLoadPythonPluginErrors:
    async def test_module_exec_error(self, host, tmp_path):
        _write_python_plugin(host, "ex-p", 'raise RuntimeError("import boom")\n')
        assert await host.load("ex-p") is None

    async def test_no_register_entry(self, host, tmp_path):
        _write_python_plugin(host, "nr-p", "x = 1\n")
        assert await host.load("nr-p") is None

    async def test_plain_register_fallback(self, host, tmp_path):
        _write_python_plugin(host, "pf-p", '''
from core.tools import ToolDefinition

def register(api):
    api.register_tool(ToolDefinition(
        name="plain_tool",
        description="plain",
        parameters={"type": "object", "properties": {}},
    ))
''')
        info = await host.load("pf-p")
        assert info is not None
        assert "plain_tool" in host._tool_registry.list_names()

    async def test_register_fn_raises(self, host, tmp_path):
        _write_python_plugin(host, "rr-p", '''
from core.plugins.sdk import define_plugin

@define_plugin("rr-p")
def register(api):
    raise RuntimeError("register boom")
''')
        assert await host.load("rr-p") is None


class TestLoadPythonPluginAdvanced:
    async def test_requires_missing(self, host, tmp_path):
        _write_python_plugin(host, "req-p", '''
from core.plugins.sdk import define_plugin

@define_plugin("req-p")
def register(api):
    api.requires(api_keys=["AIDE_PLUGIN_TEST_NEVER_SET_XYZ"])
''')
        info = await host.load("req-p")
        assert info is not None
        entry = host.state_manager.get("req-p")
        assert entry.status == PluginStatus.NEEDS_SETUP
        assert any("AIDE_PLUGIN_TEST_NEVER_SET_XYZ" in k
                   for k in entry.missing_requirements)

    async def test_provide_slot(self, host, tmp_path):
        _write_python_plugin(host, "slot-p", '''
from core.plugins.sdk import define_plugin

@define_plugin("slot-p")
def register(api):
    api.provide_slot("custom-slot")
''')
        info = await host.load("slot-p")
        assert info is not None
        assert host._slot_registry.get("custom-slot") is not None

    async def test_startup_hook_failure_logged(self, host, tmp_path):
        _write_python_plugin(host, "st-p", '''
from core.plugins.sdk import define_plugin

@define_plugin("st-p")
def register(api):
    api.on_startup(lambda: 1 / 0)
''')
        info = await host.load("st-p")
        assert info is not None
        assert host.is_loaded("st-p")

    async def test_shutdown_hook_failure_logged(self, host, tmp_path):
        _write_python_plugin(host, "sh-p", '''
from core.plugins.sdk import define_plugin

@define_plugin("sh-p")
def register(api):
    api.on_shutdown(lambda: 1 / 0)
''')
        assert await host.load("sh-p") is not None
        assert await host.unload("sh-p") is True
        assert not host.is_loaded("sh-p")

    async def test_register_hook_and_get_hooks(self, host, tmp_path):
        _write_python_plugin(host, "hk-p", '''
from core.plugins.sdk import define_plugin

@define_plugin("hk-p")
def register(api):
    api.register_hook("PreToolUse", matcher="Read", command="echo hi")
''')
        await host.load("hk-p")
        hooks = host.get_hooks()
        assert any(isinstance(h, ExtractedHook) and h.command == "echo hi"
                   for h in hooks)

    async def test_count(self, host, tmp_path):
        _write_python_plugin(host, "cnt-p", _BASIC_PLUGIN)
        assert host.count() == 0
        await host.load("cnt-p")
        assert host.count() == 1


class TestGetContextProviders:
    async def test_python_plugin_provider(self, host, tmp_path):
        _write_python_plugin(host, "cp-p", '''
from core.plugins.sdk import define_plugin

class MyProvider:
    async def provide(self, user_msg, session_dir):
        return ""

@define_plugin("cp-p")
def register(api):
    api.register_context_provider(MyProvider())
''')
        await host.load("cp-p")
        providers = host.get_context_providers()
        assert len(providers) == 1
        assert providers[0].__class__.__name__ == "MyProvider"
        assert hasattr(providers[0], "provide")

    async def test_external_skill_provider(self, host, tmp_path):
        _write_claude_plugin(host)
        await host.load("cc")
        providers = host.get_context_providers()
        assert any(getattr(p, "_name", None) == "cc:review" for p in providers)


class TestLoadExternalSkill:
    async def test_load_claude_code_full(self, host, tmp_path):
        _write_claude_plugin(host)
        info = await host.load("cc")
        assert info is not None
        assert info.loaded is True
        assert host.is_loaded("cc")

        # 技能命令 + 提取命令
        skill_cmd = host._command_registry.get("//cc:review")
        assert skill_cmd is not None
        assert "review body" in await skill_cmd.handler(MagicMock(), "args")

        # 提取命令 handler 是同步 lambda
        ext_cmd = host._command_registry.get("//cc:check")
        assert ext_cmd is not None
        assert "check body" in ext_cmd.handler(MagicMock(), "args")

        # 技能工具
        tool = host._tool_registry.get("skill_cc_review")
        assert tool is not None
        assert "review body" in await tool.execute({})

        # ContextProvider + READY 状态 + 提取的 hooks
        assert any(getattr(p, "_name", None) == "cc:review"
                   for p in host.get_context_providers())
        assert host.state_manager.get("cc").status == PluginStatus.READY
        assert any(getattr(h, "command", "") == "echo hi" for h in host.get_hooks())

        # 卸载后技能 provider 清空（含命名空间前缀键）
        assert await host.unload("cc") is True
        assert not host.is_loaded("cc")
        assert all(not k.startswith("cc:") for k in host._skill_providers)

    async def test_load_openclaw_skill(self, host, tmp_path):
        plugin_dir = host._config.plugins_dir / "oc"
        plugin_dir.mkdir()
        (plugin_dir / "SKILL.md").write_text(
            "---\nname: oc-skill\ndescription: OC skill\n---\n# OC\n\noc body")
        info = await host.load("oc")
        assert info is not None
        assert host.is_loaded("oc")
        assert any(getattr(p, "_name", None) == "oc:oc-skill"
                   for p in host.get_context_providers())

    async def test_external_skill_disabled_skips(self, host, tmp_path):
        _write_python_plugin(host, "ext", "")
        host.state_manager.disable("ext")
        _force_external_fmt(host, _FakeAdapter(), "ext")
        assert await host.load("ext") is None
        assert not host.is_loaded("ext")

    async def test_hooks_extraction_error_swallowed(self, host, tmp_path):
        _write_python_plugin(host, "h1", "")
        _force_external_fmt(host, _FakeAdapter(hooks_error=True), "h1")
        info = await host.load("h1")
        assert info is not None  # 提取 hooks 失败不阻塞加载

    async def test_skills_extraction_error(self, host, tmp_path):
        _write_python_plugin(host, "s1", "")
        _force_external_fmt(host, _FakeAdapter(skills_error=True), "s1")
        assert await host.load("s1") is None

    async def test_no_skills(self, host, tmp_path):
        _write_python_plugin(host, "s2", "")
        _force_external_fmt(host, _FakeAdapter(skills=[]), "s2")
        assert await host.load("s2") is None

    async def test_commands_extraction_error_swallowed(self, host, tmp_path):
        _write_python_plugin(host, "c1", "")
        _force_external_fmt(host, _FakeAdapter(commands_error=True), "c1")
        assert await host.load("c1") is not None

    async def test_mcp_extraction_error_swallowed(self, host, tmp_path):
        _write_python_plugin(host, "m1", "")
        _force_external_fmt(host, _FakeAdapter(mcp_error=True), "m1")
        assert await host.load("m1") is not None

    async def test_settings_extraction_error_swallowed(self, host, tmp_path):
        _write_python_plugin(host, "t1", "")
        _force_external_fmt(host, _FakeAdapter(settings_error=True), "t1")
        assert await host.load("t1") is not None

    async def test_fake_adapter_commands_mcp_settings(self, host, tmp_path):
        _write_python_plugin(host, "full", "")
        fake = _FakeAdapter(
            skills=[ExtractedSkill(name="s", description="d", content="c")],
            commands=[ExtractedCommand(name="check", description="desc", content="body")],
            mcp=[{"name": "srv", "command": "python", "args": []}],
            settings={"apiKey": "x"},
        )
        _force_external_fmt(host, fake, "full")
        info = await host.load("full")
        assert info is not None
        # 提取命令 handler 是同步 lambda
        cmd = host._command_registry.get("//full:check")
        assert cmd is not None
        assert "body" in cmd.handler(MagicMock(), "a")
        # 技能命令 + 工具
        assert host._command_registry.get("//full:s") is not None
        tool = host._tool_registry.get("skill_full_s")
        assert tool is not None
        assert "c" in await tool.execute({})


class TestLoadSkillNameVsDir:
    """回归：SKILL.md 的 name frontmatter 与目录名不一致时，
    load(discover 返回的 id) 必须仍能定位真实目录并加载。
    OpenClaw 插件常见：目录 openclaw-agent-browser 但 name=agent-browser。
    """

    def _write_skill(self, host, dirname, name):
        plugin_dir = host._config.plugins_dir / dirname
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: A test skill\n---\n# Skill\n",
            encoding="utf-8")
        return plugin_dir

    async def test_load_by_skill_name(self, host):
        """目录名 x-agent-browser，SKILL.md name=agent-browser → load('agent-browser') 成功。"""
        plugin_dir = self._write_skill(host, "x-agent-browser", "agent-browser")
        manifests = host.discover()
        assert [m.id for m in manifests if m.kind == "skill"] == ["agent-browser"]
        assert manifests[0].root_dir == plugin_dir

        info = await host.load("agent-browser")
        assert info is not None
        assert info.manifest.root_dir == plugin_dir
        assert info.loaded is True

    async def test_load_by_directory_name_still_works(self, host):
        """目录名兜底：load('x-agent-browser') 也成功（旧行为兼容）。"""
        self._write_skill(host, "x-agent-browser", "agent-browser")
        info = await host.load("x-agent-browser")
        assert info is not None

    async def test_load_unmatched_id_returns_none(self, host):
        """既不是 discover id 也不是目录名 → 返回 None。"""
        self._write_skill(host, "x-agent-browser", "agent-browser")
        info = await host.load("no-such-plugin")
        assert info is None
