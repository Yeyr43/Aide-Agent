"""Tests for Anthropic Provider — format conversion, SSE parsing, factory."""

import json
import pytest

from core.llm_gateway.anthropic_provider import AnthropicProvider
from core.llm_gateway import create_provider, AnthropicProvider as AP
from core.llm_gateway.provider import TextDelta, StreamEnd
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
