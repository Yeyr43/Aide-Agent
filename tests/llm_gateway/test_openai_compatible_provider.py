"""Tests for core.llm_gateway.openai_compatible_provider — OpenAI-compatible provider factory."""

import httpx
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from core.llm_gateway.openai_compatible_provider import OpenAICompatibleProvider
from core.llm_gateway.provider import TextDelta, ThinkingDelta, StreamEnd


class _FakeStreamContext:
    """模拟 httpx 流式响应上下文。"""

    def __init__(self, sse_lines, status_code=200):
        self._lines = sse_lines
        self.status_code = status_code

    def raise_for_status(self):
        pass

    async def aread(self):
        return b""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    """捕获请求体并返回假 SSE 流。"""

    def __init__(self, timeout=None):
        self.last_kwargs = {}
        self._lines = []

    def set_lines(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, headers=None, json=None):
        self.last_kwargs = dict(method=method, url=url, headers=headers, json=json)
        return _FakeStreamContext(self._lines)


def _make_provider(**kw):
    kw.setdefault("model", "gpt-4o")
    kw.setdefault("base_url", "https://api.example.com/v1")
    kw.setdefault("api_key", "sk-test")
    return OpenAICompatibleProvider(**kw)


class TestHeaders:
    def test_authorization_and_content_type(self):
        provider = _make_provider(api_key="sk-abc")
        headers = provider._headers()
        assert headers["Authorization"] == "Bearer sk-abc"
        assert headers["Content-Type"] == "application/json"


class TestCheckResponse:
    async def test_ok_status_no_raise(self):
        provider = _make_provider()
        await provider._check_response(_FakeStreamContext([]))  # 200 → 不抛

    async def test_error_status_logs_and_raises(self):
        provider = _make_provider()

        class ErrResponse:
            status_code = 500
            async def aread(self):
                return b"internal error"
            def raise_for_status(self):
                raise httpx.HTTPStatusError(
                    "500", request=MagicMock(), response=MagicMock())

        with pytest.raises(httpx.HTTPStatusError):
            await provider._check_response(ErrResponse())


class TestChat:
    async def test_streams_text_tokens(self):
        provider = _make_provider()
        fake = _FakeAsyncClient()
        fake.set_lines([
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            'data: {"choices":[{"delta":{"content":" World"}}]}',
            "data: [DONE]",
        ])
        with patch("core.llm_gateway.openai_compatible_provider.httpx.AsyncClient",
                   return_value=fake):
            tokens = []
            async for token in provider.chat([{"role": "user", "content": "hi"}]):
                tokens.append(token)
        assert "".join(tokens) == "Hello World"
        # 请求体校验
        body = fake.last_kwargs["json"]
        assert body["model"] == "gpt-4o"
        assert body["stream"] is True
        assert body["messages"] == [{"role": "user", "content": "hi"}]
        assert "reasoning_effort" not in body

    async def test_thinking_adds_reasoning_effort(self):
        provider = _make_provider(thinking=True)
        fake = _FakeAsyncClient()
        fake.set_lines(["data: [DONE]"])
        with patch("core.llm_gateway.openai_compatible_provider.httpx.AsyncClient",
                   return_value=fake):
            async for _ in provider.chat([]):
                pass
        assert fake.last_kwargs["json"]["reasoning_effort"] == "medium"

    async def test_endpoint_and_headers_sent(self):
        provider = _make_provider(base_url="https://api.example.com/v1/")
        fake = _FakeAsyncClient()
        fake.set_lines(["data: [DONE]"])
        with patch("core.llm_gateway.openai_compatible_provider.httpx.AsyncClient",
                   return_value=fake):
            async for _ in provider.chat([]):
                pass
        assert fake.last_kwargs["url"] == "https://api.example.com/v1/chat/completions"
        assert fake.last_kwargs["headers"]["Authorization"] == "Bearer sk-test"


class TestChatWithTools:
    async def test_streams_events_with_tools(self):
        provider = _make_provider()
        fake = _FakeAsyncClient()
        fake.set_lines([
            'data: {"choices":[{"delta":{"content":"thinking..."}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"read_file","arguments":"{}"}}]}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
            "data: [DONE]",
        ])
        tools = [{"type": "function", "function": {"name": "read_file"}}]
        with patch("core.llm_gateway.openai_compatible_provider.httpx.AsyncClient",
                   return_value=fake):
            events = []
            async for ev in provider.chat_with_tools([{"role": "user", "content": "hi"}], tools):
                events.append(ev)
        assert any(isinstance(e, TextDelta) for e in events)
        assert any(isinstance(e, StreamEnd) for e in events)
        assert fake.last_kwargs["json"]["tools"] == tools

    async def test_no_tools_omits_tools_key(self):
        provider = _make_provider()
        fake = _FakeAsyncClient()
        fake.set_lines(["data: [DONE]"])
        with patch("core.llm_gateway.openai_compatible_provider.httpx.AsyncClient",
                   return_value=fake):
            events = []
            async for ev in provider.chat_with_tools([{"role": "user", "content": "hi"}], []):
                events.append(ev)
        assert "tools" not in fake.last_kwargs["json"]


class TestOpenAICompatibleProvider:
    def test_initialization(self):
        provider = OpenAICompatibleProvider(
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            supports_vision=True,
        )
        assert provider.model == "gpt-4o"
        assert provider.supports_vision is True
        assert provider.api_key == "sk-test"
        assert provider.endpoint == "https://api.openai.com/v1/chat/completions"

    def test_default_values(self):
        provider = OpenAICompatibleProvider(
            model="gpt-4o",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
        )
        assert provider.model == "gpt-4o"
        assert provider.supports_vision is False
        assert provider.api_key == "sk-test"

    def test_chat_with_tools_returns_coroutine(self):
        provider = OpenAICompatibleProvider(
            model="gpt-4o",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
        )
        result = provider.chat_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )
        assert hasattr(result, "__aiter__")

    def test_endpoint_trailing_slash_handling(self):
        """Trailing slashes in base_url should be stripped before appending path."""
        provider = OpenAICompatibleProvider(
            model="gpt-4o",
            base_url="https://api.example.com/v1/",
            api_key="sk-test",
        )
        assert provider.endpoint == "https://api.example.com/v1/chat/completions"
        # No double slash
        assert "//chat" not in provider.endpoint

    def test_no_vision_by_default(self):
        provider = OpenAICompatibleProvider(
            model="gpt-4o",
            base_url="http://localhost:11434/v1",
            api_key="",
        )
        assert provider.supports_vision is False
