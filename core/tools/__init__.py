"""工具层 — ToolRegistry + 声明式工具清单。

ToolRegistry 维护 name → ToolDefinition 映射，
提供 get_schemas() 返回 OpenAI function calling 格式的 tools 数组。
P4 Batch 2: 集成重试机制（core.tools.retry），瞬态错误自动指数退避重试。
P6: ToolContext 注入 — 工具可通过 ctx 访问 SearchIndex、sessions_root 等共享服务。
P8: 声明式清单 — ToolDefinition/ToolContext 移至 definition.py，工具模块自包含声明。
"""

from __future__ import annotations

import logging

from .definition import ToolDefinition, ToolContext
from .retry import RetryConfig, async_retry
from . import read_file, write_file, run_shell, search_memory, web, search_in_files, search_chat
from core.locale import t

logger = logging.getLogger(__name__)


# ── ToolRegistry ────────────────────────────────────────────────────────

class ToolRegistry:
    """工具注册中心。

    注册所有内置工具，提供按 name 查找、生成 OpenAI tools schema、
    以及带重试的工具执行。

    瞬态错误（网络/超时）自动指数退避重试，永久错误（权限/不存在）立即返回。

    P6: 新增 tool_context 属性，execute() 时自动注入到工具函数。
    """

    def __init__(self, default_retry: RetryConfig | None = None) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self.default_retry = default_retry or RetryConfig()
        self.tool_context: ToolContext = ToolContext()
        self.hook_runner: object | None = None  # P7: HookRunner | None

    def register(self, tool: ToolDefinition) -> None:
        """注册一个工具。同名工具后注册的覆盖先注册的。"""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """移除一个工具。返回 True 表示成功移除。"""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def get(self, name: str) -> ToolDefinition | None:
        """按名称获取工具定义。"""
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        """返回所有已注册工具名称。"""
        return list(self._tools.keys())

    def get_schemas(self) -> list[dict]:
        """返回 OpenAI function calling 格式的 tools 数组。

        Returns:
            [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}, ...]
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    async def execute(self, name: str, arguments: dict) -> str:
        """执行指定工具（含重试），自动注入 ToolContext。

        P7: PreToolUse/PostToolUse hooks 在工具执行前后触发。
        瞬态错误（网络/超时）自动指数退避重试，永久错误立即返回。

        Args:
            name: 工具名
            arguments: LLM 返回的参数字典

        Returns:
            工具执行结果字符串。失败时返回 "错误：..." 描述。
        """
        tool = self._tools.get(name)
        if tool is None:
            return t("tool.registry.not_found", name=name, tools=', '.join(self.list_names()))
        if tool.execute is None:
            return t("tool.registry.no_execute", name=name)

        # P7: PreToolUse hook — exit 2 或 decision="block" 时阻止执行
        file_path = arguments.get("file_path", arguments.get("filepath", ""))
        pre_ok, pre_msg = await self._fire_tool_hook("PreToolUse", name, arguments, file_path)
        if not pre_ok:
            return t("tool.registry.hook_blocked", name=name, reason=pre_msg)

        retry_cfg = tool.retry or self.default_retry
        ctx = self.tool_context

        async def _call() -> str:
            # 探测工具是否接受 ctx 参数（兼容旧签名）
            import inspect
            try:
                sig = inspect.signature(tool.execute)
                if 'ctx' in sig.parameters:
                    return await tool.execute(arguments, ctx=ctx)
            except (ValueError, TypeError):
                pass
            return await tool.execute(arguments)

        result = await async_retry(_call, config=retry_cfg, tool_name=name)

        # P7: PostToolUse hook
        await self._fire_tool_hook("PostToolUse", name, arguments, file_path)

        return result

    async def _fire_tool_hook(self, event: str, tool_name: str,
                              arguments: dict, file_path: str = "") -> tuple[bool, str]:
        """P7: 触发工具级 hook 事件。

        Returns:
            (should_proceed, message) — PreToolUse 被阻止时返回 (False, reason)。
        """
        if self.hook_runner is None:
            return True, ""
        try:
            from core.plugins.hook_runner import HookContext
            ctx = HookContext(
                event=event,
                tool_name=tool_name,
                tool_args=arguments,
                file_path=file_path,
                session_id=self.tool_context.current_session_id or "",
            )
            results = await self.hook_runner.run(event, ctx)
            from core.plugins.hook_runner import check_hook_results
            ok, msg, _ = check_hook_results(results)
            if not ok:
                logger.warning(f"{event} hook 阻止了 {tool_name}: {msg}")
                return False, msg
            return True, ""
        except Exception:
            logger.debug(f"Hook {event} 异常 (tool={tool_name})", exc_info=True)
            return True, ""
