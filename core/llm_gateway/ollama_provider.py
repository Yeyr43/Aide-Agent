"""Ollama Provider — 适配 Ollama OpenAI 兼容端点 (SSE 流)。

Ollama v0.5+ 内置 OpenAI 兼容 API，端点格式与 OpenAI 一致。
适配器只负责拼 localhost base_url，SSE 解析完全复用。
"""

from .openai_compatible_provider import OpenAICompatibleProvider


class OllamaProvider(OpenAICompatibleProvider):
    """Ollama OpenAI-compatible API 适配器。

    默认连接本地 11434 端口，api_key 为占位值（Ollama 不需要认证）。
    """

    _TIMEOUT: float = 120.0
    _LOG_PREFIX: str = "Ollama API"

    def __init__(self, model: str, base_url: str, api_key: str = "ollama",
                 supports_vision: bool = False) -> None:
        super().__init__(model, base_url, api_key, supports_vision)
