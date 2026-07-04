"""LLM Gateway — AbstractProvider Protocol + 共享 SSE 解析。

两个 Provider (OpenAI/Ollama) 共享同一套 SSE → token/tool_call 解析逻辑。
适配器只负责拼 base_url 和 headers。

P1 扩展：新增 StreamEvent 类型 + _parse_sse_stream_with_tools()，
支持 function calling 的 tool_calls delta 累积。
"""

import json
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol

import httpx


# ── StreamEvent 类型 ──────────────────────────────────────────────

@dataclass
class TextDelta:
    """LLM 流式输出的文本 token。"""
    content: str


@dataclass
class StreamEnd:
    """流结束事件，携带 finish_reason 和完整的 tool_calls。"""
    finish_reason: str                          # "stop" | "tool_calls" | "length"
    tool_calls: list[dict] = field(default_factory=list)
    # tool_calls: [{"id": "call_xxx", "name": "read_file", "arguments": {...}}, ...]


# ── Protocol ──────────────────────────────────────────────────────

class AbstractProvider(Protocol):
    """LLM Provider 协议。

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
    ) -> AsyncIterator[TextDelta | StreamEnd]:
        """P1 带 function calling 的流式对话。

        Yields:
            TextDelta: 文本 token（流式渲染）
            StreamEnd: 流结束事件（含 finish_reason 和累积的 tool_calls）
        """
        ...


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
) -> AsyncIterator[TextDelta | StreamEnd]:
    """解析 OpenAI 兼容 SSE 流，同时处理 content 和 tool_calls delta。

    tool_calls delta 分片到达，按 index 累积 id/name/arguments_str。
    finish_reason 出现时，parse 所有 accumulator 的 arguments JSON，
    组装为完整 tool_calls 列表，yield StreamEnd。

    Args:
        response: httpx 流式响应对象

    Yields:
        TextDelta: 文本 token
        StreamEnd: 流结束（最后必然 yield 一次）
    """
    accumulators: dict[int, dict] = {}   # index → {id, name, arguments_str}
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

        # ── 文本 token ──
        content = delta.get("content")
        if content:
            yield TextDelta(content)

        # ── tool_calls delta 累积 ──
        tool_calls = delta.get("tool_calls") or []
        for tc in tool_calls:
            idx = tc.get("index", 0)
            if idx not in accumulators:
                accumulators[idx] = {"id": "", "name": "", "arguments_str": ""}

            acc = accumulators[idx]
            if "id" in tc and tc["id"]:
                acc["id"] = tc["id"]

            func = tc.get("function", {})
            if "name" in func and func["name"]:
                acc["name"] = func["name"]
            if "arguments" in func:
                acc["arguments_str"] += func["arguments"]

        # ── finish_reason ──
        if choice_finish is not None:
            finish_reason = choice_finish
            break

    # ── 组装最终 tool_calls ──
    parsed_calls: list[dict] = []
    for idx in sorted(accumulators.keys()):
        acc = accumulators[idx]
        try:
            args = json.loads(acc["arguments_str"]) if acc["arguments_str"].strip() else {}
        except json.JSONDecodeError:
            args = {}  # JSON 畸形时降级为空对象，避免循环中断
        parsed_calls.append({
            "id": acc["id"],
            "type": "function",
            "function": {
                "name": acc["name"],
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        })

    yield StreamEnd(
        finish_reason=finish_reason or "stop",
        tool_calls=parsed_calls,
    )
