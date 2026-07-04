"""Tests for kernel protocols — ExecutorUI, TokenUsage, ChatResult."""

import pytest
from core.kernel.protocols import ExecutorUI, TokenUsage, ChatResult


class TestTokenUsage:
    def test_defaults_are_zero(self):
        usage = TokenUsage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0

    def test_explicit_values(self):
        usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 50
        assert usage.total_tokens == 150

    def test_partial_init(self):
        """Only specify some fields."""
        usage = TokenUsage(prompt_tokens=100)
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0


class TestChatResult:
    def test_defaults(self):
        result = ChatResult()
        assert result.conversation == []
        assert result.assistant_text == ""
        assert result.captured_entries == []
        assert isinstance(result.usage, TokenUsage)

    def test_with_conversation(self):
        conv = [{"role": "user", "content": "hi"}]
        result = ChatResult(conversation=conv)
        assert result.conversation == conv

    def test_with_assistant_text(self):
        result = ChatResult(assistant_text="Hello!")
        assert result.assistant_text == "Hello!"

    def test_with_captured_entries(self):
        entries = [{"content": "user prefers short replies", "status": "pending"}]
        result = ChatResult(captured_entries=entries)
        assert len(result.captured_entries) == 1
        assert result.captured_entries[0]["status"] == "pending"

    def test_full_result(self):
        conv = [{"role": "user", "content": "hi"}]
        usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        result = ChatResult(
            conversation=conv,
            assistant_text="Hi!",
            captured_entries=[{"content": "x"}],
            usage=usage,
        )
        assert result.conversation == conv
        assert result.assistant_text == "Hi!"
        assert len(result.captured_entries) == 1
        assert result.usage.total_tokens == 15


class TestExecutorUIProtocol:
    """Verify ExecutorUI is a Protocol (not used at runtime, just for type-checking)."""

    def test_can_be_imported(self):
        """Protocol must be importable."""
        assert ExecutorUI is not None
