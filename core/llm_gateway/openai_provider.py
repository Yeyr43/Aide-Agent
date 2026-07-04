"""OpenAI Provider — 适配 OpenAI Chat Completions API (SSE 流)。"""

from .openai_compatible_provider import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI Chat Completions API 适配器。

    使用 /v1/chat/completions 端点，stream=true。
    """

    _TIMEOUT: float = 60.0
    _LOG_PREFIX: str = "OpenAI API"

    def __init__(self, model: str, base_url: str, api_key: str = "",
                 supports_vision: bool = False) -> None:
        super().__init__(model, base_url, api_key, supports_vision)
