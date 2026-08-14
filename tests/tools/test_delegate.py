"""测试 delegate 工具 — 子 agent 委托。

覆盖：空 prompt、运行时缺失、递归保护、白名单过滤、
完整委托流程、SubagentStop hook、max_turns clamp。
"""

import asyncio

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from core.tools import ToolRegistry, ToolDefinition, ToolContext
from core.tools import delegate
from core.llm_gateway import TextDelta, StreamEnd
from core.locale import t


def _make_registry() -> ToolRegistry:
    """构建含只读 + 写 + delegate 的主工具注册中心。"""
    registry = ToolRegistry()

    async def _stub(args):
        return "ok"

    for name in (
        "read_file", "search_in_files", "search_chat", "search_memory",
        "web", "write_file", "run_shell", "delegate",
    ):
        registry.register(ToolDefinition(
            name=name, description="", parameters={}, execute=_stub,
        ))
    return registry


def _make_ctx() -> ToolContext:
    """构建含 provider/tool_registry/hook_runner 的 ToolContext。"""
    hook_runner = MagicMock()
    hook_runner.run = AsyncMock(return_value=[])
    return ToolContext(
        provider=object(),
        tool_registry=_make_registry(),
        hook_runner=hook_runner,
    )


def _text_provider(text: str):
    """返回一个第一轮就输出纯文本的 fake provider。"""
    class _Provider:
        async def chat_with_tools(self, messages, tools):
            yield TextDelta(text)
            yield StreamEnd("stop", [], native_stop_reason="end_turn")
    return _Provider()


def _mock_loop(return_text: str = "结论"):
    """patch FunctionCallingLoop，返回固定结论，便于捕获 sub_registry/max_turns。"""
    mock_cls = MagicMock()
    fake_loop = MagicMock()
    fake_loop.run = AsyncMock(return_value=[{"role": "assistant", "content": return_text}])
    mock_cls.return_value = fake_loop
    return mock_cls


class TestDelegateInputValidation:
    @pytest.mark.asyncio
    async def test_empty_prompt(self):
        result = await delegate.execute({"prompt": ""}, ctx=None)
        assert result == t("tool.delegate.empty_prompt")

    @pytest.mark.asyncio
    async def test_unavailable_without_runtime(self):
        # ctx 为 None
        result = await delegate.execute({"prompt": "task"}, ctx=None)
        assert result == t("tool.delegate.unavailable")

        # ctx 有但缺 provider/registry
        result = await delegate.execute({"prompt": "task"}, ctx=ToolContext())
        assert result == t("tool.delegate.unavailable")


class TestRecursionProtection:
    @pytest.mark.asyncio
    async def test_delegate_excluded_from_subagent(self):
        """即使 tools 参数含 delegate，子 agent 的 registry 也不含它。"""
        ctx = _make_ctx()
        mock_cls = _mock_loop()

        with patch("core.kernel.fc_loop.FunctionCallingLoop", mock_cls):
            await delegate.execute(
                {"prompt": "task", "tools": ["delegate", "read_file"]}, ctx=ctx,
            )

        sub_registry = mock_cls.call_args[0][1]
        assert sub_registry.get("delegate") is None      # 递归保护
        assert sub_registry.get("read_file") is not None

    @pytest.mark.asyncio
    async def test_default_read_only_whitelist(self):
        """默认白名单只含只读工具，排除写工具和 delegate。"""
        ctx = _make_ctx()
        mock_cls = _mock_loop()

        with patch("core.kernel.fc_loop.FunctionCallingLoop", mock_cls):
            await delegate.execute({"prompt": "task"}, ctx=ctx)

        sub_registry = mock_cls.call_args[0][1]
        names = set(sub_registry.list_names())
        assert names == {"read_file", "search_in_files", "search_chat",
                         "search_memory", "web"}

    @pytest.mark.asyncio
    async def test_explicit_whitelist_respected(self):
        """tools 参数显式覆盖时，只注册指定的工具。"""
        ctx = _make_ctx()
        mock_cls = _mock_loop()

        with patch("core.kernel.fc_loop.FunctionCallingLoop", mock_cls):
            await delegate.execute(
                {"prompt": "task", "tools": ["read_file", "web"]}, ctx=ctx,
            )

        sub_registry = mock_cls.call_args[0][1]
        assert set(sub_registry.list_names()) == {"read_file", "web"}


class TestMaxTurnsClamp:
    @pytest.mark.asyncio
    async def test_clamp_upper(self):
        ctx = _make_ctx()
        mock_cls = _mock_loop()
        with patch("core.kernel.fc_loop.FunctionCallingLoop", mock_cls):
            await delegate.execute({"prompt": "task", "max_turns": 100}, ctx=ctx)
        assert mock_cls.call_args[1]["max_turns"] == 10

    @pytest.mark.asyncio
    async def test_clamp_lower(self):
        ctx = _make_ctx()
        mock_cls = _mock_loop()
        with patch("core.kernel.fc_loop.FunctionCallingLoop", mock_cls):
            await delegate.execute({"prompt": "task", "max_turns": 0}, ctx=ctx)
        assert mock_cls.call_args[1]["max_turns"] == 1

    @pytest.mark.asyncio
    async def test_clamp_default_on_invalid(self):
        ctx = _make_ctx()
        mock_cls = _mock_loop()
        with patch("core.kernel.fc_loop.FunctionCallingLoop", mock_cls):
            await delegate.execute({"prompt": "task", "max_turns": "x"}, ctx=ctx)
        assert mock_cls.call_args[1]["max_turns"] == 6


class TestFullDelegation:
    @pytest.mark.asyncio
    async def test_returns_conclusion(self):
        """完整委托流程：子 agent 第一轮输出纯文本，返回该结论。"""
        ctx = _make_ctx()
        ctx.provider = _text_provider("这是子任务的结论")
        ctx.hook_runner = None  # 本测试不关心 hook

        result = await delegate.execute({"prompt": "找出所有 TODO"}, ctx=ctx)
        assert "这是子任务的结论" in result

    @pytest.mark.asyncio
    async def test_subagent_stop_hook_triggered(self):
        """子 agent 结束时触发 SubagentStop hook。"""
        ctx = _make_ctx()
        ctx.provider = _text_provider("结论")

        await delegate.execute({"prompt": "task"}, ctx=ctx)

        events = [c[0][0] for c in ctx.hook_runner.run.call_args_list]
        assert "SubagentStop" in events


class TestConcurrencyLimit:
    @pytest.mark.asyncio
    async def test_rejects_when_concurrency_full(self):
        """并发满时直接拒绝：恰好 3 个成功，其余返回并发上限错误。"""
        ctx = _make_ctx()
        ctx.hook_runner = None

        active = 0
        max_active = 0

        async def _slow_run(messages, ui):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            active -= 1
            return [{"role": "assistant", "content": "done"}]

        mock_cls = MagicMock()
        fake_loop = MagicMock()
        fake_loop.run = _slow_run
        mock_cls.return_value = fake_loop

        # 同时发起 6 个 delegate，超过上限 3，超出的应被直接拒绝
        with patch("core.kernel.fc_loop.FunctionCallingLoop", mock_cls):
            results = await asyncio.gather(*[
                delegate.execute({"prompt": f"task{i}"}, ctx=ctx)
                for i in range(6)
            ])

        assert max_active <= delegate.MAX_CONCURRENT_SUBAGENTS
        expected_reject = t("tool.delegate.concurrent_limit",
                            max=delegate.MAX_CONCURRENT_SUBAGENTS)
        rejected = [r for r in results if r == expected_reject]
        succeeded = [r for r in results if "done" in r]
        assert len(succeeded) == 3
        assert len(rejected) == 3


class TestQueueStatus:
    @pytest.mark.asyncio
    async def test_status_returns_queue_info(self):
        """action=status 返回队列情况（上限/运行中/可用配额），无需 ctx。"""
        result = await delegate.execute({"action": "status"}, ctx=None)
        expected = t("tool.delegate.queue_status", max=3, active=0, available=3)
        assert result == expected

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        result = await delegate.execute({"action": "bogus"}, ctx=None)
        assert result == t("tool.delegate.unknown_action", action="bogus")
