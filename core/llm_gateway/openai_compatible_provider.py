"""OpenAI 兼容 Provider 基类 — OpenAI/Ollama 共享实现。

两个 Provider 的 chat/chat_with_tools 逻辑完全相同，
仅差异在超时值和日志前缀。通过类属性 _TIMEOUT / _LOG_PREFIX 参数化。
"""

import logging
from typing import AsyncIterator

import httpx

from .provider import (
    TextDelta,
    StreamEnd,
    _parse_sse_stream,
    _parse_sse_stream_with_tools,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider:
    """OpenAI 兼容 API 的共享基类。

    子类只需设置 _TIMEOUT 和 _LOG_PREFIX 类属性。
    """

    _TIMEOUT: float = 60.0
    _LOG_PREFIX: str = "API"

    def __init__(self, model: str, base_url: str, api_key: str = "",
                 supports_vision: bool = False) -> None:
        self.model = model
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.supports_vision = supports_vision

    # ── P0: 纯文本 ──

    async def chat(self, messages: list[dict]) -> AsyncIterator[str]:
        """发送消息并流式返回响应 token。"""
        async with httpx.AsyncClient(timeout=httpx.Timeout(self._TIMEOUT)) as client:
            async with client.stream(
                "POST",
                self.endpoint,
                headers=self._headers(),
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": True,
                },
            ) as response:
                await self._check_response(response)
                async for token in _parse_sse_stream(response):
                    yield token

    # ── P1: function calling ──

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> AsyncIterator[TextDelta | StreamEnd]:
        """发送消息 + tools schema，流式返回 token + tool_calls 事件。"""
        body: dict = {"model": self.model, "messages": messages, "stream": True}
        if tools:
            body["tools"] = tools

        async with httpx.AsyncClient(timeout=httpx.Timeout(self._TIMEOUT)) as client:
            async with client.stream(
                "POST",
                self.endpoint,
                headers=self._headers(),
                json=body,
            ) as response:
                await self._check_response(response)
                async for event in _parse_sse_stream_with_tools(response):
                    yield event

    # ── helpers ──

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def _check_response(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            body = await response.aread()
            logger.error(
                f"{self._LOG_PREFIX} error {response.status_code}: "
                f"{body.decode()[:1000]}"
            )
        response.raise_for_status()
