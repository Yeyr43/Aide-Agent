"""LLM Gateway — 统一 LLM 调用入口。

P0: create_provider() 工厂函数 + 纯文本 chat()
P1: 扩展 function calling (chat_with_tools), 导出 StreamEvent 类型
P4: 新增 Anthropic provider (anthropic 协议原生适配)
"""

from core.config import LLMConfig

from .provider import AbstractProvider, TextDelta, StreamEnd
from .openai_compatible_provider import OpenAICompatibleProvider
from .openai_provider import OpenAIProvider
from .ollama_provider import OllamaProvider
from .anthropic_provider import AnthropicProvider

__all__ = [
    "create_provider",
    "AbstractProvider",
    "TextDelta",
    "StreamEnd",
    "OpenAICompatibleProvider",
    "OpenAIProvider",
    "OllamaProvider",
    "AnthropicProvider",
]


def create_provider(config: LLMConfig):
    """根据配置创建对应的 LLM Provider 实例。

    Args:
        config: LLMConfig，包含 provider/model/base_url/api_key

    Returns:
        OpenAIProvider / OllamaProvider / AnthropicProvider 实例

    Raises:
        ValueError: 不支持的 provider 类型
    """
    if config.provider == "openai":
        return OpenAIProvider(
            model=config.model,
            base_url=config.base_url,
            api_key=config.api_key,
            supports_vision=config.supports_vision,
        )
    elif config.provider == "ollama":
        return OllamaProvider(
            model=config.model,
            base_url=config.base_url,
            api_key=config.api_key,
            supports_vision=config.supports_vision,
        )
    elif config.provider == "anthropic":
        return AnthropicProvider(
            model=config.model,
            base_url=config.base_url,
            api_key=config.api_key,
            supports_vision=config.supports_vision,
        )
    else:
        from core.locale import t
        raise ValueError(t("llm.unsupported_provider",
                          provider=config.provider,
                          supported="openai, ollama, anthropic"))
