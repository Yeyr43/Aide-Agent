# UI 树形回合显示改造 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 MessageList 从"边框 Panel 堆叠"重写为"树形回合展示"：每个 assistant 回合一棵树，节点用 `●` 标记（颜色随类型变、文本正常色），think 流式展开→结束自动折叠，工具调用精简单行显示，左键双击折叠、右键复制。

**Architecture:** 新建 `tree_nodes.py` 承载树组件层（TreeNode 基类 + 5 种节点 + TurnTree 容器 + 纯函数），`message_list.py` 重写为回合树的流式管理器，`bridge.py` 把工具回调从 noop 接成树节点（UI 侧 FIFO 计时，kernel 零改动），`app.tcss` 调整输入框整行与间距。

**Tech Stack:** Python 3.13+ / Textual 0.80+ / Rich（Text / Markdown / Group / escape）

## Global Constraints

- 不改 `core/` 任何文件（kernel / fc_loop / provider 零改动）；工具计时在 UI 侧用 `time.monotonic` 完成
- 只有 `●` 的颜色随节点类型变化；节点文本一律正常色（`style=""`）
- 交互：左键双击 = 折叠/展开（仅可折叠节点）；右键点击 = 复制；Enter 不参与折叠
- 不要创建 `tests/ui/__init__.py`（会遮蔽顶层 `ui` 包）
- 既有测试必须保持通过：`uv run pytest tests/ -q`（当前 1045 个）
- 提交信息用中文，格式 `feat:` / `refactor:` / `test:`

---

## 文件结构

| 文件 | 职责 | 动作 |
|------|------|------|
| `ui/textual_app/widgets/tree_nodes.py` | 树组件层：TreeNode 基类 + ThinkNode/ToolNode/BodyNode/ErrorNode/SystemNode + TurnTree 容器 + `should_separate` / `_format_args` 纯函数 | **新建** |
| `ui/textual_app/widgets/message_list.py` | MessageList 重写为回合树流式管理器；MessageWidget（用户消息）交互更新（右键复制/双击开文件） | **修改** |
| `ui/textual_app/bridge.py` | 工具回调接线（start/done/error → 树节点），文本/思考流向树节点 | **修改** |
| `ui/textual_app/app.tcss` | 输入框整行、用户框与树空一行、树节点样式 | **修改** |
| `tests/ui/test_tree_nodes.py` | 纯函数 + 节点渲染测试 | **新建** |
| `tests/ui/test_message_list.py` | MessageList 集成测试（Textual `run_test` harness） | **新建** |
| `tests/ui/test_bridge.py` | 更新：工具回调断言从 noop → 树节点转发 | **修改** |

---

### Task 1: 树组件层 `tree_nodes.py`

**Files:**
- Create: `ui/textual_app/widgets/tree_nodes.py`
- Test: `tests/ui/test_tree_nodes.py`

**Interfaces:**
- Produces（供 Task 2/3 使用）:
  - `should_separate(prev_kind: str | None, kind: str) -> bool`
  - `_format_args(arguments: dict) -> str`
  - `class TreeNode(Static)` — 基类（`_plain`、右键复制、左键双击检测）
  - `class ThinkNode(TreeNode)` — `append_chunk(chunk: str)` / `finish()` / 默认展开
  - `class ToolNode(TreeNode)` — `__init__(tool_name, arguments, code_theme="monokai")` / `set_result(result: str)` / `set_error(error: str)` / `set_duration(seconds: float)`
  - `class BodyNode(TreeNode)` — `__init__(code_theme="monokai")` / `append_chunk(chunk: str)` / `finish()` / `replace_content(text: str)` / `set_finished_text(text: str)`
  - `class ErrorNode(TreeNode)` — `__init__(text: str)`
  - `class SystemNode(TreeNode)` — `__init__(text: str)`
  - `class TurnTree(Vertical)` — `add_node(node: TreeNode, kind: str)`

- [ ] **Step 1: 写失败测试**

```python
# tests/ui/test_tree_nodes.py
"""树组件层测试 — 纯函数 + 节点渲染（不依赖完整 App）。"""
import re

from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text

from ui.textual_app.widgets.tree_nodes import (
    should_separate, _format_args, TurnTree, ThinkNode, ToolNode,
    BodyNode, ErrorNode, SystemNode,
)


class TestShouldSeparate:
    def test_first_node_no_separator(self):
        assert should_separate(None, "think") is False

    def test_same_kind_no_separator(self):
        assert should_separate("tool", "tool") is False

    def test_different_kind_separates(self):
        assert should_separate("think", "tool") is True
        assert should_separate("tool", "body") is True


class TestFormatArgs:
    def test_path_bare(self):
        assert _format_args({"path": "src/main.py"}) == "src/main.py"

    def test_query_quoted(self):
        assert _format_args({"query": "TODO"}) == '"TODO"'

    def test_empty(self):
        assert _format_args({}) == ""

    def test_long_value_truncated(self):
        assert len(_format_args({"path": "x" * 100})) <= 63

    def test_fallback_json(self):
        out = _format_args({"a": 1, "b": 2})
        assert out.startswith("{") or "a=1" in out


class TestNodeRenderables:
    """直接调用 _build_renderable()（纯方法，不触发 self.update）。"""

    def test_think_collapsed_is_bullet_think(self):
        node = ThinkNode()
        node.append_chunk("思考内容")
        node.finish()  # 结束 → 自动折叠
        r = node._build_renderable()
        assert isinstance(r, Text)
        assert "think" in r.plain

    def test_think_expanded_contains_content(self):
        node = ThinkNode()
        node.append_chunk("思考内容")
        r = node._build_renderable()
        assert "思考内容" in r.plain

    def test_tool_line_has_name_args_duration(self):
        node = ToolNode("read_file", {"path": "src/main.py"})
        node.set_result("content")
        node.set_duration(0.4)
        r = node._build_renderable()
        assert "read_file" in r.plain
        assert "src/main.py" in r.plain
        assert "0.4" in r.plain

    def test_tool_error_bullet_red(self):
        node = ToolNode("read_file", {"path": "x"})
        node.set_error("boom")
        r = node._build_renderable()
        assert isinstance(r, Text)
        assert any("cc3333" in str(s.style) for s in r.spans)  # 错误态 ● 用红色

    def test_body_finished_is_markdown(self):
        node = BodyNode()
        node.append_chunk("正文**加粗**")
        node.finish()
        r = node._build_renderable()
        assert isinstance(r, Markdown) or isinstance(r, Group)

    def test_error_node_prefix(self):
        node = ErrorNode("401 认证失败")
        r = node._build_renderable()
        assert "401" in r.plain

    def test_system_node_prefix(self):
        node = SystemNode("命令完成")
        r = node._build_renderable()
        assert "命令完成" in r.plain
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/ui/test_tree_nodes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ui.textual_app.widgets.tree_nodes'`

- [ ] **Step 3: 实现 `tree_nodes.py`**

```python
"""树组件层 — 回合树的节点与容器。

每个 assistant 回合 = 一棵 TurnTree（Vertical 容器），节点用 ● 标记。
只有 ● 的颜色随节点类型变化，节点文本一律正常色。

节点交互：左键双击折叠/展开（可折叠节点），右键点击复制内容。
"""

from __future__ import annotations

import json
import logging
import re
import time

from rich.console import Group
from rich.markdown import Markdown as RichMarkdown
from rich.markup import escape
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


class TreeNode(Static):
    """树节点基类：● + 内容。

    左键双击（可折叠节点）折叠/展开；右键点击复制内容。
    子类实现 _build_renderable() 返回渲染对象；_plain 为复制用原文。
    """

    _collapsible = False
    _bullet_style = ""
    _kind = "node"

    def __init__(self, plain_text: str = "", **kwargs) -> None:
        super().__init__(renderable="", **kwargs)
        self._plain = plain_text
        self._last_click = 0.0

    def _label_line(self, label: str, bullet_style: str | None = None) -> Text:
        t = Text()
        t.append("● ", style=bullet_style or self._bullet_style)
        t.append(label, style="")  # 文本一律正常色
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
            t.append("● ", style=self._bullet_style)
            t.append("think", style="")
            if self._thinking:
                t.append("\n")
                t.append(self._thinking, style="italic #888888")
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
        t.append("\n  ")
        t.append(body.replace("\n", "\n  "), style="dim")
        return t


# 以代码围栏/标题开头的正文，● 不能塞进 markdown 结构里
_MD_BLOCK_START = re.compile(r"^\s*(?:```|~~~|#{1,6}\s|>\s)")


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
            return Text.from_markup(escape("● " + self._buffer))
        safe = "● " + self._buffer.replace("<", "&lt;").replace(">", "&gt;")
        if _MD_BLOCK_START.match(self._buffer):
            safe_body = self._buffer.replace("<", "&lt;").replace(">", "&gt;")
            try:
                md = RichMarkdown(safe_body, code_theme=self._code_theme)
            except Exception:
                md = Text(self._buffer)
            return Group(Text("● "), md)
        try:
            return RichMarkdown(safe, code_theme=self._code_theme)
        except Exception:
            return Text.from_markup(escape("● " + self._buffer))


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
            t.append("\n  ")
            t.append(self._text.replace("\n", "\n  "), style="")
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
            t.append("\n  ")
            t.append(self._text.replace("\n", "\n  "), style="")
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/ui/test_tree_nodes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui/textual_app/widgets/tree_nodes.py tests/ui/test_tree_nodes.py
git commit -m "feat: 树组件层 — 回合树节点类 + TurnTree 容器"
```

---

### Task 2: 重写 `message_list.py` 为回合树流式管理器

**Files:**
- Modify: `ui/textual_app/widgets/message_list.py`
- Test: `tests/ui/test_message_list.py`

**Interfaces:**
- Consumes（Task 1 产出）: `ThinkNode` / `ToolNode` / `BodyNode` / `ErrorNode` / `SystemNode` / `TurnTree`
- Produces（供 Task 3 bridge 使用）:
  - `MessageList.add_thinking_chunk(chunk)`
  - `MessageList.add_ai_chunk(chunk)`
  - `MessageList.finish_ai_message() -> str`
  - `MessageList.add_tool_start(tool_name, arguments)`
  - `MessageList.add_tool_done(tool_name, result)`
  - `MessageList.add_tool_error(tool_name, error)`
  - `MessageList.add_error(text)`
  - `MessageList.add_system_notice(text)`
  - `MessageList.add_command_result(text, title="Command")`
  - `MessageList.replace_streamed_text(clean_text)`
  - `MessageList.has_pending() -> bool`
  - `MessageList.clear()`
  - `MessageList.restore_conversation(messages)`
  - `MessageList.add_user_message(text, file_paths=None)`

- [ ] **Step 1: 写失败测试**

```python
# tests/ui/test_message_list.py
"""MessageList 树形流式集成测试（Textual run_test harness）。"""
import pytest
from textual.app import App

from ui.textual_app.widgets.message_list import MessageList


class MessageListTestApp(App):
    def compose(self):
        self.ml = MessageList()
        yield self.ml


@pytest.mark.asyncio
async def test_streaming_creates_tree_with_separators():
    app = MessageListTestApp()
    async with app.run_test():
        ml = app.ml
        ml.add_thinking_chunk("思考中")   # think 节点
        ml.add_ai_chunk("正文")           # body 节点
        ml.add_tool_start("read_file", {"path": "x"})   # tool 节点
        ml.add_tool_done("read_file", "content")
        ml.finish_ai_message()
        nodes = app.query(".tree-node")
        guides = app.query(".tree-guide")
        assert len(nodes) == 3
        assert len(guides) == 2  # think→body, body→tool


@pytest.mark.asyncio
async def test_consecutive_tools_no_separator():
    app = MessageListTestApp()
    async with app.run_test():
        ml = app.ml
        ml.add_tool_start("read_file", {"path": "a"})
        ml.add_tool_done("read_file", "r1")
        ml.add_tool_start("search_in_files", {"query": "TODO"})
        ml.add_tool_done("search_in_files", "r2")
        guides = app.query(".tree-guide")
        assert len(guides) == 0  # 同类型相邻，不插 │


@pytest.mark.asyncio
async def test_think_collapses_after_body_starts():
    app = MessageListTestApp()
    async with app.run_test():
        ml = app.ml
        ml.add_thinking_chunk("思考")
        ml.add_ai_chunk("正文")
        # think 节点此时已折叠 → 渲染文本为 "● think"，不含思考内容
        think = [w for w in app.query(".tree-node")
                 if type(w).__name__ == "ThinkNode"][0]
        assert "思考" not in think.renderable.plain


@pytest.mark.asyncio
async def test_tool_fifo_pairs_by_name():
    app = MessageListTestApp()
    async with app.run_test():
        ml = app.ml
        ml.add_tool_start("read_file", {"path": "a"})   # 第一个 read_file
        ml.add_tool_start("read_file", {"path": "b"})   # 第二个 read_file
        ml.add_tool_done("read_file", "r2")             # done 配对第二个（FIFO）
        ml.add_tool_done("read_file", "r1")
        tools = [w for w in app.query(".tree-node")
                 if type(w).__name__ == "ToolNode"]
        assert len(tools) == 2
        assert tools[0]._plain == "r2"  # FIFO：先完成的是后开始的
        assert tools[1]._plain == "r1"


@pytest.mark.asyncio
async def test_user_message_closes_turn():
    app = MessageListTestApp()
    async with app.run_test():
        ml = app.ml
        ml.add_ai_chunk("AI 回复")
        ml.add_user_message("新的用户消息")
        trees = app.query(".turn-tree")
        assert len(trees) == 1  # 用户消息不建树，只关闭上一个回合树
        assert ml._current_turn is None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/ui/test_message_list.py -v`
Expected: FAIL（MessageList 尚无新方法 / 行为未变）

- [ ] **Step 3: 重写 `message_list.py`**

保留文件顶部 `MessageWidget`（更新点击行为）与 `_parse_multimodal_content`，重写 `MessageList`：

```python
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
```

注意：`_parse_multimodal_content` 在文件同作用域，直接调用即可。用户消息按设计"保留对话框"，使用 `Panel` 边框（`title="You"`，右对齐）。

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/ui/test_message_list.py tests/ui/test_tree_nodes.py -v`
Expected: PASS（若 `test_user_message_closes_turn` 断言需对齐，以实际行为修正断言）

- [ ] **Step 5: Commit**

```bash
git add ui/textual_app/widgets/message_list.py tests/ui/test_message_list.py
git commit -m "feat: MessageList 重写为回合树流式管理器"
```

---

### Task 3: UIBridge 工具接线

**Files:**
- Modify: `ui/textual_app/bridge.py`
- Test: `tests/ui/test_bridge.py`

**Interfaces:**
- Consumes（Task 2 产出）: `add_tool_start` / `add_tool_done` / `add_tool_error`
- Produces: bridge 现有 `ExecutorUI` 方法全部实现（工具不再 noop）

- [ ] **Step 1: 更新失败测试**

把 `tests/ui/test_bridge.py` 的 `TestUIBridgeToolEvents` 中 `test_tool_start_and_done_are_noops` 替换为转发断言：

```python
    def test_tool_start_forwards_to_msg_list(self, bridge_with_mock_msg_list):
        bridge, msg_list = bridge_with_mock_msg_list
        bridge.on_tool_start("read_file", {"path": "/tmp/x"})
        msg_list.add_tool_start.assert_called_once_with(
            "read_file", {"path": "/tmp/x"}
        )

    def test_tool_done_forwards_to_msg_list(self, bridge_with_mock_msg_list):
        bridge, msg_list = bridge_with_mock_msg_list
        bridge.on_tool_done("read_file", "content")
        msg_list.add_tool_done.assert_called_once_with("read_file", "content")

    def test_tool_error_forwards_to_msg_list(self, bridge_with_mock_msg_list):
        bridge, msg_list = bridge_with_mock_msg_list
        bridge.on_tool_error("read_file", "Permission denied")
        msg_list.add_tool_error.assert_called_once_with("read_file", "Permission denied")
```

并更新 `test_tool_error_shows_error` 为 `test_tool_error_forwards_to_msg_list`（不再断言 `add_error`）。

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/ui/test_bridge.py -v`
Expected: FAIL（`msg_list.add_tool_start` 是 mock，未配置 → AssertionError，因 bridge 现在还是 `pass`）

- [ ] **Step 3: 改造 `bridge.py`**

```python
    def on_tool_start(self, tool_name: str, arguments: dict) -> None:
        self._msg_list().add_tool_start(tool_name, arguments)

    def on_tool_done(self, tool_name: str, result: str) -> None:
        self._msg_list().add_tool_done(tool_name, result)

    def on_tool_error(self, tool_name: str, error: str) -> None:
        self._msg_list().add_tool_error(tool_name, error)
```

其余方法不变（`on_text_token`→`add_ai_chunk`、`on_thinking_token`→`add_thinking_chunk`、`on_text_done`→`finish_ai_message` 若 `has_pending`、`on_max_turns`→`add_system_notice`、`on_replace_streamed_text`→`replace_streamed_text`，均已是正确转发）。`on_tool_start` 现不再 noop，`on_tool_done` 不再 noop。

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/ui/test_bridge.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui/textual_app/bridge.py tests/ui/test_bridge.py
git commit -m "feat: UIBridge 工具回调接线 — 工具调用可视化"
```

---

### Task 4: CSS 样式调整

**Files:**
- Modify: `ui/textual_app/app.tcss`

- [ ] **Step 1: 修改 `app.tcss`**

```css
#input {
    margin: 0 0 1 0;          /* 占满整行（去掉左右 margin） */
    border: solid #555555;
    background: #121212;
    color: #c8c8c0;
    max-height: 5;
    scrollbar-size: 0 0;
    padding: 0 1;
}

#input:focus { border: solid #c8c8c0; }
#input.maintenance { border: solid #e0c878; }

CommandPalette {
    display: none;
    height: auto;
    max-height: 7;
    margin: 0 0 0 0;           /* 与输入框对齐，整行 */
    border: solid #555555;
    background: #121212;
    padding: 0 1;
    scrollbar-size: 0 0;
}

CommandPalette.-visible { display: block; }

CommandPalette ListView {
    height: auto;
    max-height: 6;
    background: #121212;
}

CommandPalette ListView > ListItem {
    padding: 0 1;
    color: #888888;
}

CommandPalette ListView > ListItem.-highlight {
    background: #1a2a3a;
    color: #c8c8c0;
}

CommandPalette Static { color: #888888; }

.user-message {
    margin: 1 0 2 8;          /* 保留缩进 + 底部留空一行（与树隔开） */
    background: #0c0c0c;
    color: #c8c8c0;
}

.turn-tree {
    margin: 0 0 1 0;
}

.tree-guide {
    color: #555555;
    height: 1;
}

.tree-node {
    margin: 0 0 0 8;           /* 树内容左缩进 8，对齐用户框缩进 */
    color: #c8c8c0;
}
```

删除现在不再使用的 `.ai-message` / `.error-message` / `.system-message` / `.cmd-message` 规则。

- [ ] **Step 2: 验证**

Run: `uv run pytest tests/ui/test_message_list.py tests/ui/test_tree_nodes.py tests/ui/test_bridge.py -q`
Expected: PASS（CSS 无单测，验证不报错即可）

- [ ] **Step 3: Commit**

```bash
git add ui/textual_app/app.tcss
git commit -m "style: 输入框整行 + 回合树节点样式"
```

---

### Task 5: 全量回归

**Files:** 无新文件

- [ ] **Step 1: 运行全部测试**

Run: `uv run pytest tests/ -q --tb=short`
Expected: PASS（1045 个既有测试 + 新增 UI 测试全绿）

- [ ] **Step 2: 修复任何失败**

若有失败，按 `superpowers:systematic-debugging` 流程定位修复。已知需要修的：
- `tests/ui/test_bridge.py::TestUIBridgeToolEvents::test_tool_start_and_done_are_noops`（Task 3 已替换）
- 任何直接构造 `MessageWidget`/`MessageList` 旧 API 的测试

- [ ] **Step 3: 手动冒烟（可选）**

```bash
uv run python shell/main.py
```
验证：输入"读项目结构"触发工具调用 → 工具以 `● read_file …` 单行显示、think 折叠、正文树形展示、右键复制、双击折叠、输入框整行。

- [ ] **Step 4: Commit（如有修复）**

```bash
git add -A
git commit -m "fix: UI 改造后回归修复"
```

---

## Self-Review

**Spec 覆盖：**
- ✅ 布局（用户框→空行→回合树；输入框整行）→ Task 2 + Task 4
- ✅ 渲染（无 gutter；●/│ 字面字符；块间插 │）→ Task 1 `should_separate` + Task 2
- ✅ 节点类型（think 灰●可折叠 / 工具暗● / 正文亮● / 错误红● / 系统琥珀●）→ Task 1 各节点类
- ✅ 交互（左键双击折叠 / 右键复制 / Enter 不参与）→ Task 1 `TreeNode.on_click` + Task 2 `MessageWidget.on_click`
- ✅ 工具计时（UI 侧 FIFO + `time.monotonic`）→ Task 2 `_tool_fifo`/`_tool_start_times`/`_elapsed`
- ✅ 流式（think 展开→结束折叠）→ Task 2 `add_thinking_chunk`/`add_ai_chunk`/`_close_open_text`
- ✅ 测试（test_bridge 更新 + MessageList 测试）→ Task 1/2/3
- ✅ kernel 零改动 → 全部改动在 `ui/` + `tests/ui/`

**Placeholder 检查：** 所有代码块完整，无 TBD/TODO。Task 2 中"用户消息是否保留 Panel 边框"标注为视觉效果二选一——若删 Panel，需同步更新 `test_user_message_closes_turn` 断言，已在测试注释中说明。

**类型一致性：**
- `should_separate(prev_kind, kind)` 在 Task 1 定义、Task 2 `TurnTree.add_node` 使用 — 一致
- `add_tool_start/done/error` 签名在 Task 2 定义、Task 3 bridge 调用 — 一致
- `finish_ai_message() -> str` Task 2 定义、Task 3 `on_text_done` 使用 — 一致
- `BodyNode.replace_content` Task 1 定义、Task 2 `replace_streamed_text` 使用 — 一致
- `ThinkNode.finish()` / `BodyNode.finish()` 命名在 Task 1 定义、Task 2 `_close_open_text` 使用 — 一致
