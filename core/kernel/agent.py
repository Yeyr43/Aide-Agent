"""AgentKernel — Aide 内核门面。

编排 6 个子组件，不实现逻辑，每个方法 ≤ 10 行。
P4 Batch 2: KernelContext 聚合 14 个依赖为单一注入参数。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .context import KernelContext
from .protocols import ExecutorUI, ChatResult, TokenUsage
from .fc_loop import FunctionCallingLoop
from .middleware import ChatContext, MiddlewareRunner

from core.context.relevance import _split_conversation
from core.context.token_counter import compute_context_usage
from core.errors import AideError, ProviderError
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
        # 解构 KernelContext 为独立属性
        self.config = ctx.config
        self.provider = ctx.provider
        self.tool_registry = ctx.tooling.tool_registry
        self.command_registry = ctx.tooling.command_registry
        self._pipeline = ctx.session.context_pipeline
        self._ingester = ctx.session.ingester
        self._sessions = ctx.session.session_manager
        self._reflector = ctx.memory.reflector
        self._feedback = ctx.memory.feedback_verifier
        self._auto_memory = ctx.memory.auto_memory
        self._plugins = ctx.tooling.plugin_host
        self._slots = ctx.tooling.slot_registry
        self._fc_loop = FunctionCallingLoop(
            ctx.provider, ctx.tooling.tool_registry,
            max_turns=ctx.config.app.max_turns,
            hook_runner=ctx.hook_runner,
        )
        self._runner = MiddlewareRunner()  # 插件可扩展
        self._hook_runner = ctx.hook_runner  # P7: 生命周期钩子

    # ── 运行时 provider 切换 ──

    def set_provider(self, new_provider) -> None:
        """切换 provider（用于 /model 命令或冷启动重载）。

        统一更新内核及其子组件的 provider 引用。
        """
        self.provider = new_provider
        self._fc_loop.provider = new_provider
        self._reflector._provider = new_provider
        if self._auto_memory is not None:
            self._auto_memory._provider = new_provider
        # 同步子 agent delegate 工具的 provider 引用
        if self.tool_registry.tool_context:
            self.tool_registry.tool_context.provider = new_provider

    # ── 核心 ──

    async def chat(
        self,
        user_msg: str,
        session_dir: Path,
        turn: int,
        conversation: list[dict],
        ui: ExecutorUI,
    ) -> ChatResult:
        """执行一轮对话 — 中间件编排 6 步管线：

        1. UserPromptSubmit hook → before_context → 上下文组装 → after_context
        2. before_fc_loop → FC 循环 → after_fc_loop
        3. 摄入保存 → Token 计数 → 反馈验证 → Stop hook（固化，不走中间件）
        """
        # P7: UserPromptSubmit hook
        session_id = session_dir.name if session_dir else ""
        await self._fire_hook("UserPromptSubmit", user_prompt=user_msg,
                              session_id=session_id, turn=turn)

        ctx = ChatContext(
            user_msg=user_msg,
            session_dir=session_dir,
            turn=turn,
            conversation=conversation,
            ui=ui,
        )

        # ── 1. 上下文组装（含 before/after hook）──
        ctx = await self._runner.before_context(ctx)

        # P7: PreCompact hook（上下文组装前，插件可预处理压缩）
        await self._fire_hook("PreCompact", user_prompt=user_msg,
                              session_id=session_id, turn=turn)

        system_msgs, trimmed_conv = await self._assemble_context(
            ctx.session_dir, ctx.user_msg, ctx.conversation,
        )
        ctx.system_messages = system_msgs
        full_messages = system_msgs + trimmed_conv

        ctx = await self._runner.after_context(ctx)
        full_messages = ctx.system_messages + trimmed_conv

        # ── 2. FC 循环（含 before/after hook）──
        ctx = await self._runner.before_fc_loop(ctx)

        assistant_text, new_conversation, turn_messages, thinking = (
            await self._run_and_merge(ctx.conversation, full_messages, ctx.ui)
        )
        ctx.assistant_text = assistant_text
        ctx.new_conversation = new_conversation
        ctx.turn_messages = turn_messages
        ctx.thinking = thinking

        ctx = await self._runner.after_fc_loop(ctx)

        # ── 3. 固化步骤（框架级义务，不走中间件）──
        self._ingester.set_session(str(ctx.session_dir))
        # P7: 同步 ToolContext.session_id（hook 环境变量注入用）
        if self.tool_registry.tool_context:
            self.tool_registry.tool_context.current_session_id = str(ctx.session_dir.name) if ctx.session_dir else None
        await self._ingester.ingest(
            turn=ctx.turn,
            user_msg=ctx.user_msg,
            assistant_msg=ctx.assistant_text,
            thinking=ctx.thinking,
            turn_messages=ctx.turn_messages,
        )

        tools_schema = self.tool_registry.get_schemas()
        estimated, pct = compute_context_usage(
            ctx.new_conversation, tools_schema,
            context_window=self.config.app.context_window,
        )

        if self._feedback:
            try:
                mem_fragments = self._pipeline.get_last_memory_fragments()
                if mem_fragments:
                    self._feedback.verify(
                        fragments=mem_fragments,
                        assistant_text=ctx.assistant_text,
                        user_msg=ctx.user_msg,
                        turn_messages=ctx.turn_messages,
                        session_id=ctx.session_dir.name,
                        turn=ctx.turn,
                    )
            except Exception:
                logger.debug("反馈验证异常", exc_info=True)

        # P7: Stop hook
        await self._fire_hook("Stop", user_prompt=user_msg,
                              session_id=session_id, turn=turn,
                              assistant_text=ctx.assistant_text)

        # P7: Notification hook（通用系统通知）
        await self._fire_hook("Notification", user_prompt=user_msg,
                              session_id=session_id, turn=turn)

        # 自动记忆提取（fire-and-forget：不阻塞响应，内部异常全静默）
        if self._auto_memory is not None:
            asyncio.create_task(self._auto_memory.maybe_extract(
                ctx.session_dir, ctx.turn, ctx.user_msg,
                ctx.assistant_text, ctx.turn_messages,
            ))

        return ChatResult(
            conversation=ctx.new_conversation,
            assistant_text=ctx.assistant_text,
            usage=TokenUsage(total_tokens=estimated, context_pct=pct),
        )

    # ── P7: Hook 辅助 ─────────────────────────────────────────────────

    async def _fire_hook(self, event: str, *, user_prompt: str = "",
                         session_id: str = "", turn: int = 0,
                         assistant_text: str = "",
                         tool_name: str = "", tool_args: dict = None,
                         file_path: str = "") -> None:
        """触发生命周期 hook 事件。"""
        if self._hook_runner is None:
            return
        try:
            from core.plugins.hook_runner import HookContext
            ctx = HookContext(
                event=event,
                tool_name=tool_name,
                tool_args=tool_args,
                file_path=file_path,
                session_id=session_id,
                turn=turn,
                user_prompt=user_prompt,
            )
            results = await self._hook_runner.run(event, ctx)
            # Stop/UserPromptSubmit hooks 的阻止结果仅记录日志
            for r in results:
                if r.exit_code != 0:
                    logger.debug(f"Hook {event}: exit={r.exit_code} {r.stderr[:100]}")
        except Exception:
            logger.debug(f"Hook {event} 异常", exc_info=True)

    # ── chat() 子步骤 ──

    async def _assemble_context(
        self,
        session_dir: Path | None,
        user_msg: str,
        conversation: list[dict],
    ) -> tuple[list[dict], list[dict]]:
        """组装上下文：system messages + 裁剪后的对话。"""
        tool_descriptions = [
            t.description for t in self.tool_registry._tools.values()
        ] if self.tool_registry._tools else None
        return await self._pipeline.assemble(
            session_dir, user_msg, conversation,
            context_providers=self._plugins.get_context_providers(),
            tool_descriptions=tool_descriptions,
        )

    async def _run_and_merge(
        self,
        conversation: list[dict],
        full_messages: list[dict],
        ui: ExecutorUI,
    ) -> tuple[str, list[dict], list[dict], str]:
        """执行 FC 循环 → 合并对话历史 → 提取 AI 回复。

        Returns:
            (assistant_text, new_conversation, turn_messages, thinking)
        """
        assistant_text = ""
        turn_messages: list[dict] = []
        thinking = ""

        try:
            updated = await self._fc_loop.run(full_messages, ui=ui)
            assistant_text, new_conversation, turn_messages = (
                self._merge_updated(conversation, updated)
            )
        except ProviderError as e:
            logger.warning("Provider 调用失败: %s (status=%s)", e, e.status_code)
            assistant_text = f"（LLM 服务异常: {e}）"
        except AideError as e:
            logger.warning("内核异常: %s", e)
            assistant_text = f"（系统异常: {e}）"
        except Exception:
            logger.exception("未预期的内核异常")
            assistant_text = "（系统错误: API 调用失败）"
        finally:
            # 异常兜底：始终保存（至少写入用户消息）
            if not turn_messages:
                new_conversation = list(conversation)
                new_conversation.append({"role": "assistant", "content": assistant_text})
                conv_before_user = len(conversation) - 1
                turn_messages = new_conversation[conv_before_user:]
            # 异常前已流出的思考内容也应保留（部分是细节）
            thinking = self._fc_loop.thinking

        return assistant_text, new_conversation, turn_messages, thinking

    @staticmethod
    def _merge_updated(
        conversation: list[dict],
        updated: list[dict],
    ) -> tuple[str, list[dict], list[dict]]:
        """合并 FC 循环输出到对话历史——过滤 system 消息、提取 AI 回复。

        Returns:
            (assistant_text, new_conversation, turn_messages)
        """
        older, _ = _split_conversation(conversation)
        conversation_only = [m for m in updated if m.get("role") != "system"]
        new_conversation = older + conversation_only

        # 提取最后一条 assistant 消息
        assistant_text = ""
        for msg in reversed(updated):
            if msg.get("role") == "assistant" and msg.get("content"):
                assistant_text = msg["content"]
                break

        if not assistant_text:
            assistant_text = "（未收到 AI 响应，请检查 LLM 配置或稍后重试）"
            new_conversation.append({"role": "assistant", "content": assistant_text})

        conv_before_user = len(conversation) - 1
        turn_messages = new_conversation[conv_before_user:]

        return assistant_text, new_conversation, turn_messages

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

    # ── 反思 ──

    async def reflect(self, session_dir: Path, current_turn: int):
        """统一的记忆反思 + 会话压缩。"""
        return await self._reflector.reflect(session_dir, current_turn)

    async def apply_reflection(self, session_dir: Path, result, current_turn: int) -> None:
        """应用反思结果。"""
        await self._reflector.apply(session_dir, result, current_turn)

    def flush_cache(self) -> None:
        self._pipeline.flush_cache()
