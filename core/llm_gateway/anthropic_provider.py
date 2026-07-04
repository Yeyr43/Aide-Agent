"""Anthropic Provider — 适配 Anthropic Messages API (SSE 流)。

Anthropic API 与 OpenAI 格式差异：
- system prompt 是顶级字段，不在 messages 数组中
- content 使用 content blocks 数组 [{type, text}, ...]
- tool calling: tool_use / tool_result content blocks
- SSE 事件: message_start → content_block_start/delta/stop → message_delta → message_stop
- 认证头: x-api-key (非 Authorization: Bearer)
- 版本头: anthropic-version: 2023-06-01

本适配器在内部完成所有格式转换，对外保持 TextDelta/StreamEnd 接口不变。
FC 循环和 UI 层无感知。
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

from .provider import TextDelta, StreamEnd

logger = logging.getLogger(__name__)

# Anthropic 要求所有请求必须带 max_tokens
ANTHROPIC_DEFAULT_MAX_TOKENS = 4096


class AnthropicProvider:
    """Anthropic Messages API 适配器。

    内部完成：
    1. OpenAI 消息格式 → Anthropic 消息格式（system 提取、tool_use/tool_result 转换）
    2. OpenAI tool schema → Anthropic tool schema（input_schema）
    3. Anthropic SSE 事件 → TextDelta / StreamEnd
    4. 多模态 content 转换（image_url → image source base64）
    """

    def __init__(self, model: str, base_url: str, api_key: str,
                 supports_vision: bool = True) -> None:
        self.model = model
        self.api_key = api_key
        self.supports_vision = supports_vision

        # 智能拼接 endpoint：兼容 proxy 和官方 API
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            self.endpoint = base + "/messages"
        else:
            self.endpoint = base + "/v1/messages"

    # ── P0: 纯文本 ────────────────────────────────────────────────

    async def chat(self, messages: list[dict]) -> AsyncIterator[str]:
        """纯文本流式对话。"""
        async for event in self.chat_with_tools(messages, []):
            if isinstance(event, TextDelta):
                yield event.content

    # ── P1: function calling ──────────────────────────────────────

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> AsyncIterator[TextDelta | StreamEnd]:
        """流式对话 + tool calling。

        Args:
            messages: OpenAI 格式对话历史
            tools: OpenAI function calling 格式 tools 数组

        Yields:
            TextDelta: 文本 token
            StreamEnd: 流结束（含 finish_reason 和 tool_calls）
        """
        system, anthropic_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools)

        body: dict = {
            "model": self.model,
            "max_tokens": ANTHROPIC_DEFAULT_MAX_TOKENS,
            "messages": anthropic_messages,
            "stream": True,
        }
        if system:
            body["system"] = system
        if anthropic_tools:
            body["tools"] = anthropic_tools

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            async with client.stream(
                "POST",
                self.endpoint,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=body,
            ) as response:
                if response.status_code >= 400:
                    body_bytes = await response.aread()
                    logger.error(
                        f"Anthropic API error {response.status_code}: "
                        f"{body_bytes.decode()[:1000]}"
                    )
                response.raise_for_status()
                async for event in self._parse_sse(response):
                    yield event

    # ── 消息格式转换 ───────────────────────────────────────────────

    @staticmethod
    def _convert_messages(messages: list[dict]) -> tuple[str, list[dict]]:
        """OpenAI 消息列表 → (system_text, anthropic_messages)。

        - 提取所有 system 消息合并为顶级 system 字符串
        - user content: str → [{type: text, text: ...}]
        - user content (多模态): [{type: text}, {type: image_url}] → [{type: text}, {type: image}]
        - assistant + tool_calls → [{type: text}, {type: tool_use, id, name, input}]
        - tool → {role: user, content: [{type: tool_result, ...}]}
        """
        system_parts: list[str] = []
        converted: list[dict] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                system_parts.append(AnthropicProvider._extract_text(content))
                continue

            if role == "user":
                converted.append({
                    "role": "user",
                    "content": AnthropicProvider._convert_user_content(content),
                })

            elif role == "assistant":
                converted.append({
                    "role": "assistant",
                    "content": AnthropicProvider._convert_assistant_content(msg),
                })

            elif role == "tool":
                tc_id = msg.get("tool_call_id", "")
                tc_content = AnthropicProvider._extract_text(content)
                converted.append({
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": tc_id,
                         "content": tc_content},
                    ],
                })

        system_text = "\n\n".join(system_parts) if system_parts else ""
        return system_text, converted

    @staticmethod
    def _extract_text(content) -> str:
        """从 OpenAI content（str 或 list[dict]）提取纯文本。"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                b.get("text", "") for b in content
                if b.get("type") == "text"
            ]
            return " ".join(parts)
        return str(content)

    @staticmethod
    def _convert_user_content(content) -> list[dict]:
        """user content → Anthropic content blocks。"""
        if isinstance(content, str):
            return [{"type": "text", "text": content}]

        if isinstance(content, list):
            blocks: list[dict] = []
            for block in content:
                block_type = block.get("type", "")
                if block_type == "text":
                    blocks.append({"type": "text", "text": block.get("text", "")})
                elif block_type == "image_url":
                    url = block.get("image_url", {}).get("url", "")
                    if url.startswith("data:"):
                        # data:image/png;base64,XXXXX
                        header, b64 = url.split(",", 1)
                        media_type = header.split(":")[1].split(";")[0]
                        blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        })
            return blocks if blocks else [{"type": "text", "text": ""}]

        return [{"type": "text", "text": str(content)}]

    @staticmethod
    def _convert_assistant_content(msg: dict) -> list[dict]:
        """assistant 消息 → Anthropic content blocks。

        包括文本和 tool_use 块。
        """
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls", [])

        blocks: list[dict] = []

        # 文本部分
        if content:
            if isinstance(content, str):
                if content.strip():
                    blocks.append({"type": "text", "text": content})
            elif isinstance(content, list):
                for block in content:
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        if text.strip():
                            blocks.append({"type": "text", "text": text})

        # tool_calls → tool_use blocks
        for tc in tool_calls:
            func = tc.get("function", {})
            raw_args = func.get("arguments", "{}")
            try:
                if isinstance(raw_args, str):
                    args = json.loads(raw_args)
                else:
                    args = raw_args
            except json.JSONDecodeError:
                args = {}
            blocks.append({
                "type": "tool_use",
                "id": tc.get("id", ""),
                "name": func.get("name", ""),
                "input": args,
            })

        if not blocks:
            blocks.append({"type": "text", "text": ""})

        return blocks

    # ── Tool schema 转换 ───────────────────────────────────────────

    @staticmethod
    def _convert_tools(tools: list[dict]) -> list[dict]:
        """OpenAI tool schema → Anthropic tool schema。

        OpenAI: {type: function, function: {name, description, parameters}}
        Anthropic: {name, description, input_schema}
        """
        converted: list[dict] = []
        for tool in tools:
            func = tool.get("function", {})
            converted.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {
                    "type": "object",
                    "properties": {},
                }),
            })
        return converted

    # ── SSE 解析 ───────────────────────────────────────────────────

    async def _parse_sse(
        self, response: httpx.Response,
    ) -> AsyncIterator[TextDelta | StreamEnd]:
        """解析 Anthropic SSE 事件流。

        Anthropic SSE 格式（每行以 event: 或 data: 开头）:

            event: message_start
            data: {"type": "message_start", "message": {...}}

            event: content_block_start
            data: {"type": "content_block_start", "index": 0,
                   "content_block": {"type": "text", "text": ""}}

            event: content_block_delta
            data: {"type": "content_block_delta", "index": 0,
                   "delta": {"type": "text_delta", "text": "Hello"}}

            event: content_block_stop
            data: {"type": "content_block_stop", "index": 0}

            event: message_delta
            data: {"type": "message_delta",
                   "delta": {"stop_reason": "end_turn"}}

            event: message_stop
            data: {"type": "message_stop"}

        工具调用时:

            event: content_block_start
            data: {"type": "content_block_start", "index": 1,
                   "content_block": {"type": "tool_use", "id": "toolu_xxx",
                                     "name": "read_file", "input": {}}}

            event: content_block_delta
            data: {"type": "content_block_delta", "index": 1,
                   "delta": {"type": "input_json_delta",
                             "partial_json": "{\\"path\\": \\"/a\\"}"}}

        Yields:
            TextDelta: 文本 token（流式渲染）
            StreamEnd: 流结束（必然 yield 一次）
        """
        current_event: str | None = None
        tool_use_accumulators: dict[int, dict] = {}
        stop_reason = "stop"

        async for line in response.aiter_lines():
            # event: 行 — 记录当前事件类型
            if line.startswith("event: "):
                current_event = line[7:].strip()
                continue

            if not line.startswith("data: "):
                continue

            data_str = line[6:]
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            event_type = data.get("type", current_event or "")

            # ping 心跳 — 忽略
            if event_type == "ping":
                continue

            # content_block_start — 新内容块
            if event_type == "content_block_start":
                index = data.get("index", 0)
                block = data.get("content_block", {})
                if block.get("type") == "tool_use":
                    tool_use_accumulators[index] = {
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "input_json": "",
                    }

            # content_block_delta — 文本增量或工具参数增量
            elif event_type == "content_block_delta":
                delta = data.get("delta", {})
                delta_type = delta.get("type", "")
                index = data.get("index", 0)

                if delta_type == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        yield TextDelta(text)

                elif delta_type == "input_json_delta":
                    if index in tool_use_accumulators:
                        tool_use_accumulators[index]["input_json"] += \
                            delta.get("partial_json", "")

            # message_delta — stop_reason
            elif event_type == "message_delta":
                sr = data.get("delta", {}).get("stop_reason", "end_turn")
                stop_reason = self._map_stop_reason(sr)

            # message_stop — 流结束
            elif event_type == "message_stop":
                break

        # ── 组装 tool_calls ──
        tool_calls: list[dict] = []
        for idx in sorted(tool_use_accumulators.keys()):
            acc = tool_use_accumulators[idx]
            try:
                args = (
                    json.loads(acc["input_json"])
                    if acc["input_json"].strip() else {}
                )
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({
                "id": acc["id"],
                "type": "function",
                "function": {
                    "name": acc["name"],
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            })

        yield StreamEnd(finish_reason=stop_reason, tool_calls=tool_calls)

    @staticmethod
    def _map_stop_reason(anthropic_reason: str) -> str:
        """Anthropic stop_reason → OpenAI finish_reason。"""
        mapping = {
            "end_turn": "stop",
            "tool_use": "tool_calls",
            "max_tokens": "length",
            "stop_sequence": "stop",
        }
        return mapping.get(anthropic_reason, "stop")
