"""用户输入组件 — Enter 发送，Ctrl+Enter / Shift+Enter 换行。
P4：TextArea 多行输入，自动扩展（1→2→3行），超出滚动（无滚动条）。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from PIL.Image import Image as PILImage

from textual import events
from textual.binding import Binding
from textual.widgets import TextArea
from textual.message import Message

from core.locale import t

# 用于快速检测文本中是否包含绝对路径特征（Windows 盘符/UNC + POSIX /）
_HAS_PATH_ROOT = re.compile(
    r'(?:[A-Za-z]:[/\\]'        # Windows: C:\ 或 D:/
    r'|\\\\[^\\s]+\\[^\\s]+'    # Windows UNC: \\server\share
    r'|^/)'                       # POSIX: /home/user/file
)

logger = logging.getLogger(__name__)


class InputBox(TextArea):
    """多行用户消息输入框。

    - Enter → 提交消息（UserSubmitted）
    - Ctrl+J / Shift+Enter → 插入换行（Ctrl+Enter 仅 kitty 终端可用）
    - Ctrl+A → 全选（覆盖 TextArea 默认的 cursor_line_start）
    - 高度自适应：1 行 → 2 行 → 最大 3 行，超出滚轮滚动
    - `/` 开头时驱动命令面板（CommandInput）
    P4 多模态：粘贴时自动捕获剪贴板图片 + 拖放文件路径。
    """

    BINDINGS = [
        Binding("ctrl+a", "select_all", "全选", show=False),
        # 禁用不适用于聊天输入框的 TextArea 默认快捷键
        Binding("ctrl+z", "", show=False),        # Undo — 聊天框不需要，且 Textual 有 bug
        Binding("ctrl+y", "", show=False),        # Redo
        Binding("f6", "", show=False),
        Binding("f7", "", show=False),
        Binding("pageup", "", show=False),
        Binding("pagedown", "", show=False),
        Binding("ctrl+u,super+backspace", "", show=False),
        Binding("ctrl+k", "", show=False),
        Binding("ctrl+shift+k", "", show=False),
        Binding("alt+delete", "", show=False),
        Binding("ctrl+w,ctrl+backspace,alt+backspace", "", show=False),
        Binding("ctrl+shift+left", "", show=False),
        Binding("ctrl+shift+right", "", show=False),
        Binding("ctrl+p", "", show=False),
        Binding("ctrl+n", "", show=False),
    ]

    class UserSubmitted(Message):
        """用户提交了一条有效消息。

        Attributes:
            text: 文本内容
            file_paths: 附件文件路径列表
            clipboard_images: 剪贴板中的 PIL Image 列表
        """

        def __init__(self, text: str,
                     file_paths: list[str] | None = None,
                     clipboard_images: list[PILImage] | None = None) -> None:
            self.text = text
            self.file_paths = file_paths or []
            self.clipboard_images = clipboard_images or []
            super().__init__()

    class CommandInput(Message):
        """用户正在输入命令（以 / 开头）。"""

        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    MAX_LINES = 3

    def __init__(self, placeholder: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._placeholder = placeholder
        self._showing_placeholder = False
        self._pending_files: list[str] = []
        self._pending_clipboard_images: list[PILImage] = []
        self._snapping_cursor = False  # 防止 _snap_cursor_out_of_tokens 递归

        # TextArea 默认行为关闭
        self.show_line_numbers = False

        # 高度自适应（覆盖 TextArea DEFAULT_CSS 的 height: 1fr）
        # height = 内容行数 + 2（上下边框各 1 行）
        self.styles.height = 3  # 1 行内容
        self.styles.max_height = self.MAX_LINES + 2  # 3 行内容
        self.styles.overflow_y = "auto"

    # ── placeholder 属性 (TextArea 无原生支持) ─────────────────────────

    @property
    def placeholder(self) -> str:
        return self._placeholder

    @placeholder.setter
    def placeholder(self, value: str) -> None:
        self._placeholder = value
        if self._showing_placeholder:
            self._show_placeholder_text()

    def _show_placeholder_text(self) -> None:
        """在 TextArea 中显示占位文本（灰色）。"""
        if self._placeholder and self.text == "":
            self._showing_placeholder = True
            self.load_text(self._placeholder)
            self.styles.color = "#555555"
        else:
            self._showing_placeholder = False
            self.styles.color = "#c8c8c0"

    def _clear_placeholder(self) -> None:
        """清除占位文本。"""
        if self._showing_placeholder:
            self._showing_placeholder = False
            self.load_text("")
            self.styles.color = "#c8c8c0"

    # ── value 属性 (兼容旧 Input API) ───────────────────────────────────

    @property
    def value(self) -> str:
        """返回当前文本（placeholder 状态下返回空字符串）。"""
        if self._showing_placeholder:
            return ""
        return self.text

    @value.setter
    def value(self, val: str) -> None:
        self._showing_placeholder = False
        self.styles.color = "#c8c8c0"
        self.load_text(val)

    # ── 聚焦事件 (placeholder 管理) ─────────────────────────────────────

    def _on_focus(self, event: events.Focus) -> None:
        """聚焦时清除 placeholder。"""
        self._clear_placeholder()
        super()._on_focus(event)

    def _on_blur(self, event: events.Blur) -> None:
        """失焦时如果无内容则显示 placeholder。"""
        super()._on_blur(event)
        if self.text.strip() == "" and not self._pending_files and not self._pending_clipboard_images:
            self.load_text("")
            self._show_placeholder_text()

    # ── 挂载后显示 placeholder ─────────────────────────────────────────

    def on_mount(self) -> None:
        """挂载后如果无初始文本则显示 placeholder。"""
        if self.text == "" and not self._pending_files and not self._pending_clipboard_images:
            self._show_placeholder_text()

    # ── 按键处理 ───────────────────────────────────────────────────────

    # 需要先清除 placeholder 再处理的按键（修改内容的键）
    _CONTENT_KEYS = frozenset({
        "backspace", "delete", "space",
    })

    async def _on_key(self, event: events.Key) -> None:
        """Enter 提交，Ctrl+J/Ctrl+Enter/Shift+Enter 换行。"""
        if event.key in ("ctrl+j", "ctrl+enter", "shift+enter"):
            # Ctrl+J / Shift+Enter → 插入换行
            self._clear_placeholder()
            self.insert("\n")
            event.prevent_default()
        elif event.key == "enter":
            # 普通 Enter → 提交
            event.prevent_default()
            if self._showing_placeholder:
                return
            text = self.text.strip()
            if text == "" and not self._pending_files and not self._pending_clipboard_images:
                return
            self._post_submit(text)
        elif event.key == "backspace" and not self._showing_placeholder:
            # token 整体删除：光标在 [filename] 末尾或内部 → 整个删除
            if self._handle_token_backspace():
                event.prevent_default()
            else:
                await super()._on_key(event)
        elif event.key == "delete" and not self._showing_placeholder:
            # token 整体删除：光标在 [filename] 开头或内部 → 整个删除
            if self._handle_token_delete():
                event.prevent_default()
            else:
                await super()._on_key(event)
        elif self._showing_placeholder:
            # 显示 placeholder 时：修改内容的键先清除 placeholder
            if event.character or event.key in self._CONTENT_KEYS:
                self._clear_placeholder()
            await super()._on_key(event)
        else:
            await super()._on_key(event)

    def _post_submit(self, text: str) -> None:
        """发送 UserSubmitted 并清空输入框。

        text 是输入框显示文本（含 [文件名] / [图片] token）。
        替换 [文件名] 为完整路径后发送给 LLM，前端仍渲染 chip。
        """
        files = list(self._pending_files)
        images = list(self._pending_clipboard_images)
        self._pending_files = []
        self._pending_clipboard_images = []

        # 替换 [文件名] token → 完整路径（给 LLM 看真实路径）
        submitted_text = text
        for p in files:
            submitted_text = submitted_text.replace(f"[{Path(p).name}]", p, 1)

        self.load_text("")
        self.border_subtitle = ""
        self._update_placeholder()

        self.post_message(self.UserSubmitted(
            submitted_text, file_paths=files, clipboard_images=images,
        ))

    # ── 文本变化事件 ───────────────────────────────────────────────────

    def _on_text_area_changed(self, event: TextArea.Changed) -> None:
        """输入变化时驱动命令面板 + 检测拖放文件 + 同步高度。"""
        if self._showing_placeholder:
            return
        self._detect_dropped_files()
        text = event.text_area.text.strip()
        self.post_message(self.CommandInput(text))
        self.call_after_refresh(self._sync_height)
        self._update_line_counter()

    def _detect_dropped_files(self) -> None:
        """检测 TextArea 内容中的绝对文件路径，自动提取为附件。

        仅当全文本都是存在的文件路径时才触发（拖放/粘贴场景），
        用户手打文本+路径混排时不触发。
        """
        raw = self.text
        if not raw.strip():
            return
        if "\\" not in raw and "/" not in raw:
            return
        if not _HAS_PATH_ROOT.search(raw):
            return

        from core.llm_gateway.image_utils import extract_file_paths
        remaining, paths = extract_file_paths(raw)
        if not paths:
            return
        if remaining.strip():
            return

        existing: set[str] = set(self._pending_files)
        new_paths = [p for p in paths if p not in existing]
        if not new_paths:
            return
        self._pending_files.extend(new_paths)

        # 替换完整路径为 [文件名] 纯文本，光标在末尾
        names = [f"[{Path(p).name}]" for p in self._pending_files]
        for _ in self._pending_clipboard_images:
            names.append("[图片]")
        self.load_text(" ".join(names) + " ")
        self.cursor_location = self.document.end
        self._update_placeholder()

    def _on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        """光标移动时更新行号显示 + 防止光标进入 [filename] token 内部。"""
        self._snap_cursor_out_of_tokens()
        self._update_line_counter()

    def _update_line_counter(self) -> None:
        """在右上角边框中显示 [当前行/总行数]，仅多行时显示。"""
        if self._showing_placeholder or not self.is_mounted:
            return
        try:
            total = self.wrapped_document.height
        except Exception:
            logger.debug("Failed to get wrapped_document height in _update_line_counter, skipping")
            self.border_subtitle = ""
            return
        if total <= 1:
            self.border_subtitle = ""
            return
        _, row = self.wrapped_document.location_to_offset(self.cursor_location)
        self.border_subtitle = f"[{row + 1}/{total}]"

    def on_resize(self) -> None:
        """宽度变化时重新计算高度（影响自动换行）。"""
        self.call_after_refresh(self._sync_height)

    def _sync_height(self) -> None:
        """根据内容行数动态设置高度：1→2→3 行，超出滚动。"""
        if not self.is_mounted or self._showing_placeholder:
            return
        try:
            lines = self.wrapped_document.height
        except Exception:
            logger.debug("Failed to get wrapped_document height in _sync_height, skipping")
            return
        h = min(max(1, lines), self.MAX_LINES) + 2
        if self.styles.height != h:
            self.styles.height = h

    # ── 附件管理 ─────────────────────────────────────────────────────

    def remove_attachment(self, index: int) -> None:
        """移除单个附件（按标签索引）。"""
        image_count = len(self._pending_clipboard_images)
        if index < len(self._pending_files):
            del self._pending_files[index]
        elif index < len(self._pending_files) + image_count:
            img_idx = index - len(self._pending_files)
            del self._pending_clipboard_images[img_idx]
        self._update_placeholder()

    def clear_attachments(self) -> None:
        """清空所有附件。"""
        self._pending_files = []
        self._pending_clipboard_images = []
        self._update_placeholder()

    # ── Token 管理（[filename] / [图片] 整体删除 + 光标隔离）──────────

    def _token_ranges(self) -> list[tuple[int, int, int]]:
        """返回 [(start, end, file_index)] 当前文本中所有附件 token 的位置。

        file_index 为 _pending_files 索引，负数 -1-i 表示 _pending_clipboard_images[i]。
        """
        text = self.text
        ranges: list[tuple[int, int, int]] = []

        for i, p in enumerate(self._pending_files):
            name = f"[{Path(p).name}]"
            start = 0
            while True:
                idx = text.find(name, start)
                if idx == -1:
                    break
                ranges.append((idx, idx + len(name), i))
                start = idx + len(name)

        for i in range(len(self._pending_clipboard_images)):
            name = "[图片]"
            start = 0
            while True:
                idx = text.find(name, start)
                if idx == -1:
                    break
                ranges.append((idx, idx + len(name), -1 - i))
                start = idx + len(name)

        return sorted(ranges)

    def _char_offset(self) -> int:
        """返回光标字符偏移量（基于 text 手动计算，避免 Textual Document API 兼容问题）。"""
        try:
            row, col = self.cursor_location
            lines = self.text.split("\n")
            offset = sum(len(lines[i]) + 1 for i in range(min(row, len(lines))))
            return offset + col
        except Exception:
            logger.debug("Failed to calculate cursor offset, returning 0")
            return 0

    def _char_to_location(self, offset: int) -> tuple[int, int]:
        """将字符偏移量转换为 (row, col)。"""
        remaining = offset
        for row_idx, line in enumerate(self.text.split("\n")):
            if remaining <= len(line):
                return (row_idx, remaining)
            remaining -= len(line) + 1  # +1 for \n
        return (len(self.text.split("\n")) - 1, remaining)

    def _handle_token_backspace(self) -> bool:
        """Backspace 在 token 末尾或内部 → 整体删除 token。

        Returns:
            True 如果事件已被处理（不应再传递给 super）。
        """
        offset = self._char_offset()
        for start, end, file_idx in self._token_ranges():
            if start < offset <= end:
                self._delete_token(start, end, file_idx)
                return True
        return False

    def _handle_token_delete(self) -> bool:
        """Delete 在 token 开头或内部 → 整体删除 token。"""
        offset = self._char_offset()
        for start, end, file_idx in self._token_ranges():
            if start <= offset < end:
                self._delete_token(start, end, file_idx)
                return True
        return False

    def _delete_token(self, start: int, end: int, file_idx: int) -> None:
        """删除 token 文本并从 _pending_files/_pending_clipboard_images 中移除对应附件。"""
        text = self.text
        new_text = text[:start] + text[end:]
        self.load_text(new_text)
        self.cursor_location = self._char_to_location(start)

        if file_idx >= 0:
            if file_idx < len(self._pending_files):
                del self._pending_files[file_idx]
        else:
            img_idx = -1 - file_idx
            if img_idx < len(self._pending_clipboard_images):
                del self._pending_clipboard_images[img_idx]

        self._update_placeholder()

    def _snap_cursor_out_of_tokens(self) -> None:
        """如果光标在 token 内部，跳到最近的边界（左或右）。

        通过 _snapping_cursor 标志防止递归（设置 cursor_location
        会触发 SelectionChanged → 再次进入本方法）。
        """
        if self._snapping_cursor:
            return
        offset = self._char_offset()
        for start, end, _ in self._token_ranges():
            if start < offset < end:
                self._snapping_cursor = True
                try:
                    if offset - start <= end - offset:
                        new_loc = self._char_to_location(start)
                    else:
                        new_loc = self._char_to_location(end)
                    self.cursor_location = new_loc
                finally:
                    self._snapping_cursor = False
                return

    # ── 粘贴处理 ─────────────────────────────────────────────────────

    async def _on_paste(self, event: events.Paste) -> None:
        """处理粘贴。
        文本路径检测由 _detect_dropped_files 统一处理
        （在 _on_text_area_changed 中触发）。
        剪贴板图片在粘贴完成后检测。
        """
        event.prevent_default()
        event.stop()

        self._clear_placeholder()
        await super()._on_paste(event)

        try:
            from core.llm_gateway.image_utils import grab_clipboard_image, resize_if_needed
            img = grab_clipboard_image()
            if img is not None:
                self._pending_clipboard_images.append(resize_if_needed(img))
                self.load_text("")
                self._update_placeholder()
                if not self.text:
                    self._show_placeholder_text()
        except Exception:
            logger.debug("Failed to grab clipboard image on paste, skipping")

    def _update_placeholder(self) -> None:
        """更新占位符文本（不触发显示）。"""
        self._placeholder = t("ui.widget.input_placeholder")
