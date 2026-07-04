"""UIBridge — kernel ↔ Textual 桥接层。

实现 ExecutorUI Protocol，把 kernel 事件翻译为 Textual widget 操作。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.kernel.protocols import ExecutorUI
from core.locale import t

if TYPE_CHECKING:
    from .app import AideApp

from .widgets.message_list import MessageList  # noqa: E402 — 运行时需要，供 query_one 类型检查

logger = logging.getLogger(__name__)


class UIBridge:
    """kernel ↔ Textual 桥接器。

    用法:
        bridge = UIBridge(app)
        result = await kernel.chat(msg, session_dir, turn, conv, ui=bridge)
    """

    def __init__(self, app: "AideApp") -> None:
        self._app = app
        self._last_ai_text: str = ""

    # ── ExecutorUI 实现 ──

    def _msg_list(self) -> MessageList:
        """获取消息列表 widget。"""
        return self._app.query_one("#messages", MessageList)

    def on_text_token(self, token: str) -> None:
        self._last_ai_text += token
        self._msg_list().add_ai_chunk(token)

    def on_text_done(self) -> None:
        msg_list = self._msg_list()
        if msg_list.has_pending():
            self._last_ai_text = msg_list.finish_ai_message()

    def on_tool_start(self, tool_name: str, arguments: dict) -> None:
        pass  # 不显示工具调用

    def on_tool_done(self, tool_name: str, result: str) -> None:
        pass

    def on_tool_error(self, tool_name: str, error: str) -> None:
        self._msg_list().add_error(
            t("ui.bridge.tool_error", name=tool_name, error=error)
        )

    def on_max_turns(self) -> None:
        self._msg_list().add_system_notice(
            t("ui.bridge.max_turns")
        )

    def on_replace_streamed_text(self, clean_text: str) -> None:
        """XML fallback: 用干净文本替换已渲染的 AI 消息。"""
        self._msg_list().replace_streamed_text(clean_text)

    def on_captured_entries(self, entries: list[dict]) -> None:
        """显示截获通知。"""
        if not entries:
            return
        lines = [t("ui.bridge.captured")]
        for e in entries[:3]:
            content = e.get("content", "")[:60]
            entry_type = e.get("type", "")
            tag = {"preferences": t("mem.label_preferences"), "workflows": t("mem.label_workflows"), "long_term_memory": t("mem.label_long_term_memory")}.get(entry_type, "")
            prefix = f"[{tag}] " if tag else ""
            lines.append(f"  • {prefix}{content}")
        if len(entries) > 3:
            lines.append(t("ui.bridge.and_more", n=len(entries) - 3))
        lines.append(t("ui.bridge.integrate_hint"))
        self._msg_list().add_command_result("\n".join(lines), title="Memory")

    # ── 文本收集 ──

    @property
    def last_ai_text(self) -> str:
        return self._last_ai_text

    def reset_text(self) -> None:
        self._last_ai_text = ""
