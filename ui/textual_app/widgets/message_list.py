"""消息流组件 — 树形回合展示。

每个 assistant 回合 = 一棵 TurnTree（tree_nodes.py）。
用户消息保留气泡框（MessageWidget），与树之间空一行。
流式：think 展开 → 结束折叠；工具精简单行；正文流式 Markdown。
交互：左键双击折叠/展开（可折叠节点）；右键点击复制。
"""

import logging
import time

from rich.panel import Panel
from rich.markup import escape
from rich.text import Text
from textual.containers import VerticalScroll
from textual.events import Click
from textual.widgets import Static

from .tree_nodes import (
    ThinkNode, ToolNode, BodyNode, ErrorNode, SystemNode, TurnTree,
)

logger = logging.getLogger(__name__)


class MessageWidget(Static):
    """用户消息组件：保留气泡框。双击打开附件文件，右键复制。"""

    def __init__(self, content: str = "", renderable=None,
                 image_paths: list[str] | None = None,
                 file_paths: list[str] | None = None, **kwargs) -> None:
        super().__init__(renderable if renderable is not None else "", **kwargs)
        self._plain_content = content
        self._image_paths = image_paths or []
        self._file_paths = file_paths or []
        self._last_click_time = 0.0
        self.DOUBLE_CLICK_MS = 400

    def on_click(self, event: Click) -> None:
        if event.button == 3:  # 右键 → 复制
            self._copy_to_clipboard()
            return
        if event.button != 1:
            return
        now = time.monotonic()
        elapsed = (now - self._last_click_time) * 1000
        self._last_click_time = now
        if 0 < elapsed < self.DOUBLE_CLICK_MS:
            all_files = self._image_paths + [
                f for f in self._file_paths if f not in self._image_paths
            ]
            if all_files:
                self._open_files(all_files)

    def _open_files(self, paths: list[str]) -> None:
        from core.llm_gateway.image_utils import open_with_os
        for p in paths:
            open_with_os(p)

    def _copy_to_clipboard(self) -> None:
        text = (self._plain_content or "").strip()
        if not text:
            return
        try:
            import pyperclip
            pyperclip.copy(text)
        except Exception:
            logger.warning("clipboard copy failed", exc_info=True)


class MessageList(VerticalScroll):
    """聊天消息列表 — 回合树流式管理器。"""

    def __init__(self, code_theme: str = "monokai", **kwargs) -> None:
        super().__init__(**kwargs)
        self._code_theme = code_theme
        self._current_turn: TurnTree | None = None
        self._think_node: ThinkNode | None = None
        self._body_node: BodyNode | None = None
        self._tool_fifo: dict[str, list[ToolNode]] = {}
        self._tool_start_times: dict[int, float] = {}
        self._turn_ai_text = ""

    # ── 回合树管理 ──────────────────────────────────────────────

    def _ensure_turn(self) -> TurnTree:
        if self._current_turn is None:
            tree = TurnTree()
            tree.add_class("turn-tree")
            self.mount(tree)
            self._current_turn = tree
        return self._current_turn

    def _close_open_text(self) -> None:
        """折叠当前 think + 收尾当前正文（若存在）。"""
        if self._think_node is not None:
            self._think_node.finish()
            self._think_node = None
        if self._body_node is not None:
            self._body_node.finish()
            self._body_node = None

    def _close_turn(self) -> None:
        """关闭当前回合树（下一条用户消息 / 回合结束）。"""
        self._close_open_text()
        self._current_turn = None
        self._turn_ai_text = ""

    # ── 用户消息 ────────────────────────────────────────────────

    def add_user_message(self, text: str, file_paths: list[str] | None = None) -> None:
        self._close_turn()

        display_lines: list[str] = []
        image_paths: list[str] = []
        all_file_paths: list[str] = []
        display_text = text or ""

        if file_paths:
            from pathlib import Path
            from core.llm_gateway.image_utils import is_image_path
            for p in file_paths:
                name = Path(p).name
                display_lines.append(f"[{name}]")
                if is_image_path(p):
                    image_paths.append(p)
                all_file_paths.append(p)
            for p in file_paths:
                display_text = display_text.replace(p, "")
            display_text = display_text.strip()

        if display_text:
            display_lines.append(display_text)

        display = "\n".join(display_lines) if display_lines else ""
        content = Text.from_markup(escape(display))
        msg = MessageWidget(
            display_text or ("\n".join(display_lines)),
            renderable=Panel(content, border_style="#555555",
                             title="You", title_align="right"),
            image_paths=image_paths if image_paths else None,
            file_paths=all_file_paths if all_file_paths else None,
        )
        msg.add_class("user-message")
        self.mount(msg)
        self._scroll_end()

    # ── 思考 ────────────────────────────────────────────────────

    def add_thinking_chunk(self, chunk: str) -> None:
        if self._think_node is None:
            tree = self._ensure_turn()
            self._think_node = ThinkNode()
            tree.add_node(self._think_node, "think")
        self._think_node.append_chunk(chunk)
        self._scroll_end()

    # ── 工具 ────────────────────────────────────────────────────

    def add_tool_start(self, tool_name: str, arguments: dict) -> None:
        self._close_open_text()  # 工具开始 → 折叠当前 think / 收尾正文
        tree = self._ensure_turn()
        node = ToolNode(tool_name, arguments)
        tree.add_node(node, "tool")
        self._tool_fifo.setdefault(tool_name, []).append(node)
        self._tool_start_times[id(node)] = time.monotonic()
        self._scroll_end()

    def _pop_tool_node(self, tool_name: str) -> ToolNode | None:
        q = self._tool_fifo.get(tool_name)
        if not q:
            return None
        node = q.pop(0)
        if not q:
            del self._tool_fifo[tool_name]
        return node

    def _elapsed(self, node_id: int) -> float | None:
        start = self._tool_start_times.pop(node_id, None)
        if start is None:
            return None
        return time.monotonic() - start

    def add_tool_done(self, tool_name: str, result: str) -> None:
        node = self._pop_tool_node(tool_name)
        if node is None:
            return
        node.set_result(result)
        node.set_duration(self._elapsed(id(node)))
        self._scroll_end()

    def add_tool_error(self, tool_name: str, error: str) -> None:
        node = self._pop_tool_node(tool_name)
        if node is None:
            # 无配对节点（如 LLM 错误 / 被拦截工具）→ 独立错误节点
            tree = self._ensure_turn()
            node = ErrorNode(f"{tool_name}: {error}")
            tree.add_node(node, "error")
            return
        node.set_error(error)
        node.set_duration(self._elapsed(id(node)))
        self._scroll_end()

    # ── 正文 ────────────────────────────────────────────────────

    def add_ai_chunk(self, chunk: str) -> None:
        if self._think_node is not None:  # 正文开始 → 思考折叠
            self._think_node.finish()
            self._think_node = None
        tree = self._ensure_turn()
        if self._body_node is None:
            self._body_node = BodyNode(code_theme=self._code_theme)
            tree.add_node(self._body_node, "body")
        self._body_node.append_chunk(chunk)
        self._turn_ai_text += chunk
        self._scroll_end()

    def finish_ai_message(self) -> str:
        text = self._turn_ai_text
        self._close_turn()
        return text

    def replace_streamed_text(self, clean_text: str) -> None:
        """XML fallback：用干净文本替换当前流式正文。"""
        self._turn_ai_text = clean_text
        if self._body_node is not None:
            self._body_node.replace_content(clean_text)

    # ── 错误 / 系统 / 命令 ──────────────────────────────────────

    def add_error(self, text: str) -> None:
        tree = self._ensure_turn()
        tree.add_node(ErrorNode(text), "error")
        self._scroll_end()

    def add_system_notice(self, text: str) -> None:
        tree = self._ensure_turn()
        tree.add_node(SystemNode(text), "system")
        self._scroll_end()

    def add_command_result(self, text: str, title: str = "Command") -> None:
        tree = self._ensure_turn()
        tree.add_node(SystemNode(text), "system")
        self._scroll_end()

    # ── 状态 ────────────────────────────────────────────────────

    def has_pending(self) -> bool:
        return self._think_node is not None or self._body_node is not None

    def clear(self) -> None:
        self._current_turn = None
        self._think_node = None
        self._body_node = None
        self._tool_fifo.clear()
        self._tool_start_times.clear()
        self._turn_ai_text = ""
        for child in list(self.children):
            child.remove()

    def restore_conversation(self, messages: list[dict]) -> None:
        for msg in messages:
            role = msg.get("role", "")
            raw_content = msg.get("content", "")
            text, images = _parse_multimodal_content(raw_content)
            if role == "user" and (text or images):
                file_paths = msg.get("_image_paths", []) or images
                self.add_user_message(text or "", file_paths=file_paths)
            elif role == "assistant" and text:
                self._add_restored_body(text)

    def _add_restored_body(self, text: str) -> None:
        """恢复的历史 assistant 消息 = 只有正文节点的回合树。"""
        self._close_turn()
        tree = self._ensure_turn()
        node = BodyNode(code_theme=self._code_theme)
        node.set_finished_text(text)
        tree.add_node(node, "body")
        self._close_turn()

    def _scroll_end(self) -> None:
        self.scroll_end(animate=False)


# ── 多模态 content 解析 ─────────────────────────────────────────────────

def _parse_multimodal_content(content) -> tuple[str, list[str]]:
    """解析多模态 content，提取文本和图片 data URL。

    Args:
        content: str（纯文本）或 list[dict]（OpenAI 多模态 content 数组）

    Returns:
        (text, images_data_urls)
    """
    if isinstance(content, str):
        return content, []
    if isinstance(content, list):
        text_parts: list[str] = []
        image_urls: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif part.get("type") == "image_url":
                    iu = part.get("image_url", {})
                    if isinstance(iu, dict):
                        url = iu.get("url", "")
                        if url:
                            image_urls.append(url)
        return "\n".join(text_parts), image_urls
    return str(content), []
