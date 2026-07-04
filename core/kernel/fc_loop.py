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
import re
from dataclasses import dataclass

from .protocols import ExecutorUI
from core.tools import ToolRegistry
from core.llm_gateway import TextDelta, StreamEnd

logger = logging.getLogger(__name__)

DEFAULT_MAX_TURNS = 5


def _sanitize_messages(messages: list[dict],
                      supports_vision: bool = False) -> list[dict]:
    """清洗消息列表：非视觉模型需将多模态 content（list）转为纯文本。

    视觉模型（gpt-4o、claude-3+、gemini-1.5+ 等）保留 content 数组格式，
    仅做浅拷贝确保原始 conversation 不被修改。

    Args:
        messages: 对话历史列表
        supports_vision: 模型是否支持图片输入（True 保留多模态格式）

    Returns:
        新列表，不修改输入的 dict
    """
    if supports_vision:
        # 视觉模型：原样返回（浅拷贝）
        return list(messages)

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
TOOL_RESULT_MAX_CHARS = 8000   # 工具结果最大字符数（超出截断）
MAX_WEB_CALLS = 3              # 单次 FC 循环中 web_search + web_fetch 总调用上限

# 匹配 Claude/Anthropic 格式的 XML 工具调用
_XML_INVOKE_RE = re.compile(
    r'<invoke\s+name="(\w+)"[^>]*>(.*?)</invoke>',
    re.DOTALL,
)
_XML_PARAM_RE = re.compile(
    r'<parameter\s+name="(\w+)"[^>]*>(.*?)</parameter>',
    re.DOTALL,
)


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
    _web_tool_names: frozenset = frozenset({"web_search", "web_fetch"})

    def __init__(self, provider, tool_registry: ToolRegistry,
                 max_turns: int = DEFAULT_MAX_TURNS) -> None:
        self.provider = provider
        self.registry = tool_registry
        self.max_turns = max_turns
        self.supports_vision: bool = getattr(provider, 'supports_vision', False)
        self._web_call_count = 0

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
                else:
                    messages.append({
                        "role": "assistant",
                        "content": result.response_text,
                    })
                    break

            # ── 有 tool_calls：并行执行 → 错误喂回 LLM ──────────
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

    # ── 工具执行（并行 + 超时 + 截断） ──────────────────────────

    async def _execute_tools(
        self,
        tool_calls: list[dict],
        ui: ExecutorUI,
    ) -> list[dict]:
        """并行执行所有工具调用。

        每个工具独立超时、独立截断。同轮内工具并发执行。
        所有错误作为普通结果返回 — 不阻断对话，LLM 自行降级。
        返回顺序与 tool_calls 顺序一致。

        网络工具（web_search / web_fetch）有每轮 3 次总调用上限，
        超出后直接返回错误而不发起实际请求。
        """
        async def _run_one(tc: dict) -> dict:
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
                    return {"content": result, "tool_id": tool_id}

            ui.on_tool_start(tool_name, arguments)

            try:
                result = await asyncio.wait_for(
                    self.registry.execute(tool_name, arguments),
                    timeout=TOOL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                result = f"错误：工具 {tool_name} 执行超时（{TOOL_TIMEOUT}s）"
                ui.on_tool_error(tool_name, result)
            except Exception as e:
                logger.exception(f"工具 {tool_name} 执行异常")
                result = f"工具执行异常：{e}"
                ui.on_tool_error(tool_name, result)
            else:
                # 截断过长结果
                result = self._truncate_result(result)
                ui.on_tool_done(tool_name, result)

            return {"content": result, "tool_id": tool_id}

        return await asyncio.gather(*[_run_one(tc) for tc in tool_calls])

    # ── 结果截断 ──────────────────────────────────────────────────

    @staticmethod
    def _truncate_result(result: str) -> str:
        """截断过长的工具结果，避免撑爆 LLM 上下文。

        超过 TOOL_RESULT_MAX_CHARS 时保留首尾各一半，
        中间插入截断标记。
        """
        if len(result) <= TOOL_RESULT_MAX_CHARS:
            return result

        half = TOOL_RESULT_MAX_CHARS // 2 - 50
        head = result[:half]
        tail = result[-half:]
        return (
            f"{head}\n\n"
            f"…（输出过大，已从 {len(result)} 字符截断至 {TOOL_RESULT_MAX_CHARS} 字符）…\n\n"
            f"{tail}"
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
                if isinstance(event, TextDelta):
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
            # 尝试提取 HTTP 响应体（DeepSeek 400 等错误的详细信息在 body 里）
            resp = getattr(e, 'response', None)
            if resp is not None:
                try:
                    body = resp.text[:600]
                    if body:
                        msg = f"{msg}\n响应体: {body}"
                except Exception:
                    pass
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
        """从文本中提取 Claude/Anthropic 格式的 XML 工具调用。"""
        calls: list[dict] = []
        for i, match in enumerate(_XML_INVOKE_RE.finditer(text)):
            tool_name = match.group(1)
            params_block = match.group(2)

            args: dict[str, str] = {}
            for pm in _XML_PARAM_RE.finditer(params_block):
                args[pm.group(1)] = pm.group(2).strip()

            calls.append({
                "id": f"xml_{i}",
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            })

        return calls
