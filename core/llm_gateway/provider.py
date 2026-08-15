"""LLM Gateway — AbstractProvider Protocol + 共享 SSE 解析。

两个 Provider (OpenAI/Ollama) 共享同一套 SSE → token/tool_call 解析逻辑。
适配器只负责拼 base_url 和 headers。

P1 扩展：新增 StreamEvent 类型 + _parse_sse_stream_with_tools()，
支持 function calling 的 tool_calls delta 累积。
"""

import json
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol, runtime_checkable

import httpx

from .tool_call_builder import ToolCallAccumulator, build_tool_calls


# ── StreamEvent 类型 ──────────────────────────────────────────────

@dataclass
class TextDelta:
    """LLM 流式输出的文本 token。"""
    content: str


@dataclass
class ThinkingDelta:
    """推理/思考 token。

    与 TextDelta 区分，UI 层可用不同样式渲染。

    Attributes:
        content: 推理文本内容
        kind: 推理类型 — "thinking" (隐藏推理，dimmed),
              "reasoning" (显式推理，可展示),
              "chain_of_thought" (实验性)
    """
    content: str
    kind: str = "thinking"


@dataclass
class StreamEnd:
    """流结束事件，携带 finish_reason 和完整的 tool_calls。

    Attributes:
        finish_reason: 标准化停止原因 — "stop" | "tool_calls" | "length"
        tool_calls: 标准化工具调用列表
        native_stop_reason: Provider 原生停止原因（Anthropic: "end_turn"/"max_tokens"/"tool_use",
                            OpenAI: "stop"/"length"/"tool_calls"）
        usage: token 使用量 — {"input": N, "output": N}
    """
    finish_reason: str                          # "stop" | "tool_calls" | "length"
    tool_calls: list[dict] = field(default_factory=list)
    native_stop_reason: str | None = None
    usage: dict | None = None


# ── Protocol ──────────────────────────────────────────────────────

@runtime_checkable
class AbstractProvider(Protocol):
    """LLM Provider 协议。

    所有 Provider 必须实现 chat() 和 chat_with_tools()。
    AnthropicProvider / OpenAICompatibleProvider 均满足此协议。

    P0: chat() — 纯文本流式对话
    P1: chat_with_tools() — 带 function calling 的流式对话
    """

    async def chat(self, messages: list[dict]) -> AsyncIterator[str]:
        """P0 纯文本流式对话。"""
        ...

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> AsyncIterator[TextDelta | ThinkingDelta | StreamEnd]:
        """P1 带 function calling 的流式对话。

        Yields:
            TextDelta: 文本 token（流式渲染）
            ThinkingDelta: 深度思考 token（应 dimmed 渲染）
            StreamEnd: 流结束事件（含 finish_reason 和累积的 tool_calls）
        """
        ...


# ── 流式纯文本消费（公共 helper）──────────────────────────────────

async def stream_text(
    provider: AbstractProvider,
    messages: list[dict],
    tools: list[dict] | None = None,
) -> str:
    """流式调用 LLM 并累积纯文本响应。

    消费 chat_with_tools 的 TextDelta/StreamEnd 事件，返回累积文本。
    错误处理由调用方负责（此函数只保证正确累积）。
    ReflectEngine / AutoMemoryExtractor 复用，避免各写一份消费循环。
    """
    response_text = ""
    async for event in provider.chat_with_tools(messages, tools or []):
        if isinstance(event, TextDelta):
            response_text += event.content
        elif isinstance(event, StreamEnd):
            break
    return response_text


# ── SSE 解析（P0 纯文本）─────────────────────────────────────────

async def _parse_sse_stream(response: httpx.Response) -> AsyncIterator[str]:
    """解析 OpenAI 兼容的 SSE 流，逐 chunk yield token。

    适用 OpenAI Chat Completions API 和 Ollama 兼容端点。
    SSE 格式: data: {"choices":[{"delta":{"content":"token"}}]}\n\n

    Args:
        response: httpx 流式响应对象

    Yields:
        str: 每个 token 字符串
    """
    async for line in response.aiter_lines():
        if not line.startswith("data: "):
            continue

        data_str = line[6:]  # 去掉 "data: " 前缀

        if data_str == "[DONE]":
            break

        try:
            data = json.loads(data_str)
            delta = data["choices"][0].get("delta", {})
            content = delta.get("content", "")
            if content:
                yield content
        except (json.JSONDecodeError, KeyError, IndexError):
            # 跳过格式异常的行（部分模型可能在 stream 中返回非标准字段）
            continue


# ── SSE 解析（P1 含 tool_calls）──────────────────────────────────

async def _parse_sse_stream_with_tools(
    response: httpx.Response,
) -> AsyncIterator[TextDelta | ThinkingDelta | StreamEnd]:
    """解析 OpenAI 兼容 SSE 流，同时处理 content / reasoning_content / tool_calls delta。

    DeepSeek 等兼容 API 在 delta.reasoning_content 中返回推理 token，
    作为 ThinkingDelta yield 供 UI 层灰色渲染。

    Args:
        response: httpx 流式响应对象

    Yields:
        TextDelta: 文本 token
        ThinkingDelta: 推理/思考 token（DeepSeek reasoning_content）
        StreamEnd: 流结束（最后必然 yield 一次）
    """
    accumulators: dict[int, ToolCallAccumulator] = {}
    finish_reason: str | None = None

    async for line in response.aiter_lines():
        if not line.startswith("data: "):
            continue

        data_str = line[6:]
        if data_str == "[DONE]":
            break

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        try:
            choice = data["choices"][0]
        except (KeyError, IndexError):
            continue

        delta = choice.get("delta", {})
        choice_finish = choice.get("finish_reason")

        # ── 推理/思考 token（DeepSeek reasoning_content、OpenAI o-series）──
        reasoning = delta.get("reasoning_content")
        if reasoning:
            yield ThinkingDelta(reasoning, kind="reasoning")

        # ── 文本 token ──
        content = delta.get("content")
        if content:
            yield TextDelta(content)

        # ── tool_calls delta 累积 ──
        tool_calls = delta.get("tool_calls") or []
        for tc in tool_calls:
            idx = tc.get("index", 0)
            if idx not in accumulators:
                accumulators[idx] = ToolCallAccumulator()

            acc = accumulators[idx]
            if "id" in tc and tc["id"]:
                acc.id = tc["id"]

            func = tc.get("function", {})
            if "name" in func and func["name"]:
                acc.name = func["name"]
            if "arguments" in func:
                acc.arguments_str += func["arguments"]

        # ── finish_reason ──
        if choice_finish is not None:
            finish_reason = choice_finish
            break

    # ── 组装最终 tool_calls ──
    yield StreamEnd(
        finish_reason=finish_reason or "stop",
        tool_calls=build_tool_calls(accumulators),
        native_stop_reason=finish_reason,  # 保留原始值（openai 兼容协议）
    )
