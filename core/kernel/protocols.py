"""Kernel 协议定义 — ExecutorUI 等回调接口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class ExecutorUI(Protocol):
    """FC 循环 → UI 层回调接口。UI 层实现此接口，Kernel 零 Textual 依赖。"""

    def on_text_token(self, token: str) -> None: ...
    def on_text_done(self) -> None: ...
    def on_tool_start(self, tool_name: str, arguments: dict) -> None: ...
    def on_tool_done(self, tool_name: str, result: str) -> None: ...
    def on_tool_error(self, tool_name: str, error: str) -> None: ...
    def on_max_turns(self) -> None: ...
    def on_replace_streamed_text(self, clean_text: str) -> None:
        """XML fallback: 用干净文本替换已流式渲染的带 <invoke> 的文本。"""
        ...
    def on_captured_entries(self, entries: list[dict]) -> None:
        """截获通知：规则引擎从对话中捕获了新条目。"""
        ...


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0  # 上下文估算 token 数（状态栏使用）
    context_pct: float = 0.0  # 0.0 ~ 1.0，上下文字窗口使用率


@dataclass
class ChatResult:
    conversation: list[dict] = field(default_factory=list)
    assistant_text: str = ""
    captured_entries: list[dict] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
