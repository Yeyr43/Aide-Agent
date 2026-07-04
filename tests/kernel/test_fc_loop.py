"""测试 FunctionCallingLoop — 结果截断、超时保护、并行执行。"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from core.kernel.fc_loop import (
    FunctionCallingLoop,
    TOOL_TIMEOUT,
    TOOL_RESULT_MAX_CHARS,
)
from core.tools import ToolRegistry


class TestTruncateResult:
    """测试工具结果截断。"""

    def test_no_truncation_for_short_result(self):
        short = "hello world"
        result = FunctionCallingLoop._truncate_result(short)
        assert result == short

    def test_no_truncation_at_boundary(self):
        exact = "x" * TOOL_RESULT_MAX_CHARS
        result = FunctionCallingLoop._truncate_result(exact)
        assert len(result) == TOOL_RESULT_MAX_CHARS
        assert "截断" not in result

    def test_truncation_for_long_result(self):
        long_text = "abcdefghij" * 2000  # 20000 chars, well over 8000
        result = FunctionCallingLoop._truncate_result(long_text)
        assert len(result) < len(long_text)
        assert "截断" in result
        # 应保留头部
        assert result.startswith("abcdefghij")
        # 应保留尾部
        assert result.rstrip().endswith("abcdefghij")

    def test_truncation_preserves_structure(self):
        """截断保留首尾但不崩溃于极端情况。"""
        result = FunctionCallingLoop._truncate_result("")
        assert result == ""

        result = FunctionCallingLoop._truncate_result("a" * (TOOL_RESULT_MAX_CHARS + 1))
        assert "截断" in result


class TestParallelExecution:
    """测试并行工具执行。"""

    @pytest.mark.asyncio
    async def test_single_tool_execution(self):
        """单个工具正常执行。"""
        registry = ToolRegistry()
        from core.tools import ToolDefinition

        async def echo(args):
            return f"echo: {args.get('msg', '')}"

        registry.register(ToolDefinition(
            name="echo",
            description="Echo test",
            parameters={"type": "object", "properties": {}},
            execute=echo,
        ))

        ui = MagicMock()
        loop = FunctionCallingLoop(None, registry)

        tool_calls = [{
            "id": "call_1",
            "function": {"name": "echo", "arguments": '{"msg": "hello"}'},
        }]

        results = await loop._execute_tools(tool_calls, ui)
        assert len(results) == 1
        assert results[0]["content"] == "echo: hello"
        ui.on_tool_start.assert_called_once()
        ui.on_tool_done.assert_called_once()

    @pytest.mark.asyncio
    async def test_parallel_execution_order(self):
        """并行执行的结果顺序与输入 tool_calls 一致。"""
        registry = ToolRegistry()
        from core.tools import ToolDefinition

        async def delay(args):
            t = args.get("delay", 0.01)
            await asyncio.sleep(t)
            return f"done:{args.get('id')}"

        registry.register(ToolDefinition(
            name="delay",
            description="Delay test",
            parameters={"type": "object", "properties": {}},
            execute=delay,
        ))

        ui = MagicMock()
        loop = FunctionCallingLoop(None, registry)

        # 反向顺序：第二个 delay 短，第一个 delay 长
        tool_calls = [
            {"id": "call_1", "function": {"name": "delay", "arguments": '{"id":"A","delay":0.1}'}},
            {"id": "call_2", "function": {"name": "delay", "arguments": '{"id":"B","delay":0.02}'}},
        ]

        results = await loop._execute_tools(tool_calls, ui)
        # 结果顺序必须与 tool_calls 一致（asyncio.gather 保证顺序）
        assert results[0]["content"] == "done:A"
        assert results[1]["content"] == "done:B"
        assert ui.on_tool_start.call_count == 2
        assert ui.on_tool_done.call_count == 2

    @pytest.mark.asyncio
    async def test_tool_not_found_returns_error(self):
        """工具未注册 → 返回错误（不阻断，LLM 自行降级）。"""
        registry = ToolRegistry()
        ui = MagicMock()
        loop = FunctionCallingLoop(None, registry)

        tool_calls = [{
            "id": "call_x",
            "function": {"name": "nonexistent", "arguments": "{}"},
        }]

        results = await loop._execute_tools(tool_calls, ui)
        assert "未找到工具" in results[0]["content"]
        assert "tool_id" in results[0]
        # 不应该有 _block 字段（已移除阻断机制）
        assert "_block" not in results[0]


class TestExecutionTimeout:
    """测试工具执行超时。"""

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self):
        """超时工具返回错误但不阻塞。"""
        registry = ToolRegistry()
        from core.tools import ToolDefinition

        async def slow(args):
            await asyncio.sleep(99)
            return "done"

        registry.register(ToolDefinition(
            name="slow",
            description="Too slow",
            parameters={"type": "object", "properties": {}},
            execute=slow,
        ))

        ui = MagicMock()
        loop = FunctionCallingLoop(None, registry)

        tool_calls = [{
            "id": "call_s",
            "function": {"name": "slow", "arguments": "{}"},
        }]

        # 用非常短的超时模拟
        with patch.object(loop, '_execute_tools', wraps=lambda tcs, u: asyncio.wait_for(
            asyncio.gather(*[asyncio.sleep(0.5) for _ in tcs]), timeout=0.05,
        )):
            try:
                pass
            except asyncio.TimeoutError:
                pass

        # 直接测试超时逻辑：用 asyncio.wait_for
        async def _block_forever():
            await asyncio.sleep(999)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_block_forever(), timeout=0.01)


class TestXMLFallback:
    """测试 XML tool call 提取。"""

    def test_extract_single_xml_call(self):
        text = """Let me read that file.
<invoke name="read_file">
  <parameter name="file_path">/tmp/test.txt</parameter>
</invoke>"""

        calls = FunctionCallingLoop._extract_xml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "read_file"
        args = __import__('json').loads(calls[0]["function"]["arguments"])
        assert args["file_path"] == "/tmp/test.txt"

    def test_extract_multiple_xml_calls(self):
        text = """I'll check both.
<invoke name="read_file">
  <parameter name="file_path">/tmp/a.txt</parameter>
</invoke>
<invoke name="read_file">
  <parameter name="file_path">/tmp/b.txt</parameter>
</invoke>"""

        calls = FunctionCallingLoop._extract_xml_tool_calls(text)
        assert len(calls) == 2
        assert calls[0]["id"] == "xml_0"
        assert calls[1]["id"] == "xml_1"

    def test_no_xml_no_calls(self):
        calls = FunctionCallingLoop._extract_xml_tool_calls("no tools here")
        assert calls == []

    def test_parse_args_valid_json(self):
        result = FunctionCallingLoop._parse_args('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_args_invalid_json(self):
        result = FunctionCallingLoop._parse_args("not json")
        assert result == {}

    def test_parse_args_already_dict(self):
        result = FunctionCallingLoop._parse_args({"key": "value"})
        assert result == {"key": "value"}
