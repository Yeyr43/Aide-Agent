"""UIBridge — kernel ↔ Textual 桥接层。

实现 ExecutorUI Protocol，把 kernel 事件翻译为 Textual widget 操作。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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

    def on_thinking_token(self, token: str) -> None:
        self._msg_list().add_thinking_chunk(token)

    def on_text_done(self) -> None:
        msg_list = self._msg_list()
        if msg_list.has_pending():
            self._last_ai_text = msg_list.finish_ai_message()

    def on_tool_start(self, tool_name: str, arguments: dict) -> None:
        self._msg_list().add_tool_start(tool_name, arguments)

    def on_tool_done(self, tool_name: str, result: str) -> None:
        self._msg_list().add_tool_done(tool_name, result)

    def on_tool_error(self, tool_name: str, error: str) -> None:
        self._msg_list().add_tool_error(tool_name, error)

    def on_max_turns(self) -> None:
        from core.kernel.fc_loop import MAX_LOOP_TURNS
        self._msg_list().add_system_notice(
            t("ui.bridge.max_turns", rounds=MAX_LOOP_TURNS)
        )

    def on_replace_streamed_text(self, clean_text: str) -> None:
        """XML fallback: 用干净文本替换已渲染的 AI 消息。"""
        self._msg_list().replace_streamed_text(clean_text)

    # on_captured_entries removed — P5 重构后使用 /reflect 替代实时截获

    # ── 文本收集 ──

    @property
    def last_ai_text(self) -> str:
        return self._last_ai_text

    def reset_text(self) -> None:
        self._last_ai_text = ""
