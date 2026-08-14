"""Tests for core.llm_gateway.openai_compatible_provider — OpenAI-compatible provider factory."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from core.llm_gateway.openai_compatible_provider import OpenAICompatibleProvider


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
