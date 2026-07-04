"""Tests for /plugin command handler."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from core.commands.builtin.plugin_commands import handle_plugin
from core.plugins.contract import PluginManifest


class TestHandlePluginDefault:
    """默认行为（无子命令）：自动加载所有发现的插件 + 列出状态。"""

    @pytest.mark.asyncio
    async def test_no_plugins_found(self):
        app = MagicMock()
        app._kernel = MagicMock()
        app._kernel._plugins = MagicMock()
        app._kernel._plugins.discover.return_value = []

        result = await handle_plugin(app, "")
        assert "没有发现可用插件" in result

    @pytest.mark.asyncio
    async def test_already_loaded(self):
        """已加载的插件显示 ✅ 且不会重复加载。"""
        app = MagicMock()
        app._kernel = MagicMock()
        app._kernel._plugins = MagicMock()
        app._kernel._plugins.discover.return_value = [
            PluginManifest(id="loaded", name="Already Loaded", version="1.0", description="A desc"),
        ]
        app._kernel._plugins.is_loaded.return_value = True

        result = await handle_plugin(app, "")
        assert "Already Loaded" in result
        assert "v1.0" in result
        assert "A desc" in result

    @pytest.mark.asyncio
    async def test_auto_load_new(self):
        """未加载的插件自动加载，显示 🆙。"""
        app = MagicMock()
        info = MagicMock()
        info.name = "New Plugin"
        info.manifest = PluginManifest(id="new", name="New Plugin", version="2.0", description="Fresh")
        app._kernel = MagicMock()
        app._kernel._plugins = MagicMock()
        app._kernel._plugins.discover.return_value = [
            PluginManifest(id="new", name="New Plugin", version="2.0", description="Fresh"),
        ]
        app._kernel._plugins.is_loaded.return_value = False
        app._kernel.load_plugin = AsyncMock(return_value=info)

        result = await handle_plugin(app, "")
        assert "New Plugin" in result
        assert "新加载" in result

    @pytest.mark.asyncio
    async def test_auto_load_failure(self):
        """加载失败的插件显示 ❌。"""
        app = MagicMock()
        app._kernel = MagicMock()
        app._kernel._plugins = MagicMock()
        app._kernel._plugins.discover.return_value = [
            PluginManifest(id="bad", name="Bad Plugin", version="0.1"),
        ]
        app._kernel._plugins.is_loaded.return_value = False
        app._kernel.load_plugin = AsyncMock(return_value=None)

        result = await handle_plugin(app, "")
        assert "加载失败" in result

    @pytest.mark.asyncio
    async def test_shows_summary(self):
        """底部显示汇总统计 + 提示。"""
        app = MagicMock()
        info = MagicMock()
        info.name = "P"
        info.manifest = PluginManifest(id="p", name="P", version="1.0")
        app._kernel = MagicMock()
        app._kernel._plugins = MagicMock()
        app._kernel._plugins.discover.return_value = [
            PluginManifest(id="p", name="P", version="1.0"),
        ]
        app._kernel._plugins.is_loaded.return_value = False
        app._kernel.load_plugin = AsyncMock(return_value=info)

        result = await handle_plugin(app, "")
        assert "reload" in result
        assert "unload" in result


class TestHandlePluginLoad:
    @pytest.mark.asyncio
    async def test_load_no_id(self):
        result = await handle_plugin(MagicMock(), "load")
        assert "用法" in result

    @pytest.mark.asyncio
    async def test_load_success(self):
        app = MagicMock()
        info = MagicMock()
        info.name = "loaded-plugin"
        info.manifest = PluginManifest(id="lp", name="Loaded Plugin", version="2.0.0")
        app._kernel = MagicMock()
        app._kernel.load_plugin = AsyncMock(return_value=info)

        result = await handle_plugin(app, "load lp")
        assert "已加载" in result
        assert "loaded-plugin" in result

    @pytest.mark.asyncio
    async def test_load_failure(self):
        app = MagicMock()
        app._kernel = MagicMock()
        app._kernel.load_plugin = AsyncMock(return_value=None)

        result = await handle_plugin(app, "load bad")
        assert "失败" in result


class TestHandlePluginUnload:
    @pytest.mark.asyncio
    async def test_unload_no_id(self):
        result = await handle_plugin(MagicMock(), "unload")
        assert "用法" in result

    @pytest.mark.asyncio
    async def test_unload_success(self):
        app = MagicMock()
        app._kernel = MagicMock()
        app._kernel.unload_plugin = AsyncMock(return_value=True)

        result = await handle_plugin(app, "unload p")
        assert "已卸载" in result

    @pytest.mark.asyncio
    async def test_unload_failure(self):
        app = MagicMock()
        app._kernel = MagicMock()
        app._kernel.unload_plugin = AsyncMock(return_value=False)

        result = await handle_plugin(app, "unload p")
        assert "不存在" in result or "未加载" in result


class TestHandlePluginReload:
    @pytest.mark.asyncio
    async def test_reload_no_id(self):
        result = await handle_plugin(MagicMock(), "reload")
        assert "用法" in result

    @pytest.mark.asyncio
    async def test_reload_success(self):
        app = MagicMock()
        info = MagicMock()
        info.name = "reloaded"
        info.manifest = PluginManifest(id="rp", name="RP", version="1.0.0")
        app._kernel = MagicMock()
        app._kernel._plugins = MagicMock()
        app._kernel._plugins.reload = AsyncMock(return_value=info)

        result = await handle_plugin(app, "reload rp")
        assert "已重载" in result

    @pytest.mark.asyncio
    async def test_reload_failure(self):
        app = MagicMock()
        app._kernel = MagicMock()
        app._kernel._plugins = MagicMock()
        app._kernel._plugins.reload = AsyncMock(return_value=None)

        result = await handle_plugin(app, "reload bad")
        assert "失败" in result


class TestHandlePluginUnknownSub:
    @pytest.mark.asyncio
    async def test_unknown_subcommand(self):
        result = await handle_plugin(MagicMock(), "unknown")
        assert "未知" in result
        assert "load" in result.lower()

    @pytest.mark.asyncio
    async def test_list_no_longer_a_subcommand(self):
        """list 不再是子命令，会落入未知分支。"""
        result = await handle_plugin(MagicMock(), "list")
        assert "未知" in result

    @pytest.mark.asyncio
    async def test_discover_no_longer_a_subcommand(self):
        """discover 不再是子命令，会落入未知分支。"""
        result = await handle_plugin(MagicMock(), "discover")
        assert "未知" in result
