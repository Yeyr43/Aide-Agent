"""Function Calling 循环引擎。

组装上下文 → LLM 决定（tool_call 或 reply）→ 并行调工具 → 结果喂回 → 循环。

循环轮数上限 MAX_LOOP_TURNS（50，宽松——允许"测试所有工具"、多步进程管理等长任务跑完）；
每个工具的"单次调用"可尝试 max_turns 次（默认 10）——同一（工具, 参数）反复失败
超过上限即停止重试该调用，防止工具卡死无限循环。

工具错误不作为阻断信号，全部喂回 LLM 让其自行降级。

P4: XML fallback 解析 + 工具结果截断 + 超时保护 + 并行执行 + 无阻断循环。
P3: 工具执行段（分组/超时/截断/安全）拆至 tool_executor.py，本类聚焦循环编排。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from .protocols import ExecutorUI
from .tool_executor import ToolExecutor
from .xml_tool_parser import extract_xml_tool_calls, find_xml_start
from core.llm_gateway import TextDelta, ThinkingDelta, StreamEnd
from core.errors import ProviderError

logger = logging.getLogger(__name__)

DEFAULT_MAX_TURNS = 10     # 单工具调用可尝试的次数上限（每个 (工具, 参数) 独立计数）
MAX_LOOP_TURNS = 50        # 循环总轮数上限（多工具任务需要较多轮）


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
        return [
            {k: v for k, v in m.items() if k != "_thinking"}
            for m in copy.deepcopy(messages)
        ]

    sanitized: list[dict] = []
    for msg in messages:
        content = msg.get("content")
        # 剥离内部键 _thinking（逐条落盘用，不进 LLM 上下文/API 请求）
        clean = {k: v for k, v in msg.items() if k != "_thinking"}
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
            sanitized.append({**clean, "content": txt})
        else:
            sanitized.append(clean)
    return sanitized


@dataclass
class _TurnResult:
    """单轮 LLM 调用的结果。"""
    stream_end: StreamEnd
    response_text: str
    clean_text: str | None = None  # XML 工具调用剥离后的正文（供落盘用，避免 XML 乱码残留）
    thinking: str = ""  # 本次调用的思考内容（逐条落盘，恢复时插回工具调用间）


# ── Function Calling 循环 ─────────────────────────────────────────

class FunctionCallingLoop:
    """Function Calling 循环引擎。

    用法:
        loop = FunctionCallingLoop(provider, registry)
        await loop.run(conversation, ui)
    """

    def __init__(self, provider, tool_registry,
                 max_turns: int = DEFAULT_MAX_TURNS,
                 hook_runner: object | None = None) -> None:
        self.provider = provider
        self.registry = tool_registry
        # max_turns = 单工具调用可尝试次数（每个 (工具, 参数) 独立计数），非循环总轮数
        self.max_tool_attempts = max_turns
        self._tool_attempts: dict[tuple[str, str], int] = {}  # (工具名, 参数JSON) → 尝试次数
        self.supports_vision: bool = getattr(provider, 'supports_vision', False)
        # P3: 工具执行器 — 只读并行/写串行/失败 abort 兄弟 + 超时 + 截断 + 安全
        self._tools_executor = ToolExecutor(tool_registry, hook_runner=hook_runner)
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
        self._tools_executor.reset()
        self._thinking_buffer = ""  # 新一轮开始时重置
        self._tool_attempts = {}    # 每轮用户消息独立计数（工具尝试上限按次重置）
        tools_schema = self.registry.get_schemas()
        final: StreamEnd | None = None
        turn = 0

        for turn in range(1, MAX_LOOP_TURNS + 1):
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
                    xml_start = find_xml_start(result.response_text)
                    text_content = result.response_text[:xml_start].strip() if xml_start > 0 else None
                    # 流式阶段正文节点已把 XML 显示出来 → 替换为剥离 XML 的干净文本
                    # （否则正文残留 <tool_call> 乱码；且 _turn_ai_text 也会带上）
                    ui.on_replace_streamed_text(text_content or "")
                    messages.append({
                        "role": "assistant",
                        "content": text_content or "",
                        "tool_calls": final.tool_calls,
                        "_thinking": result.thinking,  # 逐条落盘，恢复时插回工具调用间
                    })
                    # 跳过下方通用的 messages.append，直接进入工具执行
                else:
                    messages.append({
                        "role": "assistant",
                        "content": result.response_text,
                        "_thinking": result.thinking,  # 逐条落盘，恢复时插回工具调用间
                    })
                    # P6: 若模型因 max_tokens 被截断，自动续写
                    # Anthropic 原生 stop_reason="max_tokens"；OpenAI 兼容系（DeepSeek/
                    # Ollama）为 finish_reason="length"（经 provider.py 透传），两者都要兼容
                    if (final.native_stop_reason in ("max_tokens", "length")
                            and turn < MAX_LOOP_TURNS):
                        messages.append({"role": "user", "content": "(continue)"})
                        continue
                    break

            # ── 有 tool_calls：并行执行 → 错误喂回 LLM ──────────
            if final.tool_calls:
                # 原生 tool_calls 路径：追加 assistant 消息
                if not xml_calls:
                    # XML fallback 派生的 tool_calls：content 用剥离 XML 的干净文本，
                    # 否则 <tool_call> 乱码会落盘并在再次渲染时显示
                    content = (result.clean_text
                               if result.clean_text is not None
                               else (result.response_text or ""))
                    messages.append({
                        "role": "assistant",
                        "content": content,
                        "tool_calls": final.tool_calls,
                        "_thinking": result.thinking,  # 逐条落盘，恢复时插回工具调用间
                    })

            # ── 单工具调用尝试上限：同一 (工具, 参数) 反复失败超过上限 → 停止重试 ──
            # （不同工具 / 不同参数互不影响，多工具任务可一直跑；只防单个调用卡死）
            filtered_calls: list[dict] = []
            over_limit_ids: set[str] = set()
            for tc in final.tool_calls:
                key = self._tool_attempt_key(tc)
                n = self._tool_attempts.get(key, 0) + 1
                self._tool_attempts[key] = n
                if n > self.max_tool_attempts:
                    over_limit_ids.add(tc.get("id", ""))
                else:
                    filtered_calls.append(tc)

            tool_results = (
                await self._execute_tools(filtered_calls, ui) if filtered_calls else []
            )

            # 重组：按 final.tool_calls 顺序，超限调用返回"已达上限"错误喂回 LLM
            reordered: list[dict] = []
            ri = 0
            for tc in final.tool_calls:
                if tc.get("id", "") in over_limit_ids:
                    fn = tc.get("function") or {}
                    reordered.append({"content": (
                        f"⚠️ 工具 {fn.get('name', '?')} 已尝试 {self.max_tool_attempts} 次仍未成功，"
                        "已停止重试。请换一种方式或向用户说明。"
                    )})
                else:
                    reordered.append(tool_results[ri])
                    ri += 1

            # 所有工具结果（含错误）都作为 tool 消息喂给 LLM，
            # 让 LLM 自己决定降级策略。MAX_LOOP_TURNS 自然终止。
            for tc, tool_result in zip(final.tool_calls, reordered):
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": tool_result["content"],
                })

        # ── 循环结束检查 ──────────────────────────────────────────
        if turn >= MAX_LOOP_TURNS and final and final.tool_calls:
            result = await self._call_llm(messages, [], ui)
            if result and not result.stream_end.tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": result.response_text,
                    "_thinking": result.thinking,  # 逐条落盘，恢复时插回工具调用间
                })
            else:
                ui.on_max_turns()

        return messages

    @property
    def thinking(self) -> str:
        """本轮 FC 循环累计的思考内容（持久化到 turn 文件供恢复显示）。"""
        return self._thinking_buffer

    # ── 工具执行（委托 ToolExecutor，保留签名供历史调用与测试）──

    async def _execute_tools(
        self,
        tool_calls: list[dict],
        ui: ExecutorUI,
    ) -> list[dict]:
        """执行工具调用 — 只读并发、写工具串行、失败 abort 兄弟。

        委托给 ToolExecutor.execute_tools()（P3 拆分，逻辑在 tool_executor.py）。
        """
        return await self._tools_executor.execute_tools(tool_calls, ui)

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
        call_thinking = ""  # 本次调用独立计数（逐条落盘；_thinking_buffer 是整轮累计）

        try:
            async for event in self.provider.chat_with_tools(
                _sanitize_messages(messages, self.supports_vision), tools_schema,
            ):
                if isinstance(event, ThinkingDelta):
                    self._thinking_buffer += event.content
                    call_thinking += event.content
                    ui.on_thinking_token(event.content)
                elif isinstance(event, TextDelta):
                    response_text += event.content
                    if not _in_xml:
                        if "<invoke" in response_text:
                            _in_xml = True
                        else:
                            ui.on_text_token(event.content)
                elif isinstance(event, StreamEnd):
                    clean_text = self._try_xml_fallback(response_text, event, ui)
                    ui.on_text_done()
                    return _TurnResult(stream_end=event, response_text=response_text,
                                       clean_text=clean_text, thinking=call_thinking)
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
    ) -> str | None:
        """从文本中剥离 XML（<invoke> 或 <tool_call>）并作为 tool_calls fallback。

        Returns:
            剥离 XML 后的正文文本（有 XML 时），否则 None
        """
        xml_start = find_xml_start(response_text)
        if xml_start >= 0:
            clean = response_text[:xml_start].strip()
            native_has = bool(event.tool_calls)
            logger.warning(
                f"[XML] found at pos {xml_start}, clean={len(clean)}chars, "
                f"native_tools={native_has}"
            )
            # 即使 clean 为空（XML 在最开头）也要替换：清掉流式阶段已显示的 XML 乱码
            ui.on_replace_streamed_text(clean)
            if not event.tool_calls:
                xml_calls = self._extract_xml_tool_calls(response_text)
                if xml_calls:
                    event.tool_calls = xml_calls
            return clean
        return None

    @staticmethod
    def _extract_xml_tool_calls(text: str) -> list[dict]:
        """从文本中提取 XML 工具调用。委托给 core.kernel.xml_tool_parser。"""
        return extract_xml_tool_calls(text)

    @staticmethod
    def _tool_attempt_key(tc: dict) -> tuple[str, str]:
        """同一调用的身份：工具名 + 参数（用于单工具尝试上限计数）。"""
        fn = tc.get("function") or {}
        name = fn.get("name", "?")
        args = fn.get("arguments", {})
        args_json = (json.dumps(args, sort_keys=True, ensure_ascii=False)
                     if isinstance(args, (dict, list)) else str(args))
        return (name, args_json)
