"""Tests for Anthropic Provider — format conversion, SSE parsing, factory."""

import json
import httpx
import pytest
from unittest.mock import patch

from core.llm_gateway.anthropic_provider import (
    AnthropicProvider,
    ANTHROPIC_DEFAULT_MAX_TOKENS,
)
from core.llm_gateway import create_provider, AnthropicProvider as AP
from core.llm_gateway.provider import TextDelta, ThinkingDelta, StreamEnd
from core.config import LLMConfig


# ── Endpoint construction ──────────────────────────────────────────────

class TestEndpoint:
    def test_standard_base_url(self):
        p = AnthropicProvider(
            model="claude-sonnet-4-5",
            base_url="https://api.anthropic.com",
            api_key="sk-ant-test",
        )
        assert p.endpoint == "https://api.anthropic.com/v1/messages"

    def test_trailing_slash(self):
        p = AnthropicProvider(
            model="claude-sonnet-4-5",
            base_url="https://api.anthropic.com/",
            api_key="sk-ant-test",
        )
        assert p.endpoint == "https://api.anthropic.com/v1/messages"

    def test_already_has_v1(self):
        p = AnthropicProvider(
            model="claude-sonnet-4-5",
            base_url="https://api.anthropic.com/v1",
            api_key="sk-ant-test",
        )
        assert p.endpoint == "https://api.anthropic.com/v1/messages"

    def test_proxy_base_url(self):
        p = AnthropicProvider(
            model="claude-sonnet-4-5",
            base_url="https://my-proxy.example.com/anthropic",
            api_key="sk-ant-test",
        )
        assert p.endpoint == "https://my-proxy.example.com/anthropic/v1/messages"

    def test_proxy_with_v1(self):
        p = AnthropicProvider(
            model="claude-sonnet-4-5",
            base_url="https://my-proxy.example.com/anthropic/v1",
            api_key="sk-ant-test",
        )
        assert p.endpoint == "https://my-proxy.example.com/anthropic/v1/messages"


# ── Message conversion ──────────────────────────────────────────────────

class TestConvertMessages:
    def test_system_extracted_to_top_level(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        system, converted = AnthropicProvider._convert_messages(msgs)
        assert system == "You are helpful."
        assert len(converted) == 1
        assert converted[0]["role"] == "user"
        assert converted[0]["content"] == [{"type": "text", "text": "Hello"}]

    def test_multiple_system_merged(self):
        msgs = [
            {"role": "system", "content": "Rule 1"},
            {"role": "system", "content": "Rule 2"},
            {"role": "user", "content": "Hello"},
        ]
        system, converted = AnthropicProvider._convert_messages(msgs)
        assert system == "Rule 1\n\nRule 2"
        assert len(converted) == 1

    def test_user_string_content(self):
        msgs = [{"role": "user", "content": "Hello"}]
        _, converted = AnthropicProvider._convert_messages(msgs)
        assert converted[0]["content"] == [{"type": "text", "text": "Hello"}]

    def test_user_multimodal_content(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "What's in this image?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
        ]}]
        _, converted = AnthropicProvider._convert_messages(msgs)
        blocks = converted[0]["content"]
        assert blocks[0] == {"type": "text", "text": "What's in this image?"}
        assert blocks[1]["type"] == "image"
        assert blocks[1]["source"]["type"] == "base64"
        assert blocks[1]["source"]["media_type"] == "image/png"
        assert blocks[1]["source"]["data"] == "abc123"

    def test_user_multimodal_jpeg(self):
        msgs = [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,xyz"}},
        ]}]
        _, converted = AnthropicProvider._convert_messages(msgs)
        block = converted[0]["content"][0]
        assert block["source"]["media_type"] == "image/jpeg"

    def test_assistant_with_tool_calls(self):
        msgs = [{"role": "assistant", "content": "Let me check.",
                 "tool_calls": [
                     {"id": "call_1", "type": "function",
                      "function": {"name": "read_file", "arguments": '{"path": "/etc/hosts"}'}},
                 ]}]
        _, converted = AnthropicProvider._convert_messages(msgs)
        blocks = converted[0]["content"]
        assert len(blocks) == 2
        assert blocks[0] == {"type": "text", "text": "Let me check."}
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["id"] == "call_1"
        assert blocks[1]["name"] == "read_file"
        assert blocks[1]["input"] == {"path": "/etc/hosts"}

    def test_assistant_empty_content_with_tool_calls(self):
        msgs = [{"role": "assistant", "content": "",
                 "tool_calls": [
                     {"id": "c1", "type": "function",
                      "function": {"name": "search", "arguments": "{}"}},
                 ]}]
        _, converted = AnthropicProvider._convert_messages(msgs)
        blocks = converted[0]["content"]
        assert blocks[0]["type"] == "tool_use"

    def test_tool_message_to_tool_result(self):
        msgs = [{"role": "tool", "tool_call_id": "call_1",
                 "content": "file contents here"}]
        _, converted = AnthropicProvider._convert_messages(msgs)
        assert converted[0]["role"] == "user"
        block = converted[0]["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "call_1"
        assert block["content"] == "file contents here"

    def test_full_conversation_roundtrip(self):
        """完整一轮 tool calling 对话的格式转换。"""
        msgs = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Read /tmp/x"},
            {"role": "assistant", "content": "Sure.",
             "tool_calls": [
                 {"id": "tc1", "type": "function",
                  "function": {"name": "read_file", "arguments": '{"path": "/tmp/x"}'}},
             ]},
            {"role": "tool", "tool_call_id": "tc1", "content": "hello world"},
        ]
        system, converted = AnthropicProvider._convert_messages(msgs)
        assert system == "Be helpful."
        assert len(converted) == 3  # user, assistant, tool_result(user)
        assert converted[2]["role"] == "user"
        assert converted[2]["content"][0]["type"] == "tool_result"


# ── Tool schema conversion ─────────────────────────────────────────────

class TestConvertTools:
    def test_single_tool(self):
        openai_tools = [{
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file from disk",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        }]
        result = AnthropicProvider._convert_tools(openai_tools)
        assert len(result) == 1
        assert result[0]["name"] == "read_file"
        assert result[0]["description"] == "Read a file from disk"
        assert result[0]["input_schema"]["required"] == ["path"]

    def test_multiple_tools(self):
        tools = [
            {"type": "function", "function": {"name": "f1", "description": "d1", "parameters": {}}},
            {"type": "function", "function": {"name": "f2", "description": "d2", "parameters": {}}},
        ]
        result = AnthropicProvider._convert_tools(tools)
        assert len(result) == 2
        assert result[0]["name"] == "f1"
        assert result[1]["name"] == "f2"

    def test_empty_tools(self):
        assert AnthropicProvider._convert_tools([]) == []


# ── Stop reason mapping ────────────────────────────────────────────────

class TestStopReasonMapping:
    def test_end_turn(self):
        assert AnthropicProvider._map_stop_reason("end_turn") == "stop"

    def test_tool_use(self):
        assert AnthropicProvider._map_stop_reason("tool_use") == "tool_calls"

    def test_max_tokens(self):
        assert AnthropicProvider._map_stop_reason("max_tokens") == "length"

    def test_stop_sequence(self):
        assert AnthropicProvider._map_stop_reason("stop_sequence") == "stop"

    def test_unknown(self):
        assert AnthropicProvider._map_stop_reason("weird_reason") == "stop"


# ── SSE parsing ────────────────────────────────────────────────────────

class TestSSEParsing:
    @pytest.mark.asyncio
    async def test_parses_text_delta(self):
        lines = [
            'event: content_block_start',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" World"}}',
            'event: content_block_stop',
            'data: {"type":"content_block_stop","index":0}',
            'event: message_delta',
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]
        resp = _fake_anthropic_sse(lines)
        p = _make_provider()
        events = [e async for e in p._parse_sse(resp)]

        texts = [e.content for e in events if isinstance(e, TextDelta)]
        assert texts == ["Hello", " World"]

        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert len(ends) == 1
        assert ends[0].finish_reason == "stop"
        assert ends[0].tool_calls == []

    @pytest.mark.asyncio
    async def test_parses_tool_use(self):
        lines = [
            'event: content_block_start',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Let me check."}}',
            'event: content_block_stop',
            'data: {"type":"content_block_stop","index":0}',
            'event: content_block_start',
            'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_001","name":"read_file","input":{}}}',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"path\\":\\"/etc/hosts\\"}"}}',
            'event: content_block_stop',
            'data: {"type":"content_block_stop","index":1}',
            'event: message_delta',
            'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]
        resp = _fake_anthropic_sse(lines)
        p = _make_provider()
        events = [e async for e in p._parse_sse(resp)]

        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert len(ends) == 1
        assert ends[0].finish_reason == "tool_calls"
        assert len(ends[0].tool_calls) == 1
        tc = ends[0].tool_calls[0]
        assert tc["id"] == "toolu_001"
        assert tc["function"]["name"] == "read_file"
        assert json.loads(tc["function"]["arguments"]) == {"path": "/etc/hosts"}

    @pytest.mark.asyncio
    async def test_ignores_ping(self):
        lines = [
            'event: ping',
            'data: {"type":"ping"}',
            'event: content_block_start',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}',
            'event: content_block_stop',
            'data: {"type":"content_block_stop","index":0}',
            'event: message_delta',
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]
        resp = _fake_anthropic_sse(lines)
        p = _make_provider()
        events = [e async for e in p._parse_sse(resp)]
        texts = [e.content for e in events if isinstance(e, TextDelta)]
        assert texts == ["Hi"]

    @pytest.mark.asyncio
    async def test_handles_max_tokens_stop(self):
        lines = [
            'event: content_block_start',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"truncated"}}',
            'event: content_block_stop',
            'data: {"type":"content_block_stop","index":0}',
            'event: message_delta',
            'data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"}}',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]
        resp = _fake_anthropic_sse(lines)
        p = _make_provider()
        events = [e async for e in p._parse_sse(resp)]
        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert ends[0].finish_reason == "length"

    @pytest.mark.asyncio
    async def test_malformed_json_in_tool_args(self):
        lines = [
            'event: content_block_start',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"t1","name":"f","input":{}}}',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"NOT JSON"}}',
            'event: content_block_stop',
            'data: {"type":"content_block_stop","index":0}',
            'event: message_delta',
            'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]
        resp = _fake_anthropic_sse(lines)
        p = _make_provider()
        events = [e async for e in p._parse_sse(resp)]
        ends = [e for e in events if isinstance(e, StreamEnd)]
        # Should not crash; empty args fallback
        assert ends[0].tool_calls[0]["function"]["arguments"] == "{}"

    @pytest.mark.asyncio
    async def test_no_tool_use_no_tool_calls(self):
        """纯文本对话不应该有空 tool_calls。"""
        lines = [
            'event: content_block_start',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}',
            'event: content_block_stop',
            'data: {"type":"content_block_stop","index":0}',
            'event: message_delta',
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]
        resp = _fake_anthropic_sse(lines)
        p = _make_provider()
        events = [e async for e in p._parse_sse(resp)]
        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert ends[0].tool_calls == []


# ── Factory integration ─────────────────────────────────────────────────

class TestFactoryAnthropic:
    def test_create_anthropic_provider(self):
        config = LLMConfig(
            provider="anthropic",
            model="claude-sonnet-4-5",
            base_url="https://api.anthropic.com",
            api_key="sk-ant-test",
            supports_vision=True,
        )
        provider = create_provider(config)
        assert isinstance(provider, AP)
        assert provider.model == "claude-sonnet-4-5"
        assert provider.supports_vision is True

    def test_anthropic_vision_defaults_true(self):
        p = AnthropicProvider(
            model="claude-sonnet-4-5",
            base_url="https://api.anthropic.com",
            api_key="sk-ant-test",
        )
        assert p.supports_vision is True


# ── Helpers ────────────────────────────────────────────────────────────

def _make_provider():
    return AnthropicProvider(
        model="claude-sonnet-4-5",
        base_url="https://api.anthropic.com",
        api_key="sk-ant-test",
    )


def _fake_anthropic_sse(lines: list[str]):
    """Create a mock httpx response that yields Anthropic SSE event lines."""

    class FakeResponse:
        async def aiter_lines(self):
            for line in lines:
                yield line

    return FakeResponse()


# ── chat() 纯文本 ──────────────────────────────────────────────────────────

class TestChatText:
    @pytest.mark.asyncio
    async def test_chat_streams_text_deltas(self):
        p = _make_provider()

        async def _fake(messages, tools):
            yield TextDelta(content="Hello")
            yield TextDelta(content=" world")
            yield StreamEnd(finish_reason="stop", tool_calls=[])

        p.chat_with_tools = _fake
        chunks = [c async for c in p.chat([{"role": "user", "content": "hi"}])]
        assert chunks == ["Hello", " world"]


# ── chat_with_tools 完整 HTTP 路径 ────────────────────────────────────────

class TestChatWithToolsHTTP:
    @pytest.mark.asyncio
    async def test_builds_body_and_streams(self):
        sse = [
            'event: content_block_start',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}',
            'event: content_block_stop',
            'data: {"type":"content_block_stop","index":0}',
            'event: message_delta',
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]
        FakeClient.response = FakeResponse(status_code=200, lines=sse)
        p = _make_provider()
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        tools = [{"type": "function", "function": {"name": "f", "description": "d", "parameters": {}}}]
        with patch("core.llm_gateway.anthropic_provider.httpx.AsyncClient", FakeClient):
            events = [e async for e in p.chat_with_tools(msgs, tools)]

        body = FakeClient.last["kwargs"]["json"]
        assert body["model"] == "claude-sonnet-4-5"
        assert body["max_tokens"] == ANTHROPIC_DEFAULT_MAX_TOKENS
        assert body["system"] == "sys"
        assert body["tools"][0]["name"] == "f"
        assert body["stream"] is True
        headers = FakeClient.last["kwargs"]["headers"]
        assert headers["x-api-key"] == "sk-ant-test"
        assert headers["anthropic-version"] == "2023-06-01"
        assert FakeClient.last["url"].endswith("/v1/messages")
        texts = [e.content for e in events if isinstance(e, TextDelta)]
        assert texts == ["Hi"]

    @pytest.mark.asyncio
    async def test_thinking_mode_enables_extended_thinking(self):
        FakeClient.response = FakeResponse(status_code=200, lines=[])
        p = AnthropicProvider("claude", "https://api.anthropic.com", "key", thinking=True)
        with patch("core.llm_gateway.anthropic_provider.httpx.AsyncClient", FakeClient):
            _ = [e async for e in p.chat_with_tools([{"role": "user", "content": "hi"}], [])]

        body = FakeClient.last["kwargs"]["json"]
        assert body["thinking"] == {"type": "enabled", "budget_tokens": 4000}
        assert body["max_tokens"] == 8192
        assert "tools" not in body
        assert "system" not in body

    @pytest.mark.asyncio
    async def test_http_error_raises(self):
        FakeClient.response = FakeResponse(status_code=400, lines=[], error_body=b"bad key")
        p = _make_provider()
        with patch("core.llm_gateway.anthropic_provider.httpx.AsyncClient", FakeClient):
            with pytest.raises(httpx.HTTPStatusError):
                _ = [e async for e in p.chat_with_tools([{"role": "user", "content": "hi"}], [])]


# ── 消息转换边界 ───────────────────────────────────────────────────────────

class TestConvertContentEdgeCases:
    def test_tool_result_multimodal(self):
        msgs = [{"role": "tool", "tool_call_id": "t1", "content": [
            {"type": "text", "text": "看到"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]}]
        _, converted = AnthropicProvider._convert_messages(msgs)
        block = converted[0]["content"][0]
        assert block["type"] == "tool_result"
        content = block["content"]
        assert content[0] == {"type": "text", "text": "看到"}
        assert content[1]["type"] == "image"
        assert content[1]["source"]["data"] == "abc"

    def test_extract_text_list(self):
        assert AnthropicProvider._extract_text([
            {"type": "text", "text": "a"},
            {"type": "text", "text": "b"},
        ]) == "a b"

    def test_extract_text_other(self):
        assert AnthropicProvider._extract_text(123) == "123"

    def test_convert_user_content_non_string(self):
        assert AnthropicProvider._convert_user_content(42) == [{"type": "text", "text": "42"}]

    def test_assistant_content_as_blocks(self):
        msg = {"role": "assistant", "content": [
            {"type": "text", "text": "Hello"},
            {"type": "text", "text": "  "},  # 空白文本块被跳过
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
        ], "tool_calls": []}
        blocks = AnthropicProvider._convert_assistant_content(msg)
        assert blocks == [{"type": "text", "text": "Hello"}]

    def test_assistant_tool_call_invalid_json_args(self):
        msg = {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "NOT JSON"}},
        ]}
        blocks = AnthropicProvider._convert_assistant_content(msg)
        assert blocks[0]["type"] == "tool_use"
        assert blocks[0]["input"] == {}

    def test_assistant_tool_call_dict_args(self):
        msg = {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c2", "type": "function", "function": {"name": "f", "arguments": {"x": 1}}},
        ]}
        blocks = AnthropicProvider._convert_assistant_content(msg)
        assert blocks[0]["input"] == {"x": 1}

    def test_assistant_no_content_no_tool_calls(self):
        blocks = AnthropicProvider._convert_assistant_content(
            {"role": "assistant", "content": ""},
        )
        assert blocks == [{"type": "text", "text": ""}]


# ── SSE 解析边界 ───────────────────────────────────────────────────────────

class TestSSENativeStopReason:
    @pytest.mark.asyncio
    async def test_native_stop_reason_preserved(self):
        lines = [
            'event: content_block_start',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"partial"}}',
            'event: content_block_stop',
            'data: {"type":"content_block_stop","index":0}',
            'event: message_delta',
            'data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"}}',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]
        resp = _fake_anthropic_sse(lines)
        p = _make_provider()
        events = [e async for e in p._parse_sse(resp)]
        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert ends[0].native_stop_reason == "max_tokens"
        assert ends[0].finish_reason == "length"

    @pytest.mark.asyncio
    async def test_tool_use_native_stop_reason(self):
        lines = [
            'event: content_block_start',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"t1","name":"f","input":{}}}',
            'event: content_block_stop',
            'data: {"type":"content_block_stop","index":0}',
            'event: message_delta',
            'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]
        resp = _fake_anthropic_sse(lines)
        p = _make_provider()
        events = [e async for e in p._parse_sse(resp)]
        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert ends[0].native_stop_reason == "tool_use"
        assert ends[0].finish_reason == "tool_calls"
        # StreamEnd.usage 未被 Anthropic 解析填充
        assert ends[0].usage is None


class TestSSEGarbage:
    @pytest.mark.asyncio
    async def test_non_data_line_ignored(self):
        lines = [
            ": keepalive comment",
            "",
            "random garbage",
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]
        resp = _fake_anthropic_sse(lines)
        p = _make_provider()
        events = [e async for e in p._parse_sse(resp)]
        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert ends[0].finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_malformed_data_line_skipped(self):
        lines = [
            'data: {not valid json',
            'event: message_delta',
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]
        resp = _fake_anthropic_sse(lines)
        p = _make_provider()
        events = [e async for e in p._parse_sse(resp)]
        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert ends[0].finish_reason == "stop"


class TestSSEThinking:
    @pytest.mark.asyncio
    async def test_thinking_delta_yields_thinking(self):
        lines = [
            'event: content_block_start',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":"..."}}',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"第一步考虑"}}',
            'event: content_block_stop',
            'data: {"type":"content_block_stop","index":0}',
            'event: content_block_start',
            'data: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"最终答复"}}',
            'event: content_block_stop',
            'data: {"type":"content_block_stop","index":1}',
            'event: message_delta',
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]
        resp = _fake_anthropic_sse(lines)
        p = _make_provider()
        events = [e async for e in p._parse_sse(resp)]
        thinks = [e for e in events if isinstance(e, ThinkingDelta)]
        assert len(thinks) == 1
        assert thinks[0].content == "第一步考虑"
        assert thinks[0].kind == "thinking"
        texts = [e.content for e in events if isinstance(e, TextDelta)]
        assert texts == ["最终答复"]

    @pytest.mark.asyncio
    async def test_redacted_thinking_skipped(self):
        lines = [
            'event: content_block_start',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"redacted_thinking"}}',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"redacted_thinking_delta","data":"REDACTED"}}',
            'event: content_block_stop',
            'data: {"type":"content_block_stop","index":0}',
            'event: message_delta',
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]
        resp = _fake_anthropic_sse(lines)
        p = _make_provider()
        events = [e async for e in p._parse_sse(resp)]
        thinks = [e for e in events if isinstance(e, ThinkingDelta)]
        assert thinks == []


# ── HTTP 层假客户端 ────────────────────────────────────────────────────────

class FakeResponse:
    """模拟 httpx 流式响应（AsyncClient.stream 的上下文管理器返回值）。"""

    def __init__(self, status_code=200, lines=None, error_body=b""):
        self.status_code = status_code
        self.lines = lines or []
        self.error_body = error_body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_lines(self):
        for line in self.lines:
            yield line

    async def aread(self):
        return self.error_body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=None,
            )


class FakeClient:
    """模拟 httpx.AsyncClient — 记录最近一次请求并返回预置响应。"""

    response = None
    last = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, **kwargs):
        FakeClient.last = {"method": method, "url": url, "kwargs": kwargs}
        return FakeClient.response
