"""树组件层 — 回合树的节点与容器。

每个 assistant 回合 = 一棵 TurnTree（Vertical 容器），节点用 ● 标记。
只有 ● 的颜色随节点类型变化，节点文本一律正常色。

节点交互：左键双击折叠/展开（可折叠节点），右键点击复制内容。
"""

from __future__ import annotations

import json
import logging
import math
import re
import time

from rich.console import Group
from rich.markdown import Markdown as RichMarkdown
from rich.measure import Measurement
from rich.padding import Padding
from rich.segment import Segment
from rich.text import Text
from textual.containers import Vertical
from textual.events import Click
from textual.widgets import Static

logger = logging.getLogger(__name__)

DOUBLE_CLICK_MS = 400

# ── 树节点色板（● 随类型着色，连接符统一）────────────────────────────
CONNECTOR_STYLE = "#555555"   # 树连接符（├ / └ / │）统一灰色
BULLET_THINK = "#888888"      # 思考 · 灰
BULLET_TOOL = "#5cb85c"       # 工具 · 绿
BULLET_ERROR = "#cc3333"      # 警告/报错 · 红
BULLET_SYSTEM = "#d0b000"     # 系统信息 · 黄
BULLET_BODY = "#ffffff"       # 正文 · 白

# 呼吸效果：进行中节点的 ● 在原色 ↔ 压暗色之间**渐变**（正弦插值，平滑呼吸）
BREATH_TICK = 0.08             # 渐变更新间隔（秒）— 越小越平滑
BREATH_CYCLE = 2.0             # 完整呼吸周期（秒）
BREATH_DIM_MIN = 0.40          # 最暗亮度因子（与原色相乘，对比更明显）
BREATH_START_PHASE = 1.5707963267948966  # π/2：从最亮相位启动，短暂节点也能看到先亮后暗


def _scale_color(hex_color: str, factor: float) -> str:
    """把 #RRGGBB 的 RGB 各通道乘 factor（0..1）得到新色。解析失败原样返回。"""
    try:
        h = hex_color.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        f = max(0.0, min(1.0, factor))
        return f"#{int(r * f):02x}{int(g * f):02x}{int(b * f):02x}"
    except (ValueError, AttributeError):
        return hex_color


def _dim_color(hex_color: str) -> str:
    """把 #RRGGBB 压暗到最暗呼吸相位（兼容旧测试）。"""
    return _scale_color(hex_color, BREATH_DIM_MIN)


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


def _guide_indented(text: str, indent: str = "  ", style: str = "", guide: bool = True) -> Text:
    """多行内容渲染：每行缩进到文本列（标签首列 +4）。

    guide=True 时每行加 │ 引导前缀（树形续行）；guide=False 时仅缩进
    （与正文续行一致的纯缩进，避免运行中节点下方不断"长出"连接符）。
    """
    t = Text()
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if i:
            t.append("\n")
        if guide:
            t.append("│ ", style=CONNECTOR_STYLE)
        t.append(indent)
        t.append(line, style=style)
    return t


class _PrefixedLines:
    """把任意 renderable 的每行前置 │ 引导线（树形续行保留竖线）。

    用于正文 RichMarkdown 尾部：非末节点时续行也要带 │，否则正文换行会
    打断树左边框（断链）。guide 由调用方按节点连接符决定——末节点（└）
    传纯缩进（Padding），非末节点（├）传本包装。
    """

    def __init__(self, renderable, prefix: str = "│   ", prefix_style: str = CONNECTOR_STYLE) -> None:
        self._inner = renderable
        self._prefix = prefix
        # Segment 不解析字符串样式（Text 会解析）；Textual 样式缓存合并时
        # 字符串会导致 AttributeError，这里预解析为 Style 对象
        from rich.style import Style
        self._prefix_style = Style.parse(prefix_style)

    def __rich_console__(self, console, options):
        inner_options = options.update_width(max(1, options.max_width - len(self._prefix)))
        inner_segments = console.render(self._inner, inner_options)
        first = True
        for line in Segment.split_lines(inner_segments):
            if not first:
                yield Segment("\n")
            first = False
            yield Segment(self._prefix, self._prefix_style)
            yield from line

    def __rich_measure__(self, console, options):
        inner_options = options.update_width(max(1, options.max_width - len(self._prefix)))
        m = Measurement.get(console, inner_options, self._inner)
        return Measurement(m.minimum + len(self._prefix), m.maximum + len(self._prefix))


# ── 行内 Markdown（正文首行专用）─────────────────────────────────────────

# 单趟 alternation：code 优先（`` 内不解析 * / [），加粗先于斜体（共用 * 定界）。
# 未闭合的定界保持字面量 —— 流式中语法还没写完就不渲染，写完立即生效。
_INLINE_MD_RE = re.compile(
    r"(`[^`\n]+`)"                            # 1 inline code
    r"|(\*\*[^*\n]+\*\*)"                     # 2 bold
    r"|(\*[^*\s][^*\n]*\*)"                   # 3 italic（* 两侧必须紧贴非空白，避免 2*3 误判）
    r"|(~~[^~\n]+~~)"                         # 4 strike
    r"|(\[[^\]\n]+\]\([^)\s\n]+\))"           # 5 link
)


def _inline_md_text(match: "re.Match") -> tuple[str, str]:
    """提取内联标记的展示文本与主题样式名（与 RichMarkdown 正文一致的 markdown.* 样式）。"""
    if match.group(1):
        return match.group(1)[1:-1], "markdown.code"
    if match.group(2):
        return match.group(2)[2:-2], "markdown.strong"
    if match.group(3):
        return match.group(3)[1:-1], "markdown.em"
    if match.group(4):
        return match.group(4)[2:-2], "markdown.s"
    inner = match.group(5)
    return inner[1:inner.index("]")], "markdown.link"


def _render_inline_markdown(text: str) -> Text:
    """把行内 Markdown 语法转成带样式的 Text（正文首行专用）。

    用与 RichMarkdown 正文一致的 markdown.* 主题样式（渲染期解析），
    保证首行加粗/代码/斜体与换行后的正文视觉一致。直接拼 Text span，
    不经 Text.from_markup，字面量 [x] 不会被误当 Rich markup。
    """
    t = Text()
    pos = 0
    for m in _INLINE_MD_RE.finditer(text):
        if m.start() > pos:
            t.append(text[pos:m.start()])
        shown, style = _inline_md_text(m)
        t.append(shown, style=style)
        pos = m.end()
    if pos < len(text):
        t.append(text[pos:])
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
        self._connector = "│"  # 树连接符，由 TurnTree 设为 ├ / └
        self._breath_phase = 0.0       # 呼吸正弦相位（0..2π，进行中才递增）
        self._breath_interval = None   # set_interval 定时器（进行中才存在）

    def set_connector(self, char: str) -> None:
        """设置树连接符（├ 中间 / └ 末节点），并重渲染。"""
        if self._connector != char:
            self._connector = char
            self._refresh()

    def _label_line(self, label: str, bullet_style: str | None = None) -> Text:
        t = Text()
        t.append(f"{self._connector} ", style=CONNECTOR_STYLE)   # 引导列：树连接符（统一）
        t.append("● ", style=self._active_bullet_color() if bullet_style is None
                 else bullet_style)  # 子弹列（未显式指定时随呼吸变化）
        t.append(label, style="")  # 文本列，一律正常色
        return t

    # ── 呼吸效果（进行中节点 ● 渐变呼吸）───────────────────────

    def _bullet_color(self) -> str:
        """有效子弹色（子类可覆盖，如 ToolNode 错误时返回红）。"""
        return self._bullet_style

    def _active_bullet_color(self) -> str:
        """渲染用子弹色：呼吸中按正弦相位在原色 ↔ 最暗色之间渐变，否则原色。"""
        color = self._bullet_color()
        if self._breath_interval is None:
            return color
        s = (1 + math.sin(self._breath_phase)) / 2   # 0..1 平滑正弦
        return _scale_color(color, BREATH_DIM_MIN + (1 - BREATH_DIM_MIN) * s)

    def start_breathing(self) -> None:
        """进入进行中状态：启动渐变定时器（原色↔压暗色平滑呼吸）。重复调用无操作。"""
        if self._breath_interval is not None:
            return
        # 从最亮相位启动：避免短暂节点停在"中亮度"看起来像没变色
        self._breath_phase = BREATH_START_PHASE
        self._breath_interval = self.set_interval(BREATH_TICK, self._tick_breath)
        self._refresh()

    def stop_breathing(self) -> None:
        """结束进行中状态：停定时器、恢复原色。"""
        if self._breath_interval is not None:
            self._breath_interval.stop()
            self._breath_interval = None
            self._breath_phase = 0.0
            self._refresh()

    def _tick_breath(self) -> None:
        self._breath_phase = (self._breath_phase
                              + 2 * math.pi * BREATH_TICK / BREATH_CYCLE) % (2 * math.pi)
        self._refresh()

    def on_unmount(self) -> None:
        """节点移除时停掉呼吸定时器（防泄漏）。"""
        if self._breath_interval is not None:
            self._breath_interval.stop()
            self._breath_interval = None

    def _build_renderable(self):
        raise NotImplementedError

    def _refresh(self) -> None:
        self.update(self._build_renderable())

    def _toggle(self) -> None:
        """子类覆盖：切换折叠状态。"""

    # ── 交互 ──

    def on_click(self, event: Click) -> None:
        # 阻止 Textual 默认行为（focus / 文本选中），节点交互完全自定义
        event.prevent_default()
        if event.button == 3:  # 右键 → 复制
            self._copy_to_clipboard()
            event.stop()
            return
        if event.button != 1:  # 仅左键检测双击
            return
        if not self._collapsible:
            return
        now = time.monotonic()
        if 0 < (now - self._last_click) * 1000 < DOUBLE_CLICK_MS:
            self._toggle()
            event.stop()  # 双击折叠/展开不传播，避免触发选中/复制
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
    _bullet_style = BULLET_THINK
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
            t.append_text(self._label_line("think"))
            if self._thinking:
                t.append("\n")
                # guide 随连接符：非末节点（├）续行带 │ 保持竖线连续；
                # 末节点（└）仅缩进 —— 运行中不在下方"长出"连接符
                guide = self._connector == "├"
                t.append_text(_guide_indented(
                    self._thinking, indent="  " if guide else "    ",
                    style="italic #888888", guide=guide))
            return t
        return self._label_line("think")


class ToolNode(TreeNode):
    """工具调用节点：● 工具名 参数  耗时。展开显示结果。"""

    _collapsible = True
    _bullet_style = BULLET_TOOL
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
        return BULLET_ERROR if self._is_error else self._bullet_style

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._refresh()

    def _build_renderable(self):
        line = self._label_line(self._label())
        if not self._expanded:
            return line
        body = self._result or self._error or ""
        if not body:
            return line
        t = Text()
        t.append_text(line)
        t.append("\n")
        guide = self._connector == "├"
        t.append_text(_guide_indented(body, indent="  " if guide else "    ",
                                      style="dim", guide=guide))
        return t


class BodyNode(TreeNode):
    """正文节点：● 正文（Markdown）。流式阶段也渲染 Markdown，节流避免频繁重解析。

    渲染统一为：首行（行内 Markdown）与节点同行，换行后的正文 RichMarkdown
    缩进到文本列。流式按节流重解析（未换行时廉价逐 token 更新）；完成态立即渲染。
    """

    # 流式重解析节流（秒）：大 buffer 加大间隔，避免长文逐 token 解析卡顿
    STREAM_MD_THROTTLE = 0.08
    STREAM_MD_THROTTLE_LARGE = 0.25
    STREAM_MD_THROTTLE_HUGE = 0.5
    _LARGE_BUFFER = 32_000
    _HUGE_BUFFER = 128_000

    _collapsible = False
    _bullet_style = BULLET_BODY
    _kind = "body"

    def __init__(self, code_theme: str = "monokai",
                 stream_throttle: float = STREAM_MD_THROTTLE, **kwargs) -> None:
        super().__init__(**kwargs)
        self._code_theme = code_theme
        self._buffer = ""
        self._finished = False
        self._stream_throttle = stream_throttle
        self._last_stream_render = 0.0  # 上次 Markdown 重解析时间（仅换行后更新）
        self._md_cache_key: str | None = None   # 续行 markdown 缓存（呼吸每 80ms 刷新不重解析）
        self._md_cache = None

    def append_chunk(self, chunk: str) -> None:
        self._buffer += chunk
        if self._finished:
            return
        if "\n" not in self._buffer:
            # 未换行：整段都是首行 → 廉价行内渲染，逐 token 更新
            self._refresh()
        else:
            now = time.monotonic()
            if now - self._last_stream_render >= self._throttle():
                self._last_stream_render = now
                self._refresh()

    def _throttle(self) -> float:
        n = len(self._buffer)
        if n > self._HUGE_BUFFER:
            return self.STREAM_MD_THROTTLE_HUGE
        if n > self._LARGE_BUFFER:
            return self.STREAM_MD_THROTTLE_LARGE
        return self._stream_throttle

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
        first, sep, rest = self._buffer.partition("\n")
        node_line = self._node_line_with_inline(first)
        if not sep:
            return node_line
        md = self._markdown_for(rest)
        # 非末节点（├）：续行带 │ 引导线，正文换行不打断树左边框（修断链）；
        # 末节点（└）：纯缩进，运行/流式时下方不"长出"连接符
        if self._connector == "├":
            tail = _PrefixedLines(md)
        else:
            tail = Padding(md, (0, 0, 0, 4))
        return Group(node_line, tail)

    def _markdown_for(self, rest: str):
        """续行 RichMarkdown：按 rest 内容缓存，流式变化才重解析。
        呼吸渐变每 80ms 刷新时直接复用缓存，避免高频重解析卡顿。"""
        safe_rest = rest.replace("<", "&lt;").replace(">", "&gt;")
        if self._md_cache_key != safe_rest:
            self._md_cache_key = safe_rest
            try:
                self._md_cache = RichMarkdown(safe_rest, code_theme=self._code_theme)
            except Exception:
                self._md_cache = Text(safe_rest)
        return self._md_cache

    def _node_line_with_inline(self, first: str) -> Text:
        """节点行：│ ● + 首行（行内 Markdown 样式）。

        连接符与其他节点统一灰色（修复 BodyNode 此前无样式导致的 ├/└ 色差）。
        """
        t = Text()
        t.append(f"{self._connector} ", style=CONNECTOR_STYLE)
        t.append("● ", style=self._active_bullet_color())
        t.append_text(_render_inline_markdown(first))
        return t


class ErrorNode(TreeNode):
    """错误节点：● 红。折叠显示首行，展开显示全文。"""

    _bullet_style = BULLET_ERROR
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
            guide = self._connector == "├"
            t.append_text(_guide_indented(self._text,
                                          indent="  " if guide else "    ",
                                          guide=guide))
            return t
        return self._label_line("error " + self._summary(), self._bullet_style)


class SystemNode(TreeNode):
    """系统/命令节点：● 琥珀。命令结果不可折叠（直接完整显示）。"""

    _bullet_style = BULLET_SYSTEM
    _kind = "system"

    def __init__(self, text: str, **kwargs) -> None:
        super().__init__(plain_text=text, **kwargs)
        self._text = text
        # 命令结果 / 系统通知永远完整显示，不允许折叠
        self._collapsible = False
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
            guide = self._connector == "├"
            t.append_text(_guide_indented(self._text,
                                          indent="  " if guide else "    ",
                                          guide=guide))
            return t
        return self._label_line(self._summary(), self._bullet_style)


class TurnTree(Vertical):
    """一个 assistant 回合的树形容器。

    add_node 时，若新节点类型与上一个不同，先插入一行 │ 引导线。
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._last_kind: str | None = None
        self._turn_nodes: list[TreeNode] = []  # 不用 _nodes：会遮蔽 Textual 内部属性

    def add_node(self, node: TreeNode, kind: str) -> None:
        if should_separate(self._last_kind, kind):
            guide = Static("│")
            guide.add_class("tree-guide")
            self.mount(guide)
        if self._turn_nodes:
            self._turn_nodes[-1].set_connector("├")  # 前一个最末 → 中间
        node.set_connector("└")   # 新节点暂为最末（首节点也是 └，新节点加入后自动降级为 ├）
        self._turn_nodes.append(node)
        node.add_class("tree-node")
        self.mount(node)
        self._last_kind = kind
