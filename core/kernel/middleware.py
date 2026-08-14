"""ChatMiddleware — 对话中间件协议 + ChatContext。

P6: 可插拔的对话处理管线。内置中间件处理上下文组装、FC 循环、
摄入保存、Token 计数、反馈验证。插件可注册自定义中间件插入任意位置。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable, TYPE_CHECKING

if TYPE_CHECKING:
    from .protocols import ExecutorUI, TokenUsage

logger = logging.getLogger(__name__)


# ── ChatContext ───────────────────────────────────────────────────────

@dataclass
class ChatContext:
    """一轮对话的所有状态 — 在中间件之间传递。

    Attributes:
        user_msg: 用户输入文本
        session_dir: 当前会话目录
        turn: 轮次号
        conversation: 对话历史（中间件可能修改）
        ui: UI 回调接口

        system_messages: 上下文组装后填充（before_fc_loop 阶段可用）
        assistant_text: FC 循环完成后填充
        new_conversation: 合并后的完整对话历史
        turn_messages: 本轮新增的消息（用于摄入保存）
        token_usage: Token 计数结果
        metadata: 中间件间自由通信的字典
        errors: 中间件异常记录
    """
    # 输入（创建时填入）
    user_msg: str
    session_dir: Path | None
    turn: int
    conversation: list[dict]
    ui: ExecutorUI

    # 中间产物（各阶段逐步填充）
    system_messages: list[dict] = field(default_factory=list)
    assistant_text: str = ""
    thinking: str = ""  # 本轮 LLM 思考内容（持久化恢复用，不进 LLM 上下文）
    new_conversation: list[dict] = field(default_factory=list)
    turn_messages: list[dict] = field(default_factory=list)
    token_usage: TokenUsage | None = None

    # 扩展点
    metadata: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


# ── ChatMiddleware Protocol ───────────────────────────────────────────

@runtime_checkable
class ChatMiddleware(Protocol):
    """对话中间件协议。

    每个中间件可实现任意 hook 方法。框架按注册顺序调用。
    中间件不应抛出异常——捕获错误写入 ctx.errors 继续执行。

    Hook 生命周期：
      before_context → after_context → before_fc_loop → after_fc_loop
    """

    async def before_context(self, ctx: ChatContext) -> ChatContext:
        """上下文组装前。可修改 user_msg、注入前置逻辑。"""
        ...

    async def after_context(self, ctx: ChatContext) -> ChatContext:
        """上下文组装后、FC 循环前。可检查/修改 system_messages。"""
        ...

    async def before_fc_loop(self, ctx: ChatContext) -> ChatContext:
        """FC 循环前最后机会。"""
        ...

    async def after_fc_loop(self, ctx: ChatContext) -> ChatContext:
        """FC 循环完成后。可修改 assistant_text 等。"""
        ...


# ── 内置中间件（框架级义务，不可跳过）───────────────────────────────


class ContextAssemblyMiddleware:
    """组装上下文 + 裁剪对话窗口。"""

    def __init__(self, pipeline, plugins):
        self._pipeline = pipeline
        self._plugins = plugins

    async def before_context(self, ctx: ChatContext) -> ChatContext:
        tool_descriptions = None
        if hasattr(self._pipeline, '_tool_registry'):
            pass  # 从外部获取，下面实际调用时传递
        # 委托给 AgentKernel 的 _assemble_context 逻辑
        return ctx


class FCLoopMiddleware:
    """执行 Function Calling 循环。"""

    def __init__(self, fc_loop):
        self._fc_loop = fc_loop

    async def before_fc_loop(self, ctx: ChatContext) -> ChatContext:
        return ctx


class IngestMiddleware:
    """摄入保存对话。"""

    def __init__(self, ingester):
        self._ingester = ingester

    async def after_fc_loop(self, ctx: ChatContext) -> ChatContext:
        return ctx


class TokenCounterMiddleware:
    """计算 Token 使用量。"""

    def __init__(self, tool_registry, context_window):
        self._tool_registry = tool_registry
        self._context_window = context_window

    async def after_fc_loop(self, ctx: ChatContext) -> ChatContext:
        return ctx


class FeedbackMiddleware:
    """反馈验证。"""

    def __init__(self, verifier, pipeline):
        self._verifier = verifier
        self._pipeline = pipeline

    async def after_fc_loop(self, ctx: ChatContext) -> ChatContext:
        return ctx


# ── MiddlewareRunner ───────────────────────────────────────────────────

class MiddlewareRunner:
    """按顺序执行中间件链。

    用法:
        runner = MiddlewareRunner([ContextAssembly(...), FCLoop(...), ...])
        ctx = await runner.before_context(ctx)
        ctx = await runner.after_context(ctx)
        ...
    """

    def __init__(self, middlewares: list[ChatMiddleware] | None = None) -> None:
        self._middlewares: list[ChatMiddleware] = middlewares or []
        self._plugin_middlewares: list[ChatMiddleware] = []

    def add(self, mw: ChatMiddleware) -> None:
        """追加一个中间件（插件使用）。"""
        self._plugin_middlewares.append(mw)

    def remove(self, mw: ChatMiddleware) -> bool:
        """移除一个中间件。"""
        if mw in self._plugin_middlewares:
            self._plugin_middlewares.remove(mw)
            return True
        return False

    @property
    def all(self) -> list[ChatMiddleware]:
        return self._middlewares + self._plugin_middlewares

    async def _run_hook(self, hook_name: str, ctx: ChatContext) -> ChatContext:
        for mw in self.all:
            hook = getattr(mw, hook_name, None)
            if hook is not None and callable(hook):
                try:
                    ctx = await hook(ctx)
                except Exception as e:
                    logger.debug(f"中间件 {type(mw).__name__}.{hook_name} 异常: {e}")
                    ctx.errors.append(f"{type(mw).__name__}.{hook_name}: {e}")
        return ctx

    async def before_context(self, ctx: ChatContext) -> ChatContext:
        return await self._run_hook("before_context", ctx)

    async def after_context(self, ctx: ChatContext) -> ChatContext:
        return await self._run_hook("after_context", ctx)

    async def before_fc_loop(self, ctx: ChatContext) -> ChatContext:
        return await self._run_hook("before_fc_loop", ctx)

    async def after_fc_loop(self, ctx: ChatContext) -> ChatContext:
        return await self._run_hook("after_fc_loop", ctx)
