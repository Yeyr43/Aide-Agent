"""Aide 统一错误类型。

所有 Aide 特定的异常从此模块导出，避免各处使用裸 ValueError/RuntimeError。
调用者可以按需捕获具体子类，或兜底捕获 AideError。

用法:
    from core.errors import ProviderError, ConfigError, SessionError
    raise ProviderError("API key 无效", provider="openai")
    raise ConfigError("settings.json 损坏")
"""

from __future__ import annotations


class AideError(Exception):
    """Aide 所有异常的基类。"""
    ...


# ── Provider 层 ─────────────────────────────────────────────────────────

class ProviderError(AideError):
    """LLM Provider 调用失败（含认证、速率限制、网络错误）。"""

    def __init__(self, message: str, provider: str = "",
                 status_code: int | None = None,
                 retry_after: float | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retry_after = retry_after


# ── 工具层 ─────────────────────────────────────────────────────────────

class ToolError(AideError):
    """工具执行失败（超时、参数无效、执行异常）。"""

    def __init__(self, message: str, tool: str = "",
                 arguments: dict | None = None) -> None:
        super().__init__(message)
        self.tool = tool
        self.arguments = arguments or {}


# ── 配置层 ─────────────────────────────────────────────────────────────

class ConfigError(AideError):
    """配置错误 — API 名不存在、settings.json 损坏、必填字段缺失。"""
    ...


# ── 会话层 ─────────────────────────────────────────────────────────────

class SessionError(AideError):
    """会话错误 — 会话不存在、回滚失败、数据损坏。"""
    ...
