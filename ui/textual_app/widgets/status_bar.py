"""StatusBar — 底部状态栏。

左右分离布局：左边 token 条 + 模型名，右边 API 名贴窗口右边缘。
"""

from __future__ import annotations

from rich.text import Text
from textual.containers import Horizontal
from textual.widgets import Static


class StatusBar(Horizontal):
    """底部单行状态栏。

    用法:
        bar = self.query_one("#status-bar", StatusBar)
        bar.update_info(tokens=1200, token_pct=0.52, model="gpt-4o-mini", api_name="openai")
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
