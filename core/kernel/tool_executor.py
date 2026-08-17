"""ToolExecutor — 工具执行器（只读并行/写串行/失败 abort 兄弟 + 超时 + 截断）。

从 FunctionCallingLoop 提取（P3：降低 fc_loop 职责），
持有 registry + hook_runner，execute_tools() 处理一轮 tool_calls。
"""

from __future__ import annotations

import asyncio
import json
import logging

from .protocols import ExecutorUI
from .safety import check_tool_safety, check_write_overwrite
from core.tools import ToolRegistry
from core.tools.truncation import truncate_output

logger = logging.getLogger(__name__)

TOOL_TIMEOUT = 30.0            # 单个工具执行超时（秒）
MCP_TOOL_TIMEOUT = 120.0       # MCP 工具超时（需匹配 transport.CALL_TIMEOUT）
DELEGATE_TOOL_TIMEOUT = 180.0  # delegate 子 agent 跑多轮 LLM，需要更长超时
_RUN_SHELL_MAX_TIMEOUT = 60.0  # run_shell 的 timeout 参数上限（与 run_shell.MAX_TIMEOUT 一致）


def _run_shell_tool_timeout(arguments: dict) -> float:
    """run_shell 外层超时 = 内部 timeout 参数 + 2s 缓冲。

    缓冲保证 run_shell 内部先超时并 kill 进程树，外层只是兜底——
    若外层先到，wait_for 取消会掐掉 execute 内部的取消处理，命令进程后台残留。
    """
    timeout = arguments.get("timeout", 30.0)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        timeout = 30.0
    return min(float(timeout), _RUN_SHELL_MAX_TIMEOUT) + 2.0
TOOL_RESULT_MAX_CHARS = 8000   # 工具结果最大字符数（超出截断）
MAX_WEB_CALLS = 10             # 单次 FC 循环中 web 工具连续失败熔断上限（成功清零）


class ToolExecutor:
    """工具执行器 — 一轮 tool_calls 的分组执行。

    分组（free-code isConcurrencySafe 分片的简化版）：
    - 串行组：有副作用工具（write_file/run_shell）+ MCP 工具，依次执行，避免写竞态
    - 并发组：其余（只读类 + 插件工具），并行执行；任一失败 → 取消其余兄弟
      （卡住/已无意义的工具不继续等，被取消者标记"已取消"喂回 LLM）

    所有错误（含取消）作为普通结果返回 — 不阻断对话，LLM 自行降级。
    每个工具独立超时、独立截断，结果顺序与 tool_calls 顺序一致。
    """

    # 网络工具名集合
    _web_tool_names: frozenset = frozenset({"web"})

    # 不可缓存的工具（有副作用，重复调用结果可能不同）
    _uncacheable_tools: frozenset = frozenset({
        "write_file", "run_shell",
    })

    def __init__(self, registry: ToolRegistry,
                 hook_runner: object | None = None) -> None:
        self.registry = registry
        self.hook_runner = hook_runner  # P7: PermissionRequest hook
        self._web_call_count = 0
        # 同轮工具结果缓存（仅缓存无副作用工具）
        self._result_cache: dict[tuple[str, str], str] = {}

    def reset(self) -> None:
        """新一轮开始时重置轮级状态（web 计数 + 同轮缓存）。"""
        self._web_call_count = 0
        self._result_cache.clear()

    # ── 主入口 ───────────────────────────────────────────────────────

    async def execute_tools(
        self,
        tool_calls: list[dict],
        ui: ExecutorUI,
    ) -> list[dict]:
        """执行工具调用 — 只读并发、写工具串行、失败 abort 兄弟。

        网络工具（web）有每轮 3 次总调用上限，
        超出后直接返回错误而不发起实际请求。
        """
        serial_idx = [i for i, tc in enumerate(tool_calls)
                      if self._is_serial_tool(self._tool_name(tc))]
        concurrent_idx = [i for i in range(len(tool_calls)) if i not in serial_idx]

        results: dict[int, dict] = {}

        # 并发组：wait 循环，任一失败取消兄弟
        if concurrent_idx:
            group = [tool_calls[i] for i in concurrent_idx]
            group_results = await self._run_concurrent_group(group, ui)
            results.update(zip(concurrent_idx, group_results))

        # 串行组：依次执行（前一个完成后再跑下一个）
        for i in serial_idx:
            _, result = await self._run_one(tool_calls[i], ui)
            results[i] = result

        # 按原 tool_calls 顺序重组
        return [results[i] for i in range(len(tool_calls))]

    @staticmethod
    def _tool_name(tc: dict) -> str:
        return tc.get("function", {}).get("name", "unknown")

    @classmethod
    def _is_serial_tool(cls, tool_name: str) -> bool:
        """串行执行判定：有副作用工具（写文件/跑命令）与 MCP 工具。

        并发组 = 其余（只读类 + 插件工具）。写工具同轮并发会竞态
        （多个 write_file 写同一文件 / 有顺序依赖的 run_shell），故串行。
        """
        return tool_name in cls._uncacheable_tools or tool_name.startswith("mcp_")

    # ── 并发组 ──────────────────────────────────────────────────────

    async def _run_concurrent_group(
        self,
        group: list[dict],
        ui: ExecutorUI,
    ) -> list[dict]:
        """并行执行组内工具；任一失败 → 取消其余兄弟。

        Returns:
            结果列表，顺序与 group 一致。
        """
        if not group:
            return []
        tasks = {
            asyncio.create_task(self._run_one(tc, ui)): idx
            for idx, tc in enumerate(group)
        }
        pending = set(tasks)
        results: dict[int, dict] = {}

        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED,
            )
            # 先收集本轮所有已完成的结果（含与失败同时完成的兄弟，避免漏收）
            failed = False
            for t in done:
                idx = tasks[t]
                ok, result = t.result()
                results[idx] = result
                failed = failed or not ok
            if failed:
                # 失败：取消其余仍在跑的兄弟（其结果已无意义）
                for p in pending:
                    p.cancel()
                for p in pending:
                    idx2 = tasks[p]
                    name2 = self._tool_name(group[idx2])
                    ui.on_tool_error(name2, "同轮某工具失败，已取消")
                    results[idx2] = {
                        "content": "工具已取消（同轮某工具失败）",
                        "tool_id": group[idx2].get("id", ""),
                    }
                    try:
                        await p  # 清理被取消的 task，避免 pending-destroyed 警告
                    except asyncio.CancelledError:
                        pass
                pending = set()
                break

        return [results[i] for i in range(len(group))]

    # ── 单个工具 ────────────────────────────────────────────────────

    async def _run_one(
        self,
        tc: dict,
        ui: ExecutorUI,
    ) -> tuple[bool, dict]:
        """执行单个工具调用。返回 (ok, result_dict)。

        ok=False 表示失败/超时/高危阻止/网络限流 —— 触发并发组兄弟取消。
        所有结果（含错误）仍作为 tool 消息喂回 LLM 自行降级。
        """
        func = tc.get("function", {})
        tool_name = func.get("name", "unknown")
        tool_id = tc.get("id", "")
        arguments = self._parse_args(func.get("arguments", "{}"))

        # ── 网络工具限流检查（连续失败熔断：只检查计数，执行后按结果更新）──
        # 计数 == MAX 表示已连续失败 MAX 次 → 拒绝本次（本轮剩余调用）
        if tool_name in self._web_tool_names and self._web_call_count >= MAX_WEB_CALLS:
            result = f"错误：网络调用已达上限（{MAX_WEB_CALLS} 次），请基于已有信息回复。"
            ui.on_tool_error(tool_name, result)
            return False, {"content": result, "tool_id": tool_id}

        # ── 高危工具检查（BLOCKED 状态 + PermissionRequest hook）──
        if blocked_reason := await self._should_block(tool_name, arguments):
            ui.on_tool_error(tool_name, f"[警告] {blocked_reason}")
            result = (
                f"⚠️ 高风险操作已被阻止: {blocked_reason}\n"
                f"请选择替代方案或向用户说明风险后请求确认。"
            )
            return False, {"content": result, "tool_id": tool_id}

        # MCP 工具使用更长的超时（匹配 MCP CALL_TIMEOUT=120s）
        # delegate 子 agent 跑多轮 LLM，需要更长的超时
        # run_shell 外层 = 内部 timeout + 2s 缓冲：让 run_shell 内部先超时并 kill 进程树，
        # 否则外层 wait_for 取消会掐掉 execute 的取消处理，命令进程残留后台
        tool_timeout = (
            MCP_TOOL_TIMEOUT if tool_name.startswith("mcp_")
            else DELEGATE_TOOL_TIMEOUT if tool_name == "delegate"
            else _run_shell_tool_timeout(arguments) if tool_name == "run_shell"
            else TOOL_TIMEOUT
        )

        # write_file 覆盖已有文件的警告（不阻止，执行成功后附加到结果提示 LLM）
        overwrite_warning = (
            check_write_overwrite(arguments) if tool_name == "write_file" else None
        )

        # ── 同轮缓存：相同工具+参数直接返回缓存（仅无副作用工具）──
        cacheable = tool_name not in self._uncacheable_tools
        cache_key = ""
        if cacheable:
            cache_key = (tool_name, json.dumps(arguments, sort_keys=True, ensure_ascii=False))
            if cache_key in self._result_cache:
                cached = self._result_cache[cache_key]
                ui.on_tool_start(tool_name, arguments)
                ui.on_tool_done(tool_name, cached)
                # 缓存命中按缓存结果判定成败（缓存可能也是失败结果，不能一律清零）
                self._update_web_count(
                    tool_name, success=not cached.startswith(("错误：", "Error:")))
                return True, {"content": cached, "tool_id": tool_id}

        ui.on_tool_start(tool_name, arguments)

        try:
            result = await asyncio.wait_for(
                self.registry.execute(tool_name, arguments),
                timeout=tool_timeout,
            )
        except asyncio.TimeoutError:
            result = f"错误：工具 {tool_name} 执行超时（{tool_timeout}s）"
            ui.on_tool_error(tool_name, result)
            self._update_web_count(tool_name, success=False)
            return False, {"content": result, "tool_id": tool_id}
        except Exception as e:
            logger.exception(f"工具 {tool_name} 执行异常")
            result = f"工具执行异常：{e}"
            ui.on_tool_error(tool_name, result)
            self._update_web_count(tool_name, success=False)
            return False, {"content": result, "tool_id": tool_id}
        else:
            # 截断过长结果
            result = self._truncate_result(result)
            # write_file 覆盖已有文件时前置风险提示（不阻断执行，喂回 LLM 决策）
            if overwrite_warning:
                result = (f"[提示] {overwrite_warning}\n\n{result}"
                          if result else f"[提示] {overwrite_warning}")
            # 缓存无副作用工具的结果（同轮重复调用直接返回）
            if cacheable:
                self._result_cache[cache_key] = result
            ui.on_tool_done(tool_name, result)
            # web 工具成败更新：成功（非错误前缀）清零，失败累计
            self._update_web_count(
                tool_name, success=not result.startswith(("错误：", "Error:")))

        return True, {"content": result, "tool_id": tool_id}

    def _update_web_count(self, tool_name: str, success: bool) -> None:
        """web 工具连续失败熔断计数：成功清零，失败 +1。

        原逻辑按调用总数累计，3 次后永久卡死（即使成功过）。改为连续失败熔断：
        一旦某次成功，计数清零恢复可用；仅连续失败达到上限才拒绝。
        """
        if tool_name not in self._web_tool_names:
            return
        if success:
            self._web_call_count = 0
        else:
            self._web_call_count += 1

    # ── 结果截断 ──────────────────────────────────────────────────

    @staticmethod
    def _truncate_result(result: str) -> str:
        """截断过长的工具结果，避免撑爆 LLM 上下文。"""
        return truncate_output(
            result, TOOL_RESULT_MAX_CHARS,
            label=(
                f"输出 {len(result)} 字符，仅展示前 {TOOL_RESULT_MAX_CHARS} 字符。"
                "用更精确的工具参数或 head/tail/grep 获取其余部分"
            ),
        )

    # ── 安全与参数 ──────────────────────────────────────────────────

    async def _should_block(self, tool_name: str, arguments: dict) -> str | None:
        """检查工具调用是否高风险。委托给 core.kernel.safety.check_tool_safety。

        P7: 触发 PermissionRequest hook — 插件可审批高风险操作。
        """
        reason = check_tool_safety(tool_name, arguments)
        if reason is None:
            return None

        # ── P7: PermissionRequest hook — 插件可覆盖阻止决定 ──
        if self.hook_runner is not None:
            try:
                from core.plugins.hook_runner import HookContext, check_hook_results
                ctx = HookContext(
                    event="PermissionRequest",
                    tool_name=tool_name,
                    tool_args=arguments,
                )
                results = await self.hook_runner.run("PermissionRequest", ctx)
                ok, msg, modified = check_hook_results(results)
                if ok and modified:
                    # 插件批准了操作（modified_input 存在表示审批）
                    return None
                if not ok:
                    return f"{reason}（插件审批未通过: {msg}）"
            except Exception:
                pass

        return reason

    @staticmethod
    def _parse_args(raw_args: str | dict) -> dict:
        """解析工具参数（JSON 字符串或已解析的 dict）。"""
        if isinstance(raw_args, str):
            try:
                return json.loads(raw_args)
            except json.JSONDecodeError:
                return {}
        return raw_args
