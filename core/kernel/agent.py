"""AgentKernel — Aide 内核门面。

编排 6 个子组件，不实现逻辑，每个方法 ≤ 10 行。
P4 Batch 2: KernelContext 聚合 14 个依赖为单一注入参数。
"""

from __future__ import annotations

import logging
from pathlib import Path

from .context import KernelContext
from .protocols import ExecutorUI, ChatResult, TokenUsage
from .fc_loop import FunctionCallingLoop

from core.context.relevance import _split_conversation
from core.context.token_counter import compute_context_usage
from core.sessions.manager import SessionInfo

logger = logging.getLogger(__name__)


class AgentKernel:
    """Aide 内核 — 零 UI 依赖，可独立测试。

    用法:
        ctx = KernelContext(config=..., provider=..., ...)
        kernel = AgentKernel(ctx)
        result = await kernel.chat(msg, session_dir, turn, conv, ui=bridge)
    """

    def __init__(self, ctx: KernelContext) -> None:
        # 解构 KernelContext 为独立属性（保持方法体内 self.xxx 不变）
        self.config = ctx.config
        self.provider = ctx.provider
        self.tool_registry = ctx.tool_registry
        self.command_registry = ctx.command_registry
        self._pipeline = ctx.context_pipeline
        self._ingester = ctx.ingester
        self._compactor = ctx.compactor
        self._sessions = ctx.session_manager
        self._capture = ctx.capture_engine
        self._entries = ctx.entry_manager
        self._updater = ctx.prompt_updater
        self._tracker = ctx.topic_tracker
        self._plugins = ctx.plugin_host
        self._slots = ctx.slot_registry
        self._fc_loop = FunctionCallingLoop(
            ctx.provider, ctx.tool_registry,
            max_turns=ctx.config.app.max_turns,
        )

    # ── 运行时 provider 切换 ──

    def set_provider(self, new_provider) -> None:
        """切换 provider（用于 /model 命令或冷启动重载）。

        统一更新内核及其子组件的 provider 引用。
        """
        self.provider = new_provider
        self._fc_loop.provider = new_provider
        self._compactor._provider = new_provider
        self._updater._provider = new_provider

    # ── 核心 ──

    async def chat(
        self,
        user_msg: str,
        session_dir: Path,
        turn: int,
        conversation: list[dict],
        ui: ExecutorUI,
    ) -> ChatResult:
        """执行一轮对话。"""
        assistant_text = ""
        turn_messages: list[dict] = []
        estimated, pct = 0, 0.0

        # 1. 组装上下文
        system_msgs, trimmed_conv = await self._pipeline.assemble(
            session_dir, user_msg, conversation,
            context_providers=self._plugins.get_context_providers(),
        )
        full_messages = system_msgs + trimmed_conv

        try:
            # 2. FC 循环
            updated = await self._fc_loop.run(full_messages, ui=ui)

            # 2.5. 计数实际发送的上下文（system + trimmed + 工具消息增量 + tools）
            tools_schema = self.tool_registry.get_schemas()
            estimated, pct = compute_context_usage(
                updated, tools_schema,
                context_window=self.config.app.context_window,
            )

            # 合并对话历史（过滤 system 消息）
            older, _ = _split_conversation(conversation)
            conversation_only = [m for m in updated if m.get("role") != "system"]
            new_conversation = older + conversation_only

            # 提取 AI 回复
            for msg in reversed(updated):
                if msg.get("role") == "assistant" and msg.get("content"):
                    assistant_text = msg["content"]
                    break

            if not assistant_text:
                assistant_text = "（未收到 AI 响应，请检查 LLM 配置或稍后重试）"
                new_conversation.append({"role": "assistant", "content": assistant_text})

            # 仅本轮增量消息
            conv_before_user = len(conversation) - 1
            turn_messages = new_conversation[conv_before_user:]

        finally:
            # 无论成功还是失败，始终保存（如果还没有本轮消息，至少保存用户消息）
            if not turn_messages:
                assistant_text = assistant_text or f"（系统错误: API 调用失败）"
                new_conversation = list(conversation)
                new_conversation.append({"role": "assistant", "content": assistant_text})
                conv_before_user = len(conversation) - 1
                turn_messages = new_conversation[conv_before_user:]

            await self._ingester.ingest(
                turn=turn,
                user_msg=user_msg,
                assistant_msg=assistant_text,
                turn_messages=turn_messages,
            )

        token_usage = TokenUsage(total_tokens=estimated, context_pct=pct)

        # 4. 后台截获（不影响对话流程）
        captured = await self._capture.capture(
            user_msg=user_msg,
            assistant_msg=assistant_text,
            session_id=session_dir.name,
            turn=turn,
        )
        if captured:
            ui.on_captured_entries(captured)

        return ChatResult(
            conversation=new_conversation,
            assistant_text=assistant_text,
            captured_entries=captured,
            usage=token_usage,
        )

    # ── 会话 ──

    async def create_session(self, first_msg: str) -> tuple[SessionInfo, Path]:
        info = self._sessions.create(first_msg)
        session_dir = self._sessions._root / info.id
        return info, session_dir

    async def list_sessions(self) -> list[SessionInfo]:
        return self._sessions.list_all()

    async def delete_session(self, session_id: str) -> bool:
        return self._sessions.delete(session_id)

    def rollback_session(self, session_dir: Path, target_turn: int) -> int:
        """回滚会话到指定轮次。"""
        return self._sessions.rollback(session_dir, target_turn)

    # ── 插件 ──

    async def load_plugin(self, plugin_id: str):
        return await self._plugins.load(plugin_id)

    async def unload_plugin(self, plugin_id: str) -> bool:
        return await self._plugins.unload(plugin_id)

    def list_plugins(self):
        return self._plugins.list_loaded()

    # ── 压缩 ──

    async def compact_session(self, session_dir: Path):
        return await self._compactor.compact(session_dir)

    # ── prompt 更新 ──

    async def update_profile(self):
        return await self._updater.update_all()

    def flush_cache(self) -> None:
        self._pipeline.flush_cache()
