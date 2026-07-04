"""消息流组件 — 等宽边框 Panel，Markdown + Pygments。

消息等宽居中，AI 回复不显示名字。
流式阶段 Text.from_markup（快），完成阶段 RichMarkdown（Pygments 高亮）。
双击消息框复制内容到剪贴板。
"""

import time
from rich.markdown import Markdown as RichMarkdown
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static
from textual.events import Click


class MessageWidget(Static):
    """可双击复制的消息组件。

    每个消息独立跟踪自己的双击状态，双击后复制内容到剪贴板。
    有图片/文件路径时，双击打开文件，双击其他区域复制文本。
    """

    def __init__(self, content: str = "", renderable=None,
                 image_paths: list[str] | None = None,
                 file_paths: list[str] | None = None, **kwargs) -> None:
        super().__init__(renderable if renderable is not None else "", **kwargs)
        self._plain_content = content  # 纯文本原文，供复制使用
        self._image_paths = image_paths or []  # 关联的图片文件路径
        self._file_paths = file_paths or []    # 所有附件文件路径（含非图片）
        self._last_click_time: float = 0.0
        self.DOUBLE_CLICK_MS = 400

    def on_click(self, event: Click) -> None:
        """双击检测 → 有文件路径则打开文件，否则复制文本。"""
        now = time.monotonic()
        elapsed = (now - self._last_click_time) * 1000
        self._last_click_time = now

        if 0 < elapsed < self.DOUBLE_CLICK_MS:
            all_files = self._image_paths + [
                f for f in self._file_paths if f not in self._image_paths
            ]
            if all_files:
                self._open_files(all_files)
            else:
                self._copy_to_clipboard()

    def _open_files(self, paths: list[str]) -> None:
        """用系统默认程序打开文件（图片用 _open_images 兼容）。"""
        from core.llm_gateway.image_utils import open_with_os
        for p in paths:
            open_with_os(p)

    def _copy_to_clipboard(self) -> None:
        """复制消息内容到系统剪贴板。"""
        text = self._plain_content
        if not text:
            # 尝试从 renderable 提取
            try:
                r = self.renderable
                if isinstance(r, Panel):
                    inner = r.renderable
                    if isinstance(inner, Text):
                        text = inner.plain
                    elif isinstance(inner, RichMarkdown):
                        text = inner.markup
                    elif isinstance(inner, str):
                        text = inner
                    else:
                        text = str(inner)
                elif isinstance(r, Text):
                    text = r.plain
                elif isinstance(r, str):
                    text = r
            except Exception:
                return

        text = (text or "").strip()
        if not text:
            return

        try:
            import pyperclip
            pyperclip.copy(text)
        except Exception:
            pass


class MessageList(VerticalScroll):
    """聊天消息列表。

    每条消息是 MessageWidget（等宽 Panel、双击复制）。
    AI 流式复用同一个 Static，update 而非新建。
    流式阶段 Text.from_markup → 完成阶段 RichMarkdown。
    """

    def __init__(self, code_theme: str = "monokai", **kwargs) -> None:
        super().__init__(**kwargs)
        self._ai_stream: Static | None = None
        self._ai_buffer: str = ""
        self._code_theme = code_theme

    # ── 用户消息（右对齐，有边框） ──────────────────────────────────

    def add_user_message(self, text: str, file_paths: list[str] | None = None) -> None:
        """添加用户消息（含可选附件文件）。

        Args:
            text: 消息文本（可能包含完整文件路径，由 InputBox 发送）
            file_paths: 可选的附件文件路径列表（图片/普通文件）
        """
        display_lines: list[str] = []
        image_paths: list[str] = []  # 图片文件可双击打开
        all_file_paths: list[str] = []  # 所有文件（含非图片），供双击打开

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
            # 从显示文本中剥离完整路径（已渲染为 [文件名] chip，避免重复）
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

    # ── AI 流式消息（无名字，无标题） ────────────────────────────────

    def add_ai_chunk(self, chunk: str) -> None:
        if self._ai_stream is None:
            self._ai_stream = Static("")
            self._ai_stream.add_class("ai-message")
            self.mount(self._ai_stream)

        self._ai_buffer += chunk
        # escape Rich markup 元字符（[u8;32] 会被当 tag 吞掉）
        content = Text.from_markup(escape(self._ai_buffer))
        self._ai_stream.update(
            Panel(content, border_style="#555555"),
        )
        self._scroll_end()

    def replace_streamed_text(self, clean_text: str) -> None:
        """XML fallback: 用干净文本替换当前正在渲染的 AI 消息。"""
        self._ai_buffer = clean_text
        if self._ai_stream is not None:
            content = Text.from_markup(escape(clean_text))
            self._ai_stream.update(
                Panel(content, border_style="#555555"),
            )

    def finish_ai_message(self) -> str:
        full = self._ai_buffer

        # 转义所有 < 和 >，防止 RichMarkdown 把它们当 HTML 标签隐藏。
        # Rust 泛型 <T>、C++ 模板 <int>、XML <tag> 等都会被破坏。
        # 代价：autolink <https://...> 失效（AI 输出中极少出现）。
        if "<" in full or ">" in full:
            full = full.replace("<", "&lt;").replace(">", "&gt;")
            self._ai_buffer = full

        if self._ai_stream is not None and full:
            try:
                md = RichMarkdown(full, code_theme=self._code_theme)
                # 替换流式 Static 为可双击复制的 MessageWidget
                self._ai_stream.remove()
                ai_msg = MessageWidget(
                    full,
                    renderable=Panel(md, border_style="#555555"),
                )
                ai_msg.add_class("ai-message")
                self.mount(ai_msg)
            except Exception:
                pass

        self._ai_stream = None
        self._ai_buffer = ""
        return full

    # ── 错误 ────────────────────────────────────────────────────────

    def add_error(self, text: str) -> None:
        content = Text.from_markup(escape(text))
        msg = MessageWidget(
            text,
            renderable=Panel(content, border_style="#cc3333", title="Error"),
        )
        msg.add_class("error-message")
        self.mount(msg)
        self._scroll_end()

    # ── 系统通知 ────────────────────────────────────────────────────

    def add_system_notice(self, text: str) -> None:
        content = Text.from_markup(escape(text))
        msg = MessageWidget(
            text,
            renderable=Panel(content, border_style="#e09030", title="System"),
        )
        msg.add_class("system-message")
        self.mount(msg)
        self._scroll_end()

    # ── 命令结果 ────────────────────────────────────────────────────

    def add_command_result(self, text: str, title: str = "Command") -> None:
        # 转义 HTML 标签，防止 RichMarkdown 吞掉 <T> 等代码内容
        safe = text.replace("<", "&lt;").replace(">", "&gt;")
        try:
            content = RichMarkdown(safe, code_theme=self._code_theme)
        except Exception:
            content = Text.from_markup(escape(text))
        msg = MessageWidget(
            text,
            renderable=Panel(content, border_style="#e09030", title=title),
        )
        msg.add_class("cmd-message")
        self.mount(msg)
        self._scroll_end()

    # ── 状态 ────────────────────────────────────────────────────────

    def has_pending(self) -> bool:
        return self._ai_stream is not None and bool(self._ai_buffer)

    def clear(self) -> None:
        """清空所有消息。"""
        self._ai_stream = None
        self._ai_buffer = ""
        for child in list(self.children):
            child.remove()

    def restore_conversation(self, messages: list[dict]) -> None:
        """从 conversation 列表恢复显示所有消息（含多模态 content）。"""
        for msg in messages:
            role = msg.get("role", "")
            raw_content = msg.get("content", "")
            text, images = _parse_multimodal_content(raw_content)

            if role == "user" and (text or images):
                # 优先使用 _image_paths（文件路径），回退到 content 中的 data URL
                file_paths = msg.get("_image_paths", []) or images
                self.add_user_message(text or "", file_paths=file_paths)
            elif role == "assistant" and text:
                # 转义 HTML 标签，防止 <T> 等代码内容被 RichMarkdown 吞掉
                safe = text.replace("<", "&lt;").replace(">", "&gt;")
                try:
                    md = RichMarkdown(safe, code_theme=self._code_theme)
                except Exception:
                    md = Text.from_markup(escape(text))
                widget = MessageWidget(
                    text,
                    renderable=Panel(md, border_style="#555555"),
                )
                widget.add_class("ai-message")
                self.mount(widget)

    def _scroll_end(self) -> None:
        self.scroll_end(animate=False)


# ── 多模态 content 解析 ─────────────────────────────────────────────────

def _format_image_placeholder(data_url: str) -> str:
    """从 data URL 提取图片信息，生成可读占位符。"""
    try:
        from core.llm_gateway.image_utils import data_url_to_image, get_image_size_kb
        kb = get_image_size_kb(data_url)
        img = data_url_to_image(data_url)
        if img is not None:
            w, h = img.size
            return f"[🖼 Image: {w}×{h}, {kb:.0f}KB]"
    except Exception:
        pass
    return "[🖼 Image attached]"


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
