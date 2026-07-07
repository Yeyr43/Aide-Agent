"""StatusBar — 底部状态栏。

左右分离布局：左边 token 条 + 模型名，右边 API 名贴窗口右边缘。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.containers import Horizontal
from textual.widgets import Static

if TYPE_CHECKING:
    from core.kernel.protocols import TokenUsage


class StatusBar(Horizontal):
    """底部单行状态栏。

    用法:
        bar = self.query_one("#status-bar", StatusBar)
        bar.update_info(tokens=1200, token_pct=0.52, model="gpt-4o-mini", api_name="openai")

        # 高级：自动从 session 估算 token 用量
        bar.update_from_session(
            usage=result.usage,
            conversation=session.conversation,
            tools_schema=registry.get_schemas(),
            model="gpt-4o",
            api_name="openai",
            context_window=128000,
        )
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._tokens: int = 0
        self._token_pct: float = 0.0
        self._model: str = "—"
        self._api_name: str = ""
        self._context_window: int = 128000

    def compose(self) -> None:
        yield Static(id="status-left")
        yield Static(id="status-right")

    def update_info(
        self,
        tokens: int = 0,
        token_pct: float = 0.0,
        model: str = "",
        api_name: str = "",
        context_window: int | None = None,
    ) -> None:
        self._tokens = tokens
        self._token_pct = min(token_pct, 1.0) if token_pct else 0.0
        if model:
            self._model = model
        if api_name:
            self._api_name = api_name
        if context_window is not None:
            self._context_window = context_window

        self._build_display()

    def update_from_session(
        self,
        *,
        usage: TokenUsage | None = None,
        conversation: list[dict] | None = None,
        tools_schema: list[dict] | None = None,
        model: str = "",
        api_name: str = "",
        context_window: int = 128000,
    ) -> None:
        """从会话数据自动估算 token 用量并刷新显示。

        Chat 后优先使用 kernel 返回的准确计数，
        否则从 conversation 估算（会话恢复 / 未聊天时）。
        """
        from core.context.token_counter import compute_context_usage

        if usage is not None:
            estimated = usage.total_tokens
            pct = usage.context_pct
        elif conversation:
            estimated, pct = compute_context_usage(
                conversation, tools_schema or [],
                context_window=context_window,
            )
        else:
            estimated, pct = 0, 0.0

        self.update_info(
            tokens=estimated, token_pct=pct,
            model=model, api_name=api_name,
            context_window=context_window,
        )

    def _build_display(self) -> None:
        if self._context_window > 0 and self._token_pct > 0:
            filled = int(self._token_pct * 10)
            bar = "█" * filled + "░" * (10 - filled)
            pct = int(self._token_pct * 100)
            left = f"[{bar}] {pct}%"
        elif self._tokens > 0:
            left = f"{self._format_tokens(self._tokens)} tokens"
        else:
            left = "—"

        left_text = Text()
        left_text.append(left, style="bold")
        left_text.append(" " * 4)
        left_text.append(self._model, style="dim")

        self.query_one("#status-left", Static).update(left_text)
        self.query_one("#status-right", Static).update(
            f"API：{self._api_name}" if self._api_name else ""
        )

    @staticmethod
    def _format_tokens(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return str(n)
