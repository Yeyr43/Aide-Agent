"""树组件层 — 回合树的节点与容器。

每个 assistant 回合 = 一棵 TurnTree（Vertical 容器），节点用 ● 标记。
只有 ● 的颜色随节点类型变化，节点文本一律正常色。

节点交互：左键双击折叠/展开（可折叠节点），右键点击复制内容。
"""

from __future__ import annotations

import json
import logging
import time

from rich.console import Group
from rich.markdown import Markdown as RichMarkdown
from rich.markup import escape
from rich.padding import Padding
from rich.text import Text
from textual.containers import Vertical
from textual.events import Click
from textual.widgets import Static

logger = logging.getLogger(__name__)

DOUBLE_CLICK_MS = 400


def should_separate(prev_kind: str | None, kind: str) -> bool:
    """前后节点类型不同时，插入一行 │ 引导线（首节点不插）。"""
    return prev_kind is not None and prev_kind != kind


def _format_args(arguments: dict) -> str:
    """紧凑渲染工具调用参数。

    规则：按 [path, file_path, query, command, url, text] 顺序取第一个字符串字段；
    query 带引号，其余裸显；超 60 字符截断；无关键字段则 JSON 截断。
    """
    if not arguments:
        return ""
    for key in ("path", "file_path", "query", "command", "url", "text"):
        val = arguments.get(key)
        if isinstance(val, str) and val:
            if len(val) > 60:
                val = val[:57] + "..."
            return f'"{val}"' if key == "query" else val
    s = json.dumps(arguments, ensure_ascii=False)
    return s[:60] + ("..." if len(s) > 60 else "")


def _guide_indented(text: str, indent: str = "   ", style: str = "") -> Text:
    """多行内容渲染：每行加 │ 引导前缀，缩进到文本列。"""
    t = Text()
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if i:
            t.append("\n")
        t.append("│ ", style="#555555")
        t.append(indent)
        t.append(line, style=style)
    return t


class TreeNode(Static):
    """树节点基类：● + 内容。

    左键双击（可折叠节点）折叠/展开；右键点击复制内容。
    子类实现 _build_renderable() 返回渲染对象；_plain 为复制用原文。
    """

    _collapsible = False
    _bullet_style = ""
    _kind = "node"

    def __init__(self, plain_text: str = "", **kwargs) -> None:
        super().__init__(content="", **kwargs)
        self._plain = plain_text
        self._last_click = 0.0

    def _label_line(self, label: str, bullet_style: str | None = None) -> Text:
        t = Text()
        t.append("│ ", style="#555555")   # 引导列：贯穿整个消息树
        t.append("● ", style=bullet_style or self._bullet_style)  # 子弹列
        t.append(label, style="")  # 文本列，一律正常色
        return t

    def _build_renderable(self):
        raise NotImplementedError

    def _refresh(self) -> None:
        self.update(self._build_renderable())

    def _toggle(self) -> None:
        """子类覆盖：切换折叠状态。"""

    # ── 交互 ──

    def on_click(self, event: Click) -> None:
        if event.button == 3:  # 右键 → 复制
            self._copy_to_clipboard()
            return
        if event.button != 1:  # 仅左键检测双击
            return
        if not self._collapsible:
            return
        now = time.monotonic()
        if 0 < (now - self._last_click) * 1000 < DOUBLE_CLICK_MS:
            self._toggle()
        self._last_click = now

    def _copy_to_clipboard(self) -> None:
        text = (self._plain or "").strip()
        if not text:
            return
        try:
            import pyperclip
            pyperclip.copy(text)
        except Exception:
            logger.debug("clipboard copy failed", exc_info=True)


class ThinkNode(TreeNode):
    """思考节点：● think。流式期间展开，结束自动折叠。双击展开全文。"""

    _collapsible = True
    _bullet_style = "#888888"
    _kind = "think"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._thinking = ""
        self._expanded = True

    def append_chunk(self, chunk: str) -> None:
        self._thinking += chunk
        if self._expanded:
            self._refresh()

    def finish(self) -> None:
        """思考结束 → 折叠为 ● think。"""
        self._expanded = False
        self._plain = self._thinking
        self._refresh()

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._refresh()

    def _build_renderable(self):
        if self._expanded:
            t = Text()
            t.append_text(self._label_line("think", self._bullet_style))
            if self._thinking:
                t.append("\n")
                t.append_text(_guide_indented(self._thinking, style="italic #888888"))
            return t
        return self._label_line("think", self._bullet_style)


class ToolNode(TreeNode):
    """工具调用节点：● 工具名 参数  耗时。展开显示结果。"""

    _collapsible = True
    _bullet_style = "#555555"
    _kind = "tool"

    def __init__(self, tool_name: str, arguments: dict,
                 code_theme: str = "monokai", **kwargs) -> None:
        super().__init__(**kwargs)
        self._tool_name = tool_name
        self._arguments = arguments
        self._result = ""
        self._error = ""
        self._duration: float | None = None
        self._is_error = False
        self._expanded = False
        self._refresh()

    def set_result(self, result: str) -> None:
        self._result = result
        self._plain = result
        self._refresh()

    def set_error(self, error: str) -> None:
        self._is_error = True
        self._error = error
        self._plain = error
        self._refresh()

    def set_duration(self, seconds: float | None) -> None:
        self._duration = seconds
        self._refresh()

    def _label(self) -> str:
        label = self._tool_name
        args_repr = _format_args(self._arguments)
        if args_repr:
            label += " " + args_repr
        if self._duration is not None:
            label += f"  {self._duration:.1f}s"
        return label

    def _bullet_color(self) -> str:
        return "#cc3333" if self._is_error else self._bullet_style

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._refresh()

    def _build_renderable(self):
        line = self._label_line(self._label(), self._bullet_color())
        if not self._expanded:
            return line
        body = self._result or self._error or ""
        if not body:
            return line
        t = Text()
        t.append_text(line)
        t.append("\n")
        t.append_text(_guide_indented(body, style="dim"))
        return t


class BodyNode(TreeNode):
    """正文节点：● 正文（Markdown）。流式阶段 Text 快速渲染，完成阶段 Markdown。"""

    _collapsible = False
    _bullet_style = ""
    _kind = "body"

    def __init__(self, code_theme: str = "monokai", **kwargs) -> None:
        super().__init__(**kwargs)
        self._code_theme = code_theme
        self._buffer = ""
        self._finished = False

    def append_chunk(self, chunk: str) -> None:
        self._buffer += chunk
        if not self._finished:
            self._refresh()

    def finish(self) -> None:
        self._finished = True
        self._plain = self._buffer
        self._refresh()

    def replace_content(self, text: str) -> None:
        """XML fallback：替换当前流式正文。"""
        self._buffer = text
        self._refresh()

    def set_finished_text(self, text: str) -> None:
        """恢复会话：直接设置已完成正文（跳过流式）。"""
        self._buffer = text
        self._finished = True
        self._plain = text
        self._refresh()

    def _build_renderable(self):
        if not self._buffer:
            return self._label_line("")
        if not self._finished:
            return Text.from_markup(escape("│ ● " + self._buffer))
        safe_body = self._buffer.replace("<", "&lt;").replace(">", "&gt;")
        try:
            md = RichMarkdown(safe_body, code_theme=self._code_theme)
        except Exception:
            md = Text(self._buffer)
        # 正文：│ ● 首行 + 正文缩进到文本列（RichMarkdown 无法逐行加引导前缀）
        return Group(Text("│ ● "), Padding(md, (0, 0, 0, 4)))


class ErrorNode(TreeNode):
    """错误节点：● 红。折叠显示首行，展开显示全文。"""

    _bullet_style = "#cc3333"
    _kind = "error"

    def __init__(self, text: str, **kwargs) -> None:
        super().__init__(plain_text=text, **kwargs)
        self._text = text
        self._collapsible = len(text) > 80
        self._expanded = False
        self._refresh()

    def _summary(self) -> str:
        first = (self._text or "").splitlines()[0] if self._text else ""
        return (first[:80] + "…") if len(first) > 80 else (first or "error")

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._refresh()

    def _build_renderable(self):
        if self._expanded:
            t = Text()
            t.append_text(self._label_line("error", self._bullet_style))
            t.append("\n")
            t.append_text(_guide_indented(self._text))
            return t
        return self._label_line("error " + self._summary(), self._bullet_style)


class SystemNode(TreeNode):
    """系统/命令节点：● 琥珀。折叠显示首行，过长可展开。"""

    _bullet_style = "#e09030"
    _kind = "system"

    def __init__(self, text: str, **kwargs) -> None:
        super().__init__(plain_text=text, **kwargs)
        self._text = text
        self._collapsible = len(text) > 80
        self._expanded = False
        self._refresh()

    def _summary(self) -> str:
        first = (self._text or "").splitlines()[0] if self._text else ""
        return (first[:80] + "…") if len(first) > 80 else (first or "system")

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._refresh()

    def _build_renderable(self):
        if self._expanded:
            t = Text()
            t.append_text(self._label_line(self._summary() + "（展开）", self._bullet_style))
            t.append("\n")
            t.append_text(_guide_indented(self._text))
            return t
        return self._label_line(self._summary(), self._bullet_style)


class TurnTree(Vertical):
    """一个 assistant 回合的树形容器。

    add_node 时，若新节点类型与上一个不同，先插入一行 │ 引导线。
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._last_kind: str | None = None

    def add_node(self, node: TreeNode, kind: str) -> None:
        if should_separate(self._last_kind, kind):
            guide = Static("│")
            guide.add_class("tree-guide")
            self.mount(guide)
        node.add_class("tree-node")
        self.mount(node)
        self._last_kind = kind
