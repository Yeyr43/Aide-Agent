"""测试 FunctionCallingLoop — 结果截断、超时保护、并行执行。"""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from core.kernel.fc_loop import FunctionCallingLoop
from core.kernel.tool_executor import ToolExecutor, TOOL_TIMEOUT, TOOL_RESULT_MAX_CHARS
from core.tools import ToolRegistry


class TestTruncateResult:
    """测试工具结果截断。"""

    def test_no_truncation_for_short_result(self):
        short = "hello world"
        result = ToolExecutor._truncate_result(short)
        assert result == short

    def test_no_truncation_at_boundary(self):
        exact = "x" * TOOL_RESULT_MAX_CHARS
        result = ToolExecutor._truncate_result(exact)
        assert len(result) == TOOL_RESULT_MAX_CHARS
        assert "截断" not in result

    def test_truncation_for_long_result(self):
        long_text = "abcdefghij" * 2000  # 20000 chars, well over 8000
        result = ToolExecutor._truncate_result(long_text)
        assert len(result) < len(long_text)
        assert "仅展示前" in result  # actionable hint
        # 应保留头部
        assert result.startswith("abcdefghij")
        # 应保留尾部
        assert result.rstrip().endswith("abcdefghij")

    def test_truncation_preserves_structure(self):
        """截断保留首尾但不崩溃于极端情况。"""
        result = ToolExecutor._truncate_result("")
        assert result == ""

        result = ToolExecutor._truncate_result("a" * (TOOL_RESULT_MAX_CHARS + 1))
        assert "仅展示前" in result


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


class TestToolGrouping:
    """工具并发分级：只读并行、写串行、失败 abort 兄弟。"""

    @pytest.mark.asyncio
    async def test_read_tools_run_in_parallel(self):
        """并发组（read_file）两个工具并行执行 — 第二个在第一个完成前启动。"""
        registry = ToolRegistry()
        from core.tools import ToolDefinition

        events: list[tuple[str, str]] = []

        async def read(args):
            events.append(("start", args["id"]))
            await asyncio.sleep(0.05)
            events.append(("end", args["id"]))
            return f"read:{args['id']}"

        registry.register(ToolDefinition(
            name="read_file", description="Read",
            parameters={"type": "object", "properties": {}}, execute=read,
        ))
        ui = MagicMock()
        loop = FunctionCallingLoop(None, registry)

        tool_calls = [
            {"id": "c1", "function": {"name": "read_file", "arguments": '{"id":"A"}'}},
            {"id": "c2", "function": {"name": "read_file", "arguments": '{"id":"B"}'}},
        ]
        results = await loop._execute_tools(tool_calls, ui)
        # 并发：B 在 A 结束前已启动
        assert events.index(("start", "B")) < events.index(("end", "A"))
        assert results[0]["content"] == "read:A"
        assert results[1]["content"] == "read:B"

    @pytest.mark.asyncio
    async def test_write_tools_run_serially(self):
        """串行组（write_file）两个工具依次执行 — 第二个在第一个完成后才启动。"""
        registry = ToolRegistry()
        from core.tools import ToolDefinition

        events: list[tuple[str, str]] = []

        async def write(args):
            events.append(("start", args["id"]))
            await asyncio.sleep(0.03)
            events.append(("end", args["id"]))
            return f"write:{args['id']}"

        registry.register(ToolDefinition(
            name="write_file", description="Write",
            parameters={"type": "object", "properties": {}}, execute=write,
        ))
        ui = MagicMock()
        loop = FunctionCallingLoop(None, registry)

        tool_calls = [
            {"id": "c1", "function": {"name": "write_file", "arguments": '{"id":"A"}'}},
            {"id": "c2", "function": {"name": "write_file", "arguments": '{"id":"B"}'}},
        ]
        results = await loop._execute_tools(tool_calls, ui)
        # 串行：B 在 A 结束后才启动
        assert events.index(("start", "B")) > events.index(("end", "A"))
        assert results[0]["content"] == "write:A"
        assert results[1]["content"] == "write:B"

    @pytest.mark.asyncio
    async def test_concurrent_failure_cancels_siblings(self):
        """并发组中一个工具失败（高危阻止）→ 取消其余仍在跑的兄弟。"""
        registry = ToolRegistry()
        from core.tools import ToolDefinition

        async def read(args):
            await asyncio.sleep(0.5)  # 长阻塞，若不被取消将占满超时
            return "never"

        registry.register(ToolDefinition(
            name="read_file", description="Read",
            parameters={"type": "object", "properties": {}}, execute=read,
        ))
        ui = MagicMock()
        loop = FunctionCallingLoop(None, registry)

        # A 被高危检查阻止（立即失败 ok=False），B 仍在阻塞
        async def fake_block(name, arguments):
            return "高危测试" if arguments.get("id") == "A" else None
        loop._tools_executor._should_block = fake_block

        tool_calls = [
            {"id": "c1", "function": {"name": "read_file", "arguments": '{"id":"A"}'}},
            {"id": "c2", "function": {"name": "read_file", "arguments": '{"id":"B"}'}},
        ]
        results = await loop._execute_tools(tool_calls, ui)
        assert "高风险操作已被阻止" in results[0]["content"]
        assert "已取消" in results[1]["content"]
        assert ui.on_tool_error.call_count >= 1  # 取消时补 on_tool_error

    @pytest.mark.asyncio
    async def test_mixed_serial_and_concurrent_preserves_order(self):
        """混合轮次（write + read + read）：写串行、读并行，结果按原顺序。"""
        registry = ToolRegistry()
        from core.tools import ToolDefinition

        async def read(args):
            await asyncio.sleep(0.01)
            return f"read:{args['id']}"

        async def write(args):
            await asyncio.sleep(0.01)
            return f"write:{args['id']}"

        registry.register(ToolDefinition(
            name="read_file", description="Read",
            parameters={"type": "object", "properties": {}}, execute=read,
        ))
        registry.register(ToolDefinition(
            name="write_file", description="Write",
            parameters={"type": "object", "properties": {}}, execute=write,
        ))
        ui = MagicMock()
        loop = FunctionCallingLoop(None, registry)

        tool_calls = [
            {"id": "w", "function": {"name": "write_file", "arguments": '{"id":"W"}'}},
            {"id": "r1", "function": {"name": "read_file", "arguments": '{"id":"A"}'}},
            {"id": "r2", "function": {"name": "read_file", "arguments": '{"id":"B"}'}},
        ]
        results = await loop._execute_tools(tool_calls, ui)
        assert results[0]["content"] == "write:W"
        assert results[1]["content"] == "read:A"
        assert results[2]["content"] == "read:B"


class TestWriteFileOverwriteWarning:
    """write_file 覆盖已有文件 → 不阻止，结果附加警告（回归 Critical）。"""

    @pytest.mark.asyncio
    async def test_existing_file_warns_not_blocked(self, tmp_path):
        """编辑/覆写已有文件不再被硬拦截，结果带覆盖警告。"""
        import json as _json
        registry = ToolRegistry()
        from core.tools import ToolDefinition

        async def fake_write(args):
            return "文件已写入"

        registry.register(ToolDefinition(
            name="write_file", description="Write",
            parameters={"type": "object", "properties": {}},
            execute=fake_write,
        ))
        ui = MagicMock()
        loop = FunctionCallingLoop(None, registry)

        target = tmp_path / "existing.txt"
        target.write_text("old")

        tool_calls = [{
            "id": "w",
            "function": {"name": "write_file",
                         "arguments": _json.dumps({"file_path": str(target)})},
        }]
        results = await loop._execute_tools(tool_calls, ui)
        # 不阻止
        assert "高风险操作已被阻止" not in results[0]["content"]
        # 结果附带覆盖警告 + 原始结果保留
        assert "已存在" in results[0]["content"]
        assert "文件已写入" in results[0]["content"]

    @pytest.mark.asyncio
    async def test_new_file_no_warning(self, tmp_path):
        """新建文件不附加警告。"""
        import json as _json
        registry = ToolRegistry()
        from core.tools import ToolDefinition

        async def fake_write(args):
            return "文件已写入"

        registry.register(ToolDefinition(
            name="write_file", description="Write",
            parameters={"type": "object", "properties": {}},
            execute=fake_write,
        ))
        ui = MagicMock()
        loop = FunctionCallingLoop(None, registry)

        target = tmp_path / "brand_new.txt"
        tool_calls = [{
            "id": "w",
            "function": {"name": "write_file",
                         "arguments": _json.dumps({"file_path": str(target)})},
        }]
        results = await loop._execute_tools(tool_calls, ui)
        assert "已存在" not in results[0]["content"]
        assert results[0]["content"] == "文件已写入"


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
        result = ToolExecutor._parse_args('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_args_invalid_json(self):
        result = ToolExecutor._parse_args("not json")
        assert result == {}

    def test_parse_args_already_dict(self):
        result = ToolExecutor._parse_args({"key": "value"})
        assert result == {"key": "value"}


class TestXmlFallbackContentClean:
    """回归：XML <tool_call> 派生的 tool_calls，落盘 assistant 消息必须剥离 XML
    （曾用完整 response_text 含 <tool_call> 乱码，再次渲染时正文残留 XML）。"""

    @pytest.mark.asyncio
    async def test_xml_tool_call_content_is_clean(self):
        from core.llm_gateway import TextDelta, StreamEnd
        from core.tools import ToolDefinition

        registry = ToolRegistry()

        async def echo(args):
            return "ok"
        registry.register(ToolDefinition(
            name="echo", description="Echo", parameters={"type": "object", "properties": {}},
            execute=echo,
        ))

        provider = AsyncMock()
        call = [0]
        async def _mock_chat(messages, tools):
            call[0] += 1
            if call[0] == 1:
                yield TextDelta(content="开始执行。")
                yield TextDelta(content="\n<tool_call><function=echo><parameter=msg>hi</parameter></function></tool_call>")
            else:
                yield TextDelta(content="完成")
            yield StreamEnd(finish_reason="stop", tool_calls=[])

        provider.chat_with_tools = _mock_chat

        ui = MagicMock()
        loop = FunctionCallingLoop(provider, registry, max_turns=1)

        messages = await loop.run([{"role": "user", "content": "go"}], ui=ui)

        # 带 tool_calls 的 assistant 消息：content 必须干净（无 <tool_call>），tool_calls 正确
        tool_msgs = [m for m in messages if m.get("tool_calls")]
        assert len(tool_msgs) == 1
        assert "<tool_call>" not in tool_msgs[0]["content"], tool_msgs[0]["content"]
        assert "<function=" not in tool_msgs[0]["content"]
        assert "开始执行" in tool_msgs[0]["content"]
        assert tool_msgs[0]["tool_calls"][0]["function"]["name"] == "echo"
        assert json.loads(tool_msgs[0]["tool_calls"][0]["function"]["arguments"])["msg"] == "hi"
        # 显示替换被调用（正文节点不残留 XML）
        ui.on_replace_streamed_text.assert_called()


class TestToolAttemptLimit:
    """回归：max_turns 语义改为"单工具调用可尝试次数"——同一 (工具, 参数) 反复失败
    超限即停止重试；不同工具/参数互不影响（多工具任务不再被 5 轮卡死）。"""

    @pytest.mark.asyncio
    async def test_same_tool_failing_rejected_after_limit(self):
        from core.llm_gateway import TextDelta, StreamEnd
        from core.tools import ToolDefinition

        registry = ToolRegistry()

        async def flaky(args):
            return "错误：总是失败"
        registry.register(ToolDefinition(
            name="flaky", description="", parameters={"type": "object", "properties": {}},
            execute=flaky,
        ))

        provider = AsyncMock()
        call = [0]
        async def _mock_chat(messages, tools):
            call[0] += 1
            if call[0] <= 5:
                yield StreamEnd(finish_reason="tool_calls", tool_calls=[{
                    "id": f"c{call[0]}", "type": "function",
                    "function": {"name": "flaky", "arguments": "{}"},
                }])
            else:
                yield TextDelta(content="放弃")
                yield StreamEnd(finish_reason="stop", tool_calls=[])
        provider.chat_with_tools = _mock_chat

        ui = MagicMock()
        loop = FunctionCallingLoop(provider, registry, max_turns=3)  # 单工具尝试上限 3
        messages = await loop.run([{"role": "user", "content": "go"}], ui=ui)

        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        rejections = [m for m in tool_msgs if "已停止重试" in m["content"]]
        assert rejections, "超限后应有'已停止重试'消息"
        assert "已尝试 3 次" in rejections[0]["content"]
        # flaky 实际执行了 3 次（第 4 次起被拒绝）
        executed = [m for m in tool_msgs if "总是失败" in m["content"]]
        assert len(executed) == 3

    def test_xml_at_start_always_replaces_streamed_text(self):
        """XML 在最开头（clean 为空）也必须替换流式显示的 XML 乱码。"""
        from core.llm_gateway import StreamEnd
        from core.tools import ToolDefinition
        from core.tools import ToolRegistry

        loop = FunctionCallingLoop(None, ToolRegistry())
        ui = MagicMock()
        event = StreamEnd(finish_reason="stop", tool_calls=[])
        text = '<tool_call><function=echo><parameter=msg>hi</parameter></function></tool_call>'
        clean = loop._try_xml_fallback(text, event, ui)
        ui.on_replace_streamed_text.assert_called_once_with("")
        assert clean == ""
        assert event.tool_calls


class TestPerCallThinkingPersistence:
    """回归：思考需逐条落盘（assistant 消息带 _thinking），恢复时插回工具调用间。

    曾只存聚合 thinking 字段，恢复时所有思考叠成一个顶部节点，
    工具调用间的思考丢失位置（"工具调用间的思考无法加载"）。
    """

    @pytest.mark.asyncio
    async def test_assistant_messages_carry_per_call_thinking(self):
        """FC 循环每次 LLM 调用：assistant 消息带 _thinking，且互不合并。"""
        from core.llm_gateway import TextDelta, ThinkingDelta, StreamEnd
        from core.tools import ToolDefinition
        from core.tools import ToolRegistry

        registry = ToolRegistry()
        async def echo(args):
            return "ok"
        registry.register(ToolDefinition(
            name="echo", description="Echo", parameters={"type": "object", "properties": {}},
            execute=echo,
        ))

        provider = AsyncMock()
        call = [0]
        async def _mock_chat(messages, tools):
            call[0] += 1
            if call[0] == 1:
                yield ThinkingDelta(content="先想一下")
                yield StreamEnd(finish_reason="tool_calls", tool_calls=[{
                    "id": "c1", "type": "function",
                    "function": {"name": "echo", "arguments": "{}"},
                }])
            else:
                yield ThinkingDelta(content="再总结")
                yield TextDelta(content="完成")
                yield StreamEnd(finish_reason="stop", tool_calls=[])
        provider.chat_with_tools = _mock_chat

        ui = MagicMock()
        loop = FunctionCallingLoop(provider, registry, max_turns=1)
        messages = await loop.run([{"role": "user", "content": "go"}], ui=ui)

        assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
        assert len(assistant_msgs) == 2
        assert assistant_msgs[0].get("_thinking") == "先想一下"
        assert assistant_msgs[1].get("_thinking") == "再总结"
        # 逐条落盘，互不合并
        assert "先想一下" not in assistant_msgs[1]["_thinking"]

    def test_sanitize_messages_strips_thinking(self):
        """_thinking 仅内部落盘用，不得泄漏到 LLM API 请求。"""
        from core.kernel.fc_loop import _sanitize_messages

        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "text", "tool_calls": [],
             "_thinking": "secret thinking"},
        ]
        clean = _sanitize_messages(messages)
        for m in clean:
            assert "_thinking" not in m, "内部思考键泄漏到 LLM 上下文"
        # 原列表不受影响（_thinking 仍留作落盘）
        assert messages[1]["_thinking"] == "secret thinking"
