"""测试 middleware.py — ChatMiddleware 协议 + MiddlewareRunner。"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.kernel.middleware import (
    ChatContext,
    ChatMiddleware,
    MiddlewareRunner,
)


# ── 测试用中间件 ────────────────────────────────────────────────────────

class NoopMiddleware:
    """不实现任何 hook 的中间件 — 应被安全跳过。"""
    pass


class CountingMiddleware:
    """记录各 hook 调用次数。"""

    def __init__(self):
        self.before_context_calls = 0
        self.after_context_calls = 0
        self.before_fc_loop_calls = 0
        self.after_fc_loop_calls = 0

    async def before_context(self, ctx: ChatContext) -> ChatContext:
        self.before_context_calls += 1
        ctx.metadata["mw_touched"] = True
        return ctx

    async def after_context(self, ctx: ChatContext) -> ChatContext:
        self.after_context_calls += 1
        return ctx

    async def before_fc_loop(self, ctx: ChatContext) -> ChatContext:
        self.before_fc_loop_calls += 1
        return ctx

    async def after_fc_loop(self, ctx: ChatContext) -> ChatContext:
        self.after_fc_loop_calls += 1
        return ctx


class MetadataMiddleware:
    """通过 metadata 在中间件间传递数据。"""

    async def before_context(self, ctx: ChatContext) -> ChatContext:
        ctx.metadata["injected"] = "hello from mw"
        return ctx


class ErrorMiddleware:
    """故意抛出异常的中间件 — 应被捕获而不中断链。"""

    async def after_context(self, ctx: ChatContext) -> ChatContext:
        raise RuntimeError("middleware error")

    async def before_fc_loop(self, ctx: ChatContext) -> ChatContext:
        raise ValueError("another error")


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def ui():
    """创建一个最小 UI mock。"""
    ui = MagicMock()
    ui.on_tool_start = AsyncMock()
    ui.on_tool_done = AsyncMock()
    ui.on_tool_error = AsyncMock()
    ui.on_token_count = MagicMock()
    return ui


@pytest.fixture
def ctx(ui):
    """创建一个基础的 ChatContext。"""
    return ChatContext(
        user_msg="hello",
        session_dir=None,
        turn=1,
        conversation=[],
        ui=ui,
    )


# ── ChatContext 测试 ──────────────────────────────────────────────────────

class TestChatContext:
    """测试 ChatContext dataclass。"""

    def test_default_values(self, ui):
        ctx = ChatContext(
            user_msg="test",
            session_dir=None,
            turn=0,
            conversation=[],
            ui=ui,
        )
        assert ctx.user_msg == "test"
        assert ctx.system_messages == []
        assert ctx.assistant_text == ""
        assert ctx.token_usage is None
        assert ctx.metadata == {}
        assert ctx.errors == []

    def test_metadata_mutable(self, ui):
        ctx = ChatContext(
            user_msg="test",
            session_dir=None,
            turn=0,
            conversation=[],
            ui=ui,
        )
        ctx.metadata["key"] = "value"
        assert ctx.metadata["key"] == "value"

    def test_errors_append(self, ui):
        ctx = ChatContext(
            user_msg="test",
            session_dir=None,
            turn=0,
            conversation=[],
            ui=ui,
        )
        ctx.errors.append("something went wrong")
        assert len(ctx.errors) == 1


# ── MiddlewareRunner 测试 ─────────────────────────────────────────────────

class TestMiddlewareRunner:
    """测试 MiddlewareRunner — 中间件链编排。"""

    async def test_empty_runner(self, ctx):
        runner = MiddlewareRunner()
        result = await runner.before_context(ctx)
        assert result is ctx

    async def test_single_hook_called(self, ctx):
        mw = CountingMiddleware()
        runner = MiddlewareRunner([mw])
        result = await runner.before_context(ctx)
        assert mw.before_context_calls == 1
        assert result.metadata["mw_touched"] is True

    async def test_all_hooks_called_in_order(self, ctx):
        mw = CountingMiddleware()
        runner = MiddlewareRunner([mw])

        await runner.before_context(ctx)
        await runner.after_context(ctx)
        await runner.before_fc_loop(ctx)
        await runner.after_fc_loop(ctx)

        assert mw.before_context_calls == 1
        assert mw.after_context_calls == 1
        assert mw.before_fc_loop_calls == 1
        assert mw.after_fc_loop_calls == 1

    async def test_error_middleware_does_not_stop_chain(self, ctx):
        mw = ErrorMiddleware()
        runner = MiddlewareRunner([mw])

        # 不应抛出异常
        await runner.after_context(ctx)
        await runner.before_fc_loop(ctx)

        assert len(ctx.errors) >= 2
        assert any("RuntimeError" in e or "middleware error" in e for e in ctx.errors)
        assert any("ValueError" in e or "another error" in e for e in ctx.errors)

    async def test_error_middleware_is_caught(self, ctx):
        mw = ErrorMiddleware()
        runner = MiddlewareRunner([mw])
        # 不应抛出
        result = await runner.after_context(ctx)
        assert result is ctx

    async def test_metadata_passing_between_middlewares(self, ctx):
        mw1 = MetadataMiddleware()
        mw2 = CountingMiddleware()
        runner = MiddlewareRunner([mw1, mw2])

        await runner.before_context(ctx)
        assert ctx.metadata["injected"] == "hello from mw"
        assert ctx.metadata["mw_touched"] is True

    async def test_noop_middleware_skipped_safely(self, ctx):
        """不实现任何 hook 的中间件应被安全跳过。"""
        mw = NoopMiddleware()
        runner = MiddlewareRunner([mw])
        # 不应抛出
        result = await runner.before_context(ctx)
        assert result is ctx
        result = await runner.after_context(ctx)
        assert result is ctx

    async def test_multiple_middlewares_chain(self, ctx):
        mw1 = CountingMiddleware()
        mw2 = CountingMiddleware()
        runner = MiddlewareRunner([mw1, mw2])

        await runner.before_context(ctx)
        assert mw1.before_context_calls == 1
        assert mw2.before_context_calls == 1
        assert ctx.metadata["mw_touched"] is True

    # ── add / remove ───────────────────────────────────────────────────

    async def test_add_plugin_middleware(self, ctx):
        runner = MiddlewareRunner()
        mw = CountingMiddleware()
        runner.add(mw)
        assert len(runner.all) == 1
        await runner.before_context(ctx)
        assert mw.before_context_calls == 1

    async def test_remove_middleware(self):
        runner = MiddlewareRunner()
        mw = CountingMiddleware()
        runner.add(mw)
        assert runner.remove(mw) is True
        assert len(runner.all) == 0
        # 重复移除返回 False
        assert runner.remove(mw) is False

    async def test_remove_from_builtin_fails(self):
        """内置中间件不可通过 remove 删除。"""
        mw = CountingMiddleware()
        runner = MiddlewareRunner([mw])
        # remove 只操作 _plugin_middlewares
        assert runner.remove(mw) is False
        assert len(runner.all) == 1

    async def test_all_combines_both_lists(self):
        builtin = CountingMiddleware()
        plugin = CountingMiddleware()
        runner = MiddlewareRunner([builtin])
        runner.add(plugin)
        assert len(runner.all) == 2


# ── Protocol 兼容性 ──────────────────────────────────────────────────────

class TestChatMiddlewareProtocol:
    """验证 ChatMiddleware Protocol 的兼容性 — 用 hasattr 而非 isinstance。

    @runtime_checkable 要求 Protocol 所有方法都存在，
    但中间件只实现需要的 hook（可选方法），
    所以 isinstance 会误判。运行时会用 getattr + callable 检查。
    """

    def test_counting_mw_has_hooks(self):
        mw = CountingMiddleware()
        assert callable(getattr(mw, 'before_context', None))
        assert callable(getattr(mw, 'after_context', None))

    def test_noop_mw_has_no_hooks(self):
        """NoopMiddleware 不实现任何 hook — Runner 会安全跳过。"""
        mw = NoopMiddleware()
        assert not callable(getattr(mw, 'before_context', None))
