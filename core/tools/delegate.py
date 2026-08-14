"""delegate — 子 agent 委托工具。

主 agent 把子任务打包，派给一个上下文独立的子 agent 跑受限 FC 循环，
只返回压缩后的结论。子 agent 一次性、用完即删：不持久化、不摄入、不建会话。

默认只读工具白名单，主 agent 可通过 tools 参数覆盖；delegate 自身永远禁止递归
（子 agent 的 ToolRegistry 根本不含 delegate，调用必然 not_found）。
"""

from __future__ import annotations

import asyncio

from core.locale import t
from .definition import ToolDefinition
from core.tools.truncation import truncate_output

# 子 agent 默认只读工具白名单（可被主 agent 的 tools 参数覆盖）
READ_ONLY_TOOLS = ("read_file", "search_in_files", "search_chat", "search_memory", "web")

# 子 agent 独立轮数上限（防止一个子任务拖垮主对话）
SUBAGENT_MAX_TURNS = 6

# 结论返回的字符上限（超出截断）
SUBAGENT_RESULT_MAX_CHARS = 4000

# 子 agent 同时并发的上限（计数限流，超出的调用直接拒绝）
MAX_CONCURRENT_SUBAGENTS = 3
_active_subagents = 0


def _clamp(value, lo: int, hi: int, default: int) -> int:
    """将 value 限制在 [lo, hi]，非整数时返回 default。"""
    if not isinstance(value, int):
        return default
    return max(lo, min(hi, value))


def _queue_status() -> str:
    """返回当前子 agent 队列情况（编排前查询，供主 agent 二次确认派发数量）。"""
    active = _active_subagents
    return t(
        "tool.delegate.queue_status",
        max=MAX_CONCURRENT_SUBAGENTS,
        active=active,
        available=MAX_CONCURRENT_SUBAGENTS - active,
    )


async def execute(arguments: dict, ctx=None) -> str:
    """delegate 工具 — 派生子 agent 执行子任务，返回结论。

    Args:
        arguments: {
            "action": str       — "run"=委托子任务（默认），"status"=查询子 agent 队列情况
            "prompt": str       — 要委托给子 agent 的任务描述（action=run 时需要）
            "tools": list[str]  — 允许子 agent 使用的工具名（可选，默认只读类）
            "max_turns": int    — 子 agent 最大轮数（可选，默认 6，最大 10）
        }
        ctx: ToolContext（由 ToolRegistry 自动注入，含 provider/tool_registry/hook_runner）

    Returns:
        action=run：子 agent 的结论文本（截断到 ~4000 字符）
        action=status：当前子 agent 队列情况
    """
    action = arguments.get("action", "run").strip()

    # 编排前查询：主 agent 先看队列情况，再决定派发几个子任务（二次确认）
    if action == "status":
        return _queue_status()
    if action != "run":
        return t("tool.delegate.unknown_action", action=action)

    prompt = arguments.get("prompt", "").strip()
    if not prompt:
        return t("tool.delegate.empty_prompt")

    # 递归保护：白名单永远排除 delegate 自身
    allowed = list(arguments.get("tools") or READ_ONLY_TOOLS)
    allowed = [n for n in allowed if n != "delegate"]

    provider = getattr(ctx, "provider", None) if ctx else None
    registry = getattr(ctx, "tool_registry", None) if ctx else None
    hook_runner = getattr(ctx, "hook_runner", None) if ctx else None
    if provider is None or registry is None:
        return t("tool.delegate.unavailable")

    # 延迟导入避免循环：core.tools → delegate → kernel.fc_loop → core.tools
    from core.tools import ToolRegistry
    from core.kernel.fc_loop import FunctionCallingLoop
    from core.kernel.protocols import NullUI

    # 过滤白名单 → 子 agent 专属 ToolRegistry（不含 delegate，天然禁止递归）
    sub_registry = ToolRegistry()
    for name in allowed:
        td = registry.get(name)
        if td:
            sub_registry.register(td)
    sub_registry.tool_context = ctx          # 共享 search_index 等
    sub_registry.hook_runner = hook_runner   # PermissionRequest 在子 agent 也生效

    max_turns = _clamp(arguments.get("max_turns"), 1, 10, SUBAGENT_MAX_TURNS)
    sub_messages = [
        {"role": "system", "content": t("tool.delegate.subagent_system")},
        {"role": "user", "content": prompt},
    ]

    loop = FunctionCallingLoop(
        provider, sub_registry,
        max_turns=max_turns,
        hook_runner=hook_runner,
    )

    # 并发限流：最多同时跑 MAX_CONCURRENT_SUBAGENTS 个子 agent，超出直接拒绝
    # 检查 + 递增之间无 await，在 asyncio 单线程下是原子的，不会 race
    global _active_subagents
    if _active_subagents >= MAX_CONCURRENT_SUBAGENTS:
        return t("tool.delegate.concurrent_limit", max=MAX_CONCURRENT_SUBAGENTS)
    _active_subagents += 1
    try:
        updated = await loop.run(sub_messages, ui=NullUI())
    except asyncio.CancelledError:
        raise  # 取消传播（主 agent Ctrl+C → gather → 子 agent）
    except Exception as e:
        return t("tool.delegate.failed", e=e)
    finally:
        _active_subagents -= 1

    # 提取最后一条 assistant 文本作为结论（复用 _merge_updated 的模式）
    conclusion = ""
    for msg in reversed(updated):
        if msg.get("role") == "assistant" and msg.get("content"):
            conclusion = msg["content"]
            break

    # SubagentStop hook（9 事件里最后一个未接线的）
    if hook_runner is not None:
        try:
            from core.plugins.hook_runner import HookContext
            await hook_runner.run("SubagentStop", HookContext(
                event="SubagentStop",
                session_id=getattr(ctx, "current_session_id", "") or "",
                user_prompt=prompt,
            ))
        except Exception:
            pass

    if not conclusion:
        conclusion = t("tool.delegate.no_conclusion")
    return truncate_output(conclusion, SUBAGENT_RESULT_MAX_CHARS)


# ── JSON Schema ───────────────────────────────────────────────────

schema = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["run", "status"],
            "description": (
                "run=委托子任务；status=查询当前子 agent 队列情况"
                "（编排前先查，确认当前可用配额后再决定派发几个子任务）。"
            ),
        },
        "prompt": {
            "type": "string",
            "description": "要委托给子 agent 完成的任务描述（action=run 时需要）。子 agent 有独立上下文，完成后返回结论。",
        },
        "tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "允许子 agent 使用的工具名列表（可选，默认只读类："
                "read_file / search_in_files / search_chat / search_memory / web）。"
                "delegate 自身始终被排除，禁止递归。"
            ),
        },
        "max_turns": {
            "type": "integer",
            "description": "子 agent 最大轮数（可选，默认 6，最大 10）",
        },
    },
    "required": ["action"],
}


definition = ToolDefinition(
    name="delegate",
    description=t("tool_desc.delegate"),
    parameters=schema,
    execute=execute,
)
