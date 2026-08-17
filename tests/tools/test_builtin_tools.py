"""测试内置工具 — search_in_files 列表模式 + ToolRegistry 注册/执行。"""

import pytest
from pathlib import Path
from unittest.mock import patch

from core.tools import search_in_files
from core.tools import ToolRegistry
from core.tools.definition import ToolDefinition
from core.tools.retry import RetryConfig
from core.plugins.hook_runner import HookResult


def _tool_def(name: str, execute=None):
    return ToolDefinition(
        name=name,
        description=f"desc {name}",
        parameters={"type": "object", "properties": {}},
        execute=execute,
    )


class TestToolRegistry:
    """ToolRegistry — 注册/查询/schema/执行/重试/hook。"""

    def test_register_and_list(self):
        reg = ToolRegistry()
        reg.register(_tool_def("a"))
        reg.register(_tool_def("b"))
        assert set(reg.list_names()) == {"a", "b"}
        assert reg.get("a").name == "a"
        assert reg.get("missing") is None

    def test_unregister(self):
        reg = ToolRegistry()
        reg.register(_tool_def("a"))
        assert reg.unregister("a") is True
        assert reg.unregister("a") is False  # 已不存在 → False
        assert reg.get("a") is None

    def test_get_schemas_format(self):
        reg = ToolRegistry()
        reg.register(_tool_def("a"))
        schemas = reg.get_schemas()
        assert schemas[0]["type"] == "function"
        assert schemas[0]["function"]["name"] == "a"
        assert schemas[0]["function"]["description"] == "desc a"

    async def test_execute_unknown_tool(self):
        reg = ToolRegistry()
        result = await reg.execute("nope", {})
        assert "未找到" in result or "not found" in result.lower()

    async def test_execute_no_execute_function(self):
        reg = ToolRegistry()
        reg.register(_tool_def("a"))  # execute=None
        result = await reg.execute("a", {})
        assert "无可执行体" in result or "no executor" in result.lower()

    async def test_execute_injects_ctx(self):
        reg = ToolRegistry()
        async def _impl(arguments, ctx=None):
            return f"injected:{ctx is not None}"
        reg.register(_tool_def("a", execute=_impl))
        assert await reg.execute("a", {}) == "injected:True"

    async def test_execute_legacy_signature(self):
        reg = ToolRegistry()
        async def _impl(arguments):
            return "legacy"
        reg.register(_tool_def("a", execute=_impl))
        assert await reg.execute("a", {}) == "legacy"

    async def test_pre_tool_hook_blocks(self):
        reg = ToolRegistry()
        async def _impl(arguments):
            return "ran"
        reg.register(_tool_def("a", execute=_impl))

        class _Blocking:
            async def run(self, event, ctx):
                return [HookResult(exit_code=2, stderr="blocked by policy")]

        reg.hook_runner = _Blocking()
        result = await reg.execute("a", {})
        assert "阻止" in result or "blocked" in result.lower()
        assert result != "ran"

    async def test_pre_tool_hook_allows_and_post_fires(self):
        reg = ToolRegistry()
        async def _impl(arguments):
            return "ran"
        reg.register(_tool_def("a", execute=_impl))

        class _Hooks:
            def __init__(self):
                self.events = []
            async def run(self, event, ctx):
                self.events.append(event)
                return [HookResult(exit_code=0)]

        hooks = _Hooks()
        reg.hook_runner = hooks
        assert await reg.execute("a", {}) == "ran"
        assert "PreToolUse" in hooks.events
        assert "PostToolUse" in hooks.events

    async def test_hook_runner_exception_falls_back(self):
        reg = ToolRegistry()
        async def _impl(arguments):
            return "ran"
        reg.register(_tool_def("a", execute=_impl))

        class _Raising:
            async def run(self, event, ctx):
                raise RuntimeError("boom")

        reg.hook_runner = _Raising()
        assert await reg.execute("a", {}) == "ran"  # hook 异常 → 放行

    async def test_transient_retry_then_success(self):
        reg = ToolRegistry()
        calls = {"n": 0}

        async def _impl(arguments):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("transient")
            return "ok"

        reg.register(_tool_def("a", execute=_impl))
        reg.default_retry = RetryConfig(max_retries=1, base_delay=0, max_delay=0)
        assert await reg.execute("a", {}) == "ok"
        assert calls["n"] == 2


class TestSearchInFilesListMode:
    """测试 search_in_files 的目录列表模式（pattern 为空）。"""

    @pytest.mark.asyncio
    async def test_list_current_dir(self):
        """pattern 为空 → 列出当前目录。"""
        result = await search_in_files.execute({"pattern": ""})
        assert "错误" not in result

    @pytest.mark.asyncio
    async def test_list_dir_not_exists(self):
        result = await search_in_files.execute({"pattern": "", "directory": "/NONEXISTENT_DIR_XYZ"})
        assert "错误" in result

    @pytest.mark.asyncio
    async def test_list_file_not_dir(self, tmp_path):
        """传入文件路径应报错。"""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = await search_in_files.execute({"pattern": "", "directory": str(f)})
        assert "不是目录" in result.lower() or "错误" in result.lower()

    @pytest.mark.asyncio
    async def test_list_with_glob(self, tmp_path):
        """测试 glob 过滤。"""
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        (tmp_path / "c.txt").write_text("")
        result = await search_in_files.execute({
            "pattern": "", "directory": str(tmp_path), "glob": "*.py",
        })
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result

    @pytest.mark.asyncio
    async def test_list_recursive(self, tmp_path):
        """测试递归列出。"""
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("")
        (tmp_path / "root.py").write_text("")
        result = await search_in_files.execute({
            "pattern": "", "directory": str(tmp_path), "recursive": True,
        })
        assert "root.py" in result
        assert "deep.py" in result

    @pytest.mark.asyncio
    async def test_list_empty_dir(self, tmp_path):
        """空目录。"""
        empty = tmp_path / "empty"
        empty.mkdir()
        result = await search_in_files.execute({"pattern": "", "directory": str(empty)})
        assert "空" in result.lower() or "为空" in result.lower()


class TestSearchInFilesSchema:
    """验证 schema 合法性。"""

    def test_schema(self):
        schema = search_in_files.schema
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "pattern" in schema["properties"]
        assert "directory" in schema["properties"]
        assert "glob" in schema["properties"]
        assert "recursive" in schema["properties"]
        assert "case_sensitive" in schema["properties"]
        assert "max_results" in schema["properties"]
        # pattern 不再是 required — 空 pattern = 列表模式
        assert "pattern" not in schema.get("required", [])
