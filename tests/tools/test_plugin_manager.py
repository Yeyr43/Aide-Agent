"""Tests for core.tools.plugin_manager — plugin 管理工具。

覆盖：list / install（目录、zip、路径逃逸、非法插件）/ load / unload / 缺 ctx。
"""

import zipfile
from pathlib import Path

import pytest

from core.tools import plugin_manager
from core.config import Config
from core.commands import CommandRegistry
from core.plugins.host import PluginHost
from core.tools import ToolRegistry
from core.tools.definition import ToolContext


def _make_skill_plugin(root: Path, name: str) -> Path:
    """造一个 OpenClaw skill 插件目录（SKILL.md 文件）。"""
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n# {name}\n", encoding="utf-8")
    return d


@pytest.fixture
def host(tmp_path) -> PluginHost:
    config = Config(aide_root=tmp_path)
    config.plugins_dir.mkdir(parents=True)
    return PluginHost(config, ToolRegistry(), CommandRegistry())


def _ctx(host) -> ToolContext:
    ctx = ToolContext()
    ctx.plugin_host = host
    return ctx


class TestDefinition:
    def test_schema(self):
        d = plugin_manager.definition
        assert d.name == "plugin"
        assert d.parameters["required"] == ["action"]
        assert "install" in d.parameters["properties"]["action"]["enum"]

    def test_execute_is_async(self):
        import inspect
        assert inspect.iscoroutinefunction(plugin_manager.execute)


class TestMissingCtx:
    async def test_no_plugin_host_returns_error(self):
        r = await plugin_manager.execute({"action": "list"}, ctx=None)
        assert "插件系统不可用" in r


class TestList:
    async def test_empty_list(self, host):
        r = await plugin_manager.execute({"action": "list"}, ctx=_ctx(host))
        assert "插件列表" in r
        assert "无已安装插件" in r

    async def test_list_after_install(self, host, tmp_path):
        _make_skill_plugin(tmp_path / "src", "demo-skill")
        await plugin_manager.execute(
            {"action": "install", "path": str(tmp_path / "src" / "demo-skill")}, ctx=_ctx(host))
        r = await plugin_manager.execute({"action": "list"}, ctx=_ctx(host))
        assert "demo-skill" in r
        assert "已加载" in r


class TestInstall:
    async def test_install_directory(self, host, tmp_path):
        src = _make_skill_plugin(tmp_path / "src", "demo-skill")
        r = await plugin_manager.execute(
            {"action": "install", "path": str(src)}, ctx=_ctx(host))
        assert "demo-skill" in r
        assert (host._config.plugins_dir / "demo-skill" / "SKILL.md").exists()
        assert host.is_loaded("demo-skill")

    async def test_install_nested_plugin_root(self, host, tmp_path):
        """外层目录含插件子目录 → 找到子目录安装。"""
        outer = tmp_path / "outer"
        _make_skill_plugin(outer, "nested-skill")
        r = await plugin_manager.execute(
            {"action": "install", "path": str(outer)}, ctx=_ctx(host))
        assert "nested-skill" in r
        assert (host._config.plugins_dir / "nested-skill" / "SKILL.md").exists()

    async def test_install_missing_path(self, host, tmp_path):
        r = await plugin_manager.execute(
            {"action": "install", "path": str(tmp_path / "nope")}, ctx=_ctx(host))
        assert "不存在" in r

    async def test_install_not_a_plugin(self, host, tmp_path):
        d = tmp_path / "random"
        d.mkdir()
        (d / "readme.txt").write_text("hi", encoding="utf-8")
        r = await plugin_manager.execute(
            {"action": "install", "path": str(d)}, ctx=_ctx(host))
        assert "不是有效的插件" in r or "未找到插件" in r

    async def test_install_already_exists(self, host, tmp_path):
        src = _make_skill_plugin(tmp_path / "src", "demo-skill")
        await plugin_manager.execute(
            {"action": "install", "path": str(src)}, ctx=_ctx(host))
        r = await plugin_manager.execute(
            {"action": "install", "path": str(src)}, ctx=_ctx(host))
        assert "已存在" in r

    async def test_install_zip(self, host, tmp_path):
        zip_path = tmp_path / "plugin.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("zipped-skill/SKILL.md",
                        "---\nname: zipped-skill\ndescription: z\n---\n# Z\n")
        r = await plugin_manager.execute(
            {"action": "install", "path": str(zip_path)}, ctx=_ctx(host))
        assert "zipped-skill" in r
        assert host.is_loaded("zipped-skill")

    async def test_install_zip_path_escape_rejected(self, host, tmp_path):
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../escape.txt", "x")
        r = await plugin_manager.execute(
            {"action": "install", "path": str(zip_path)}, ctx=_ctx(host))
        assert "不安全" in r or "错误" in r
        # 未写出到插件目录外
        assert not (tmp_path.parent / "escape.txt").exists()

    async def test_install_requires_path(self, host):
        r = await plugin_manager.execute({"action": "install"}, ctx=_ctx(host))
        assert "path" in r


class TestLoadUnload:
    async def test_load_and_unload(self, host, tmp_path):
        _make_skill_plugin(tmp_path / "src", "demo-skill")
        await plugin_manager.execute(
            {"action": "install", "path": str(tmp_path / "src" / "demo-skill")}, ctx=_ctx(host))
        # unload
        r = await plugin_manager.execute(
            {"action": "unload", "plugin_id": "demo-skill"}, ctx=_ctx(host))
        assert "已卸载" in r
        assert not host.is_loaded("demo-skill")
        # load
        r = await plugin_manager.execute(
            {"action": "load", "plugin_id": "demo-skill"}, ctx=_ctx(host))
        assert "已加载" in r
        assert host.is_loaded("demo-skill")

    async def test_unload_missing_plugin(self, host):
        r = await plugin_manager.execute(
            {"action": "unload", "plugin_id": "nope"}, ctx=_ctx(host))
        assert "错误" in r

    async def test_load_requires_id(self, host):
        r = await plugin_manager.execute({"action": "load"}, ctx=_ctx(host))
        assert "plugin_id" in r

    async def test_unknown_action(self, host):
        r = await plugin_manager.execute({"action": "bogus"}, ctx=_ctx(host))
        assert "未知 action" in r
