"""Function Calling 循环引擎。

组装上下文 → LLM 决定（tool_call 或 reply）→ 并行调工具 → 结果喂回 → 循环。

硬编码 max_turns=5，达到上限后自动给 LLM 一次纯文本回复机会。
工具错误不作为阻断信号，全部喂回 LLM 让其自行降级。

P4: XML fallback 解析 + 工具结果截断 + 超时保护 + 并行执行 + 无阻断循环。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from .protocols import ExecutorUI
from .safety import check_tool_safety, check_write_overwrite, strip_quoted
from .xml_tool_parser import extract_xml_tool_calls, try_parse_xml
from core.tools import ToolRegistry
from core.tools.truncation import truncate_output
from core.llm_gateway import TextDelta, ThinkingDelta, StreamEnd
from core.errors import ProviderError

logger = logging.getLogger(__name__)

DEFAULT_MAX_TURNS = 10


def _sanitize_messages(messages: list[dict],
                      supports_vision: bool = False) -> list[dict]:
    """清洗消息列表：非视觉模型需将多模态 content（list）转为纯文本。

    视觉模型（gpt-4o、claude-3+、gemini-1.5+ 等）保留 content 数组格式，
    做深拷贝确保 Provider 侧的转换不会污染原始 conversation。

    Args:
        messages: 对话历史列表
        supports_vision: 模型是否支持图片输入（True 保留多模态格式）

    Returns:
        新列表，不修改输入的 dict（深拷贝）
    """
    if supports_vision:
        # 视觉模型：深拷贝，防止 Provider 转换时修改原始 conversation
        import copy
        return copy.deepcopy(messages)

    sanitized: list[dict] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            text_parts = [
                p.get("text", "") for p in content
                if p.get("type") == "text"
            ]
            has_image = any(
                p.get("type") == "image_url" for p in content
            )
            txt = " ".join(text_parts)
            if has_image:
                txt = f"{txt}\n[图片]" if txt else "[图片]"
            sanitized.append({**msg, "content": txt})
        else:
            sanitized.append(msg)
    return sanitized
TOOL_TIMEOUT = 30.0            # 单个工具执行超时（秒）
MCP_TOOL_TIMEOUT = 120.0       # MCP 工具超时（需匹配 transport.CALL_TIMEOUT）
DELEGATE_TOOL_TIMEOUT = 180.0  # delegate 子 agent 跑多轮 LLM，需要更长超时
TOOL_RESULT_MAX_CHARS = 8000   # 工具结果最大字符数（超出截断）
MAX_WEB_CALLS = 3              # 单次 FC 循环中 web 工具总调用上限


@dataclass
class _TurnResult:
    """单轮 LLM 调用的结果。"""
    stream_end: StreamEnd
    response_text: str


# ── Function Calling 循环 ─────────────────────────────────────────

class FunctionCallingLoop:
    """Function Calling 循环引擎。

    用法:
        loop = FunctionCallingLoop(provider, registry)
        await loop.run(conversation, ui)
    """

    # 网络工具名集合（类级常量）
    _web_tool_names: frozenset = frozenset({"web"})

    # 不可缓存的工具（有副作用，重复调用结果可能不同）
    _uncacheable_tools: frozenset = frozenset({
        "write_file", "run_shell",
    })

    def __init__(self, provider, tool_registry: ToolRegistry,
                 max_turns: int = DEFAULT_MAX_TURNS,
                 hook_runner: object | None = None) -> None:
        self.provider = provider
        self.registry = tool_registry
        self.max_turns = max_turns
        self.supports_vision: bool = getattr(provider, 'supports_vision', False)
        self._web_call_count = 0
        self.hook_runner = hook_runner  # P7: PermissionRequest hook
        # 同轮工具结果缓存（仅缓存无副作用工具）
        self._result_cache: dict[tuple[str, str], str] = {}
        self._thinking_buffer = ""  # 本轮累计思考内容（持久化恢复用）

    async def run(
        self,
        messages: list[dict],
        ui: ExecutorUI,
    ) -> list[dict]:
        """执行 function calling 循环。

        Args:
            messages: 当前对话历史（会原地修改，追加 assistant/tool 消息）
            ui: UI 回调接口

        Returns:
            更新后的 messages 列表
        """
        self._web_call_count = 0
        self._result_cache.clear()
        self._thinking_buffer = ""  # 新一轮开始时重置
        tools_schema = self.registry.get_schemas()
        final: StreamEnd | None = None
        turn = 0

        for turn in range(1, self.max_turns + 1):
            result = await self._call_llm(messages, tools_schema, ui)
            if result is None:
                # LLM 调用失败 → 终止循环（非工具错误，是 Provider 层故障）
                break

            final = result.stream_end

            # ── 无 tool_calls：检查 XML fallback → 正常回复 ──────
            xml_calls: list[dict] = []
            if not final.tool_calls:
                xml_calls = self._extract_xml_tool_calls(result.response_text)
                if xml_calls:
                    final.tool_calls = xml_calls
                    xml_start = result.response_text.find("<invoke")
                    text_content = result.response_text[:xml_start].strip() if xml_start > 0 else None
                    messages.append({
                        "role": "assistant",
                        "content": text_content or "",
                        "tool_calls": final.tool_calls,
                    })
                    # 跳过下方通用的 messages.append，直接进入工具执行
                else:
                    messages.append({
                        "role": "assistant",
                        "content": result.response_text,
                    })
                    # P6: 若模型因 max_tokens 被截断，自动续写
                    # Anthropic 原生 stop_reason="max_tokens"；OpenAI 兼容系（DeepSeek/
                    # Ollama）为 finish_reason="length"（经 provider.py 透传），两者都要兼容
                    if (final.native_stop_reason in ("max_tokens", "length")
                            and turn < self.max_turns):
                        messages.append({"role": "user", "content": "(continue)"})
                        continue
                    break

            # ── 有 tool_calls：并行执行 → 错误喂回 LLM ──────────
            if final.tool_calls:
                # 原生 tool_calls 路径：追加 assistant 消息
                if not xml_calls:
                    messages.append({
                        "role": "assistant",
                        "content": result.response_text or "",
                        "tool_calls": final.tool_calls,
                    })

            tool_results = await self._execute_tools(final.tool_calls, ui)

            # 所有工具结果（含错误）都作为 tool 消息喂给 LLM，
            # 让 LLM 自己决定降级策略。MAX_TURNS 自然终止。
            for tc, tool_result in zip(final.tool_calls, tool_results):
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": tool_result["content"],
                })

        # ── 循环结束检查 ──────────────────────────────────────────
        if turn >= self.max_turns and final and final.tool_calls:
            result = await self._call_llm(messages, [], ui)
            if result and not result.stream_end.tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": result.response_text,
                })
            else:
                ui.on_max_turns()

        return messages

    @property
    def thinking(self) -> str:
        """本轮 FC 循环累计的思考内容（持久化到 turn 文件供恢复显示）。"""
        return self._thinking_buffer

    # ── 工具执行（并行 + 超时 + 截断） ──────────────────────────

    async def _execute_tools(
        self,
        tool_calls: list[dict],
        ui: ExecutorUI,
    ) -> list[dict]:
        """执行工具调用 — 只读并发、写工具串行、失败 abort 兄弟。

        每个工具独立超时、独立截断。返回顺序与 tool_calls 顺序一致。

        分组（free-code isConcurrencySafe 分片的简化版）：
        - 串行组：有副作用工具（write_file/run_shell）+ MCP 工具，依次执行，避免写竞态
        - 并发组：其余（只读类 + 插件工具），并行执行；任一失败 → 取消其余兄弟
          （卡住/已无意义的工具不继续等，被取消者标记"已取消"喂回 LLM）

        所有错误（含取消）作为普通结果返回 — 不阻断对话，LLM 自行降级。

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

        # ── 网络工具限流检查 ──
        if tool_name in self._web_tool_names:
            self._web_call_count += 1
            if self._web_call_count > MAX_WEB_CALLS:
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
        tool_timeout = (
            MCP_TOOL_TIMEOUT if tool_name.startswith("mcp_")
            else DELEGATE_TOOL_TIMEOUT if tool_name == "delegate"
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
                ui.on_tool_start(tool_name, arguments)
                ui.on_tool_done(tool_name, self._result_cache[cache_key])
                return True, {"content": self._result_cache[cache_key], "tool_id": tool_id}

        ui.on_tool_start(tool_name, arguments)

        try:
            result = await asyncio.wait_for(
                self.registry.execute(tool_name, arguments),
                timeout=tool_timeout,
            )
        except asyncio.TimeoutError:
            result = f"错误：工具 {tool_name} 执行超时（{tool_timeout}s）"
            ui.on_tool_error(tool_name, result)
            return False, {"content": result, "tool_id": tool_id}
        except Exception as e:
            logger.exception(f"工具 {tool_name} 执行异常")
            result = f"工具执行异常：{e}"
            ui.on_tool_error(tool_name, result)
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

        return True, {"content": result, "tool_id": tool_id}

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

    # ── helpers ───────────────────────────────────────────────────

    async def _call_llm(
        self,
        messages: list[dict],
        tools_schema: list[dict],
        ui: ExecutorUI,
    ) -> _TurnResult | None:
        """调用 LLM 流式接口，返回 _TurnResult 或 None（异常）。"""
        response_text = ""
        _in_xml = False

        try:
            async for event in self.provider.chat_with_tools(
                _sanitize_messages(messages, self.supports_vision), tools_schema,
            ):
                if isinstance(event, ThinkingDelta):
                    self._thinking_buffer += event.content
                    ui.on_thinking_token(event.content)
                elif isinstance(event, TextDelta):
                    response_text += event.content
                    if not _in_xml:
                        if "<invoke" in response_text:
                            _in_xml = True
                        else:
                            ui.on_text_token(event.content)
                elif isinstance(event, StreamEnd):
                    self._try_xml_fallback(response_text, event, ui)
                    ui.on_text_done()
                    return _TurnResult(stream_end=event, response_text=response_text)
        except TypeError as e:
            logger.exception("LLM 流处理类型错误")
            ui.on_tool_error("LLM", f"类型错误(可能是 pycache 过期): {e}")
            return None
        except Exception as e:
            logger.exception("LLM 调用失败")
            msg = str(e)
            status_code = None
            # 尝试提取 HTTP 响应体（DeepSeek 400 等错误的详细信息在 body 里）
            resp = getattr(e, 'response', None)
            if resp is not None:
                status_code = getattr(resp, 'status_code', None)
                try:
                    body = resp.text[:600]
                    if body:
                        msg = f"{msg}\n响应体: {body}"
                except Exception:
                    pass
            perr = ProviderError(msg, provider=self.provider.__class__.__name__,
                                 status_code=status_code)
            logger.warning("ProviderError: %s (status=%s)", perr, perr.status_code)
            ui.on_tool_error("LLM", msg)
            return None

        self._try_xml_fallback(response_text, StreamEnd("error", []), ui)
        ui.on_text_done()
        ui.on_tool_error("LLM", "流式响应异常中断")
        return None

    def _try_xml_fallback(
        self, response_text: str, event: StreamEnd, ui: ExecutorUI,
    ) -> StreamEnd:
        """从文本中剥离 <invoke> XML 并作为 tool_calls fallback。"""
        xml_start = response_text.find("<invoke")
        if xml_start >= 0:
            clean = response_text[:xml_start].strip()
            native_has = bool(event.tool_calls)
            logger.warning(
                f"[XML] found at pos {xml_start}, clean={len(clean)}chars, "
                f"native_tools={native_has}"
            )
            if clean:
                ui.on_replace_streamed_text(clean)
            if not event.tool_calls:
                xml_calls = self._extract_xml_tool_calls(response_text)
                if xml_calls:
                    event.tool_calls = xml_calls
        return event

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
    def _strip_quoted(text: str) -> str:
        """移除 shell 命令中引号包裹的内容。委托给 core.kernel.safety.strip_quoted。"""
        return strip_quoted(text)

    @staticmethod
    def _parse_args(raw_args: str | dict) -> dict:
        """解析工具参数（JSON 字符串或已解析的 dict）。"""
        if isinstance(raw_args, str):
            try:
                return json.loads(raw_args)
            except json.JSONDecodeError:
                return {}
        return raw_args

    @staticmethod
    def _extract_xml_tool_calls(text: str) -> list[dict]:
        """从文本中提取 XML 工具调用。委托给 core.kernel.xml_tool_parser。"""
        return extract_xml_tool_calls(text)
