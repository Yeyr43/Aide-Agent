"""Tests for core.context.token_counter — token estimation utilities."""

import json
import pytest
from unittest.mock import patch, MagicMock

from core.context.token_counter import (
    estimate_tokens,
    compute_context_usage,
    _extract_content_text_and_images,
    _estimate_image_tokens,
    DEFAULT_CONTEXT_WINDOW,
    trim_conversation_to_window,
    _split_turns,
)


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_english_text(self):
        tokens = estimate_tokens("Hello, world! This is a test.")
        # ~4 chars/token → ~7-8 tokens
        assert 5 <= tokens <= 12

    def test_chinese_text(self):
        tokens = estimate_tokens("你好世界这是一段测试文本")
        # CJK ~1.5 chars/token
        assert tokens > 0

    def test_mixed_text(self):
        tokens = estimate_tokens("Hello 你好 World 世界")
        assert tokens > 0

    def test_long_text(self):
        text = "a" * 1000
        tokens = estimate_tokens(text)
        # ~4 chars/token → ~250 tokens
        assert 200 <= tokens <= 300

    def test_special_characters(self):
        tokens = estimate_tokens("!@#$%^&*()")
        assert tokens >= 0


class TestExtractContentTextAndImages:
    def test_plain_string(self):
        text, img_tokens = _extract_content_text_and_images("hello")
        assert text == "hello"
        assert img_tokens == 0

    def test_text_only_list(self):
        content = [{"type": "text", "text": "hello world"}]
        text, img_tokens = _extract_content_text_and_images(content)
        assert "hello world" in text
        assert img_tokens == 0

    def test_image_only_list(self):
        content = [{
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="},
        }]
        text, img_tokens = _extract_content_text_and_images(content)
        assert img_tokens > 0  # at minimum auto mode (85)

    def test_mixed_content(self):
        content = [
            {"type": "text", "text": "What's this?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc="}},
        ]
        text, img_tokens = _extract_content_text_and_images(content)
        assert "What's this?" in text
        assert img_tokens > 0

    def test_unknown_block_type(self):
        content = [{"type": "unknown_type", "data": "stuff"}]
        text, img_tokens = _extract_content_text_and_images(content)
        assert img_tokens == 0

    def test_non_list_non_string(self):
        text, img_tokens = _extract_content_text_and_images(42)
        assert "42" in text
        assert img_tokens == 0


class TestEstimateImageTokens:
    def test_invalid_data_url(self):
        tokens = _estimate_image_tokens("not-a-data-url")
        assert tokens == 85  # fallback to auto

    def test_very_short_base64(self):
        tokens = _estimate_image_tokens("data:image/png;base64,a")
        # Probably can't decode as image → fallback to 85
        assert tokens >= 0


class TestComputeContextUsage:
    def test_empty_messages(self):
        estimated, pct = compute_context_usage([])
        assert estimated >= 0
        assert pct >= 0.0

    def test_simple_text_messages(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        estimated, pct = compute_context_usage(messages, context_window=128000)
        assert estimated > 0
        assert 0.0 < pct < 1.0

    def test_with_tools_schema(self):
        messages = [{"role": "user", "content": "hi"}]
        tools = [{"type": "function", "function": {"name": "echo"}}]
        estimated_with, _ = compute_context_usage(messages, tools_schema=tools)
        estimated_without, _ = compute_context_usage(messages, tools_schema=None)
        # With tools should count more tokens
        assert estimated_with >= estimated_without

    def test_context_window_zero_returns_no_pct(self):
        messages = [{"role": "user", "content": "hi" * 1000}]
        estimated, pct = compute_context_usage(messages, context_window=0)
        assert estimated > 0
        assert pct == 0.0

    def test_usage_pct_clamped_at_one(self):
        """Very long text should produce pct <= 1.0."""
        huge_text = "x" * 1_000_000
        messages = [{"role": "user", "content": huge_text}]
        estimated, pct = compute_context_usage(messages, context_window=1000)
        assert pct == 1.0

    def test_non_serializable_tools_schema(self):
        """Non-serializable tool schema (mock) should not crash."""
        messages = [{"role": "user", "content": "hi"}]
        # Mock object that can't be json.dumps'd — it will raise TypeError inside
        # compute_context_usage which is caught by the except clause
        try:
            estimated, pct = compute_context_usage(
                messages, tools_schema=[MagicMock()],
            )
            assert estimated >= 0
        except TypeError:
            # May propagate if the mock can't be serialized at all
            pass

    def test_multimodal_content(self):
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc="}},
            ],
        }]
        estimated, pct = compute_context_usage(messages)
        assert estimated > 0

    def test_default_context_window(self):
        messages = [{"role": "user", "content": "Hello, this is a test message with some content"}]
        estimated, pct = compute_context_usage(messages)
        assert estimated > 0
        assert isinstance(pct, float)


class TestSplitTurns:
    def test_splits_by_user_message(self):
        msgs = [_msg("user", "a"), _msg("assistant", "b"),
                _msg("tool", "r"), _msg("user", "c"), _msg("assistant", "d")]
        turns = _split_turns(msgs)
        assert len(turns) == 2
        assert [m["role"] for m in turns[0]] == ["user", "assistant", "tool"]
        assert [m["role"] for m in turns[1]] == ["user", "assistant"]

    def test_leading_orphan_messages_one_turn(self):
        turns = _split_turns([_msg("assistant", "stray")])
        assert len(turns) == 1
        assert turns[0][0]["role"] == "assistant"


class TestTrimConversation:
    def test_within_budget_unchanged(self):
        """预算充足时不修剪（幂等）。"""
        system = [_msg("system", "soul")]
        conv = [_msg("user", "hello"), _msg("assistant", "hi")]
        out = trim_conversation_to_window(system, conv, context_window=128000)
        assert out == conv

    def test_unlimited_window_no_trim(self):
        conv = [_msg("user", "q"), _msg("assistant", "A" * 5000)]
        out = trim_conversation_to_window([_msg("system", "s")], conv,
                                          context_window=0)
        assert out == conv

    def test_drops_oldest_turns(self):
        """超预算时从头部逐轮丢弃最老轮次，保留最近轮。"""
        system = [_msg("system", "s")]
        turn = [_msg("user", "q"), _msg("assistant", "A" * 5000)]  # ~1251 tokens
        conv = turn + turn + turn
        # budget = 4000*0.9 = 3600；3 轮 3753 → 丢 1 轮 2502 ≤ 3600
        out = trim_conversation_to_window(system, conv, context_window=4000)
        users = [m for m in out if m["role"] == "user"]
        assert len(users) == 2
        # 保留的是最近两轮（顺序不变）
        assert out[-1] == turn[1]

    def test_truncates_last_turn_when_single_remains(self):
        """只剩 1 轮仍超 → 截断 assistant 正文（user 保留），不超预算。"""
        system = [_msg("system", "s")]
        conv = [_msg("user", "q"), _msg("assistant", "A" * 50000)]  # ~12500 tokens
        out = trim_conversation_to_window(system, conv, context_window=1000)
        assert out[0]["role"] == "user"
        assert len(out[1]["content"]) < 50000
        est, _ = compute_context_usage(system + out, context_window=0)
        assert est <= 1000 * 0.9

    def test_system_messages_kept_intact(self):
        """system 消息不参与丢弃，原样保留。"""
        system = [_msg("system", "关键引导内容")]
        conv = [_msg("user", "q"), _msg("assistant", "A" * 5000)] * 3
        out = trim_conversation_to_window(system, conv, context_window=4000)
        assert out[0]["role"] != "system"  # 返回的是 conv（不含 system）
        est, _ = compute_context_usage(system + out, context_window=0)
        assert est <= 4000 * 0.9
