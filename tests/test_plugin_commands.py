"""Tests for /plugins command handler（原 /plugin 与 /plugins 合并）。"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from core.commands.builtin.plugin_commands import handle_plugins, handle_plugin_call
from core.plugins.contract import PluginManifest
from core.plugins.state import PluginStateEntry, PluginStatus
from core.tools import ToolRegistry, ToolDefinition


def _app_with_plugins(manifests, loaded_ids=(), load_plugin=None,
                      entries=None, counts=None):
    """构造 mock app：discover 返回 manifests，list_loaded 返回 loaded_ids。"""
    app = MagicMock()
    app.kernel = MagicMock()
    app.kernel._plugins = MagicMock()
    app.kernel._plugins.discover.return_value = manifests
    app.kernel._plugins.list_loaded.return_value = [
        PluginManifest(id=i) for i in loaded_ids
    ]
    state_mgr = MagicMock()
    state_mgr.list_all.return_value = entries if entries is not None else []
    state_mgr.count_by_status.return_value = counts or {
        "ready": 0, "needs_setup": 0, "disabled": 0}
    app.kernel._plugins.state_manager = state_mgr
    if load_plugin is not None:
        app.kernel.load_plugin = load_plugin
    return app


class TestPluginsDefault:
    """无子命令：加载所有发现插件 + 列出状态面板。"""

    @pytest.mark.asyncio
    async def test_no_plugins_found(self):
        app = _app_with_plugins([])
        result = await handle_plugins(app, "")
        assert "无已安装插件" in result

    @pytest.mark.asyncio
    async def test_already_loaded_skipped(self):
        """已加载的插件不重复加载，直接显示面板。"""
        app = _app_with_plugins(
            [PluginManifest(id="loaded", name="Already Loaded", version="1.0")],
            loaded_ids=["loaded"],
            load_plugin=AsyncMock(),
        )
        result = await handle_plugins(app, "")
        assert "插件状态" in result
        app.kernel.load_plugin.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_load_new(self):
        """未加载的插件自动加载，面板显示新加载。"""
        info = MagicMock()
        info.name = "New Plugin"
        info.manifest = PluginManifest(id="new", name="New Plugin", version="2.0")
        app = _app_with_plugins(
            [PluginManifest(id="new", name="New Plugin", version="2.0")],
            loaded_ids=[],
            load_plugin=AsyncMock(return_value=info),
        )
        result = await handle_plugins(app, "")
        assert "新加载" in result
        assert "new" in result

    @pytest.mark.asyncio
    async def test_auto_load_failure(self):
        """加载失败显示失败列表。"""
        app = _app_with_plugins(
            [PluginManifest(id="bad", name="Bad Plugin", version="0.1")],
            loaded_ids=[],
            load_plugin=AsyncMock(return_value=None),
        )
        result = await handle_plugins(app, "")
        assert "加载失败" in result
        assert "bad" in result

    @pytest.mark.asyncio
    async def test_load_exception_swallowed(self):
        """load 抛异常 → 计入失败，不中断面板。"""
        app = _app_with_plugins(
            [PluginManifest(id="boom", version="1.0")],
            loaded_ids=[],
            load_plugin=AsyncMock(side_effect=RuntimeError("boom")),
        )
        result = await handle_plugins(app, "")
        assert "加载失败" in result

    @pytest.mark.asyncio
    async def test_usage_hint(self):
        app = _app_with_plugins(
            [PluginManifest(id="p", name="P", version="1.0")],
            loaded_ids=[],
            load_plugin=AsyncMock(return_value=MagicMock()),
        )
        result = await handle_plugins(app, "")
        assert "load" in result
        assert "unload" in result
        assert "reload" in result


class TestPluginsPanel:
    """状态面板（三态）。"""

    @pytest.mark.asyncio
    async def test_panel_shows_entries_and_counts(self):
        entries = [
            PluginStateEntry(plugin_id="ready-p", version="1.2.0",
                             status=PluginStatus.READY),
            PluginStateEntry(plugin_id="setup-p", status=PluginStatus.NEEDS_SETUP,
                             missing_requirements=["api_key:FOO"]),
            PluginStateEntry(plugin_id="dis-p", version="0.1",
                             status=PluginStatus.DISABLED),
        ]
        app = _app_with_plugins(
            [PluginManifest(id="new-p", name="New", version="3.0")],
            loaded_ids=[],
            load_plugin=AsyncMock(return_value=MagicMock()),
            entries=entries,
            counts={"ready": 1, "needs_setup": 1, "disabled": 1},
        )
        result = await handle_plugins(app, "")
        assert "Ready: **1**" in result
        assert "Needs Setup: **1**" in result
        assert "Disabled: **1**" in result
        assert "ready-p" in result
        assert "v1.2.0" in result
        assert "缺少: `api_key:FOO`" in result
        assert "dis-p" in result
        assert "已禁用" in result
        app.kernel.load_plugin.assert_awaited_once_with("new-p")


class TestPluginsSubcommands:
    """load / unload / reload。"""

    @pytest.mark.asyncio
    async def test_load_no_id(self):
        result = await handle_plugins(MagicMock(), "load")
        assert "用法" in result

    @pytest.mark.asyncio
    async def test_load_success(self):
        info = MagicMock()
        info.name = "loaded-plugin"
        info.manifest = PluginManifest(id="lp", name="Loaded Plugin", version="2.0.0")
        app = MagicMock()
        app.kernel = MagicMock()
        app.kernel.load_plugin = AsyncMock(return_value=info)
        result = await handle_plugins(app, "load lp")
        assert "已加载" in result
        assert "loaded-plugin" in result

    @pytest.mark.asyncio
    async def test_load_failure(self):
        app = MagicMock()
        app.kernel = MagicMock()
        app.kernel.load_plugin = AsyncMock(return_value=None)
        result = await handle_plugins(app, "load bad")
        assert "失败" in result

    @pytest.mark.asyncio
    async def test_unload_no_id(self):
        result = await handle_plugins(MagicMock(), "unload")
        assert "用法" in result

    @pytest.mark.asyncio
    async def test_unload_success(self):
        app = MagicMock()
        app.kernel = MagicMock()
        app.kernel.unload_plugin = AsyncMock(return_value=True)
        result = await handle_plugins(app, "unload p")
        assert "已卸载" in result

    @pytest.mark.asyncio
    async def test_unload_failure(self):
        app = MagicMock()
        app.kernel = MagicMock()
        app.kernel.unload_plugin = AsyncMock(return_value=False)
        result = await handle_plugins(app, "unload p")
        assert "不存在" in result or "未加载" in result

    @pytest.mark.asyncio
    async def test_reload_no_id(self):
        result = await handle_plugins(MagicMock(), "reload")
        assert "用法" in result

    @pytest.mark.asyncio
    async def test_reload_success(self):
        info = MagicMock()
        info.name = "reloaded"
        info.manifest = PluginManifest(id="rp", name="RP", version="1.0.0")
        app = MagicMock()
        app.kernel = MagicMock()
        app.kernel._plugins = MagicMock()
        app.kernel._plugins.reload = AsyncMock(return_value=info)
        result = await handle_plugins(app, "reload rp")
        assert "已重载" in result

    @pytest.mark.asyncio
    async def test_reload_failure(self):
        app = MagicMock()
        app.kernel = MagicMock()
        app.kernel._plugins = MagicMock()
        app.kernel._plugins.reload = AsyncMock(return_value=None)
        result = await handle_plugins(app, "reload bad")
        assert "失败" in result


class TestPluginsEnableDisable:
    @pytest.mark.asyncio
    async def test_enable_missing_id(self):
        result = await handle_plugins(MagicMock(), "enable")
        assert "enable" in result

    @pytest.mark.asyncio
    async def test_enable_success(self):
        app = MagicMock()
        app.kernel = MagicMock()
        app.kernel._plugins = MagicMock()
        app.kernel._plugins.enable_plugin = AsyncMock()
        result = await handle_plugins(app, "enable foo")
        app.kernel._plugins.enable_plugin.assert_awaited_once_with("foo")
        assert "已启用" in result

    @pytest.mark.asyncio
    async def test_disable_missing_id(self):
        result = await handle_plugins(MagicMock(), "disable")
        assert "disable" in result

    @pytest.mark.asyncio
    async def test_disable_success(self):
        app = MagicMock()
        app.kernel = MagicMock()
        app.kernel._plugins = MagicMock()
        app.kernel._plugins.disable_plugin = AsyncMock()
        result = await handle_plugins(app, "disable foo")
        app.kernel._plugins.disable_plugin.assert_awaited_once_with("foo")
        assert "已禁用" in result


class TestPluginsUnknownArg:
    """非子命令参数（如 list/discover）→ 按无参数处理（加载 + 面板）。"""

    @pytest.mark.asyncio
    async def test_list_treated_as_refresh(self):
        app = _app_with_plugins([])
        result = await handle_plugins(app, "list")
        assert "无已安装插件" in result

    @pytest.mark.asyncio
    async def test_discover_treated_as_refresh(self):
        app = _app_with_plugins([])
        result = await handle_plugins(app, "discover")
        assert "无已安装插件" in result


# ── //plugin 统一调用器 ─────────────────────────────────────────────────


def _plugin_call_app(plugins=None, registry=None):
    """构造 mock app：host 返回插件信息，tool_registry 执行真实工具。

    plugins: {plugin_id: [ToolDefinition, ...]} 或 None
    """
    host = MagicMock()
    host._plugins = MagicMock()
    info_by_id = {}
    infos = []
    for pid, tools in (plugins or {}).items():
        info = MagicMock()
        info.id = pid
        info.api._tools = tools
        info_by_id[pid] = info
        infos.append(info)
    host._plugins.get.side_effect = lambda pid: info_by_id.get(pid)
    host.list_loaded.return_value = infos

    if registry is None:
        registry = ToolRegistry()
        for tools in (plugins or {}).values():
            for tool in tools:
                registry.register(tool)

    app = MagicMock()
    app.kernel._plugins = host
    app.kernel.tool_registry = registry
    return app


class TestPluginCall:
    async def _greet_tool(self):
        async def greet(args):
            return f"Hello, {args.get('name', 'World')}!"
        return ToolDefinition(
            name="greet", description="Greet someone",
            parameters={"type": "object", "properties": {
                "name": {"type": "string", "description": "名字"},
            }, "required": ["name"]},
            execute=greet,
        )

    @pytest.mark.asyncio
    async def test_no_args_lists_all(self):
        tool = await self._greet_tool()
        app = _plugin_call_app({"demo": [tool]})
        result = await handle_plugin_call(app, "")
        assert "demo" in result
        assert "greet" in result

    @pytest.mark.asyncio
    async def test_no_loaded_plugins(self):
        app = _plugin_call_app({})
        result = await handle_plugin_call(app, "")
        assert "暂无" in result

    @pytest.mark.asyncio
    async def test_plugin_only_lists_tools(self):
        tool = await self._greet_tool()
        app = _plugin_call_app({"demo": [tool]})
        result = await handle_plugin_call(app, "demo")
        assert "greet" in result

    @pytest.mark.asyncio
    async def test_unknown_plugin(self):
        app = _plugin_call_app({})
        result = await handle_plugin_call(app, "nope")
        assert "未加载" in result

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        tool = await self._greet_tool()
        app = _plugin_call_app({"demo": [tool]})
        result = await handle_plugin_call(app, "demo nope")
        assert "没有工具" in result

    @pytest.mark.asyncio
    async def test_invoke_tool_kv(self):
        tool = await self._greet_tool()
        app = _plugin_call_app({"demo": [tool]})
        result = await handle_plugin_call(app, "demo greet name=Claude")
        assert result == "Hello, Claude!"

    @pytest.mark.asyncio
    async def test_invoke_tool_json(self):
        tool = await self._greet_tool()
        app = _plugin_call_app({"demo": [tool]})
        result = await handle_plugin_call(app, 'demo greet {"name": "JSON"}')
        assert result == "Hello, JSON!"

    @pytest.mark.asyncio
    async def test_missing_required_shows_usage(self):
        tool = await self._greet_tool()
        app = _plugin_call_app({"demo": [tool]})
        result = await handle_plugin_call(app, "demo greet")
        assert "用法" in result
        assert "name" in result
