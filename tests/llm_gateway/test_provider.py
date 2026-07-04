"""Tests for LLM Gateway — provider types, SSE parsing, factory function."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.llm_gateway.provider import (
    TextDelta,
    StreamEnd,
    _parse_sse_stream,
    _parse_sse_stream_with_tools,
)
from core.llm_gateway import create_provider, OpenAIProvider, OllamaProvider, AnthropicProvider
from core.config import LLMConfig


# ── StreamEvent Tests ──────────────────────────────────────────────────


class TestTextDelta:
    def test_contains_content(self):
        d = TextDelta("hello")
        assert d.content == "hello"

    def test_empty_content(self):
        d = TextDelta("")
        assert d.content == ""


class TestStreamEnd:
    def test_defaults(self):
        e = StreamEnd(finish_reason="stop")
        assert e.finish_reason == "stop"
        assert e.tool_calls == []

    def test_with_tool_calls(self):
        calls = [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]
        e = StreamEnd(finish_reason="tool_calls", tool_calls=calls)
        assert e.finish_reason == "tool_calls"
        assert len(e.tool_calls) == 1
        assert e.tool_calls[0]["function"]["name"] == "read_file"


# ── SSE Parsing Tests (P0 pure text) ──────────────────────────────────


class TestSSEParseStream:
    @pytest.mark.asyncio
    async def test_parses_simple_content(self):
        response = _fake_sse_lines([
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            'data: {"choices":[{"delta":{"content":" World"}}]}',
            "data: [DONE]",
        ])
        tokens = []
        async for token in _parse_sse_stream(response):
            tokens.append(token)
        assert "".join(tokens) == "Hello World"

    @pytest.mark.asyncio
    async def test_skips_non_data_lines(self):
        response = _fake_sse_lines([
            ": comment line",
            'data: {"choices":[{"delta":{"content":"X"}}]}',
            "data: [DONE]",
        ])
        tokens = [t async for t in _parse_sse_stream(response)]
        assert tokens == ["X"]

    @pytest.mark.asyncio
    async def test_handles_malformed_json(self):
        response = _fake_sse_lines([
            'data: not-json',
            'data: {"choices":[{"delta":{"content":"ok"}}]}',
            "data: [DONE]",
        ])
        tokens = [t async for t in _parse_sse_stream(response)]
        assert "ok" in tokens

    @pytest.mark.asyncio
    async def test_handles_missing_choices(self):
        response = _fake_sse_lines([
            'data: {}',
            "data: [DONE]",
        ])
        tokens = [t async for t in _parse_sse_stream(response)]
        assert tokens == []

    @pytest.mark.asyncio
    async def test_empty_content_not_yielded(self):
        response = _fake_sse_lines([
            'data: {"choices":[{"delta":{"content":""}}]}',
            'data: {"choices":[{"delta":{"content":"real"}}]}',
            "data: [DONE]",
        ])
        tokens = [t async for t in _parse_sse_stream(response)]
        assert tokens == ["real"]


# ── SSE Parsing Tests (P1 with tool_calls) ────────────────────────────


class TestSSEParseStreamWithTools:
    @pytest.mark.asyncio
    async def test_yields_text_delta(self):
        response = _fake_sse_lines([
            'data: {"choices":[{"delta":{"content":"Hi"}},{"delta":{"content":" there"},"finish_reason":"stop"}]}',
        ])
        events = [e async for e in _parse_sse_stream_with_tools(response)]
        texts = [e.content for e in events if isinstance(e, TextDelta)]
        assert "Hi" in texts
        # At least one StreamEnd
        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert len(ends) >= 1

    @pytest.mark.asyncio
    async def test_accumulates_tool_calls(self):
        chunks = [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"read_file"}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"p"}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"ath\\": \\"/x\\"}"}}]}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        ]
        response = _fake_sse_lines(chunks)
        events = [e async for e in _parse_sse_stream_with_tools(response)]

        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert len(ends) == 1
        end = ends[0]
        assert end.finish_reason == "tool_calls"
        assert len(end.tool_calls) == 1
        assert end.tool_calls[0]["function"]["name"] == "read_file"
        assert end.tool_calls[0]["id"] == "call_1"

    @pytest.mark.asyncio
    async def test_malformed_tool_arguments_degrades_gracefully(self):
        chunks = [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"f","arguments":"NOT JSON"}}]}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        ]
        response = _fake_sse_lines(chunks)
        events = [e async for e in _parse_sse_stream_with_tools(response)]
        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert len(ends) == 1
        # Should degrade to empty object, not crash
        assert ends[0].tool_calls[0]["function"]["arguments"] == "{}"

    @pytest.mark.asyncio
    async def test_handle_malformed_line(self):
        response = _fake_sse_lines([
            'data: bad json',
            'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}',
        ])
        events = [e async for e in _parse_sse_stream_with_tools(response)]
        texts = [e.content for e in events if isinstance(e, TextDelta)]
        assert "ok" in texts


# ── Factory Tests ──────────────────────────────────────────────────────


class TestCreateProvider:
    def test_create_openai_provider(self):
        config = LLMConfig(
            provider="openai",
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
        )
        provider = create_provider(config)
        assert isinstance(provider, OpenAIProvider)
        assert provider.model == "gpt-4o"

    def test_create_ollama_provider(self):
        config = LLMConfig(
            provider="ollama",
            model="llama3",
            base_url="http://localhost:11434/v1",
            api_key="ollama",
        )
        provider = create_provider(config)
        assert isinstance(provider, OllamaProvider)
        assert provider.model == "llama3"

    def test_unsupported_provider_raises(self):
        config = LLMConfig(
            provider="unknown_provider_xyz",
            model="some-model",
            base_url="https://example.com",
            api_key="sk-test",
        )
        with pytest.raises(ValueError, match="不支持的 LLM provider"):
            create_provider(config)


# ── Provider Instance Tests ────────────────────────────────────────────


class TestOpenAIProvider:
    def test_endpoint_is_chat_completions(self):
        p = OpenAIProvider(
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
        )
        assert p.endpoint.endswith("/chat/completions")

    def test_endpoint_no_trailing_slash_dedup(self):
        p = OpenAIProvider(
            model="gpt-4o",
            base_url="https://api.openai.com/v1/",
            api_key="sk-test",
        )
        assert p.endpoint == "https://api.openai.com/v1/chat/completions"


class TestOllamaProvider:
    def test_endpoint_is_chat_completions(self):
        p = OllamaProvider(
            model="llama3",
            base_url="http://localhost:11434/v1",
            api_key="ollama",
        )
        assert p.endpoint.endswith("/chat/completions")


class TestAnthropicProvider:
    def test_endpoint_is_messages(self):
        p = AnthropicProvider(
            model="claude-sonnet-4-5",
            base_url="https://api.anthropic.com",
            api_key="sk-ant-test",
        )
        assert p.endpoint.endswith("/v1/messages")

    def test_vision_default_true(self):
        p = AnthropicProvider(
            model="claude-sonnet-4-5",
            base_url="https://api.anthropic.com",
            api_key="sk-ant-test",
        )
        assert p.supports_vision is True


# ── Helpers ────────────────────────────────────────────────────────────


def _fake_sse_lines(lines: list[str]):
    """Create a mock httpx response with given SSE lines."""
    class FakeResponse:
        async def aiter_lines(self):
            for line in lines:
                yield line

    return FakeResponse()
