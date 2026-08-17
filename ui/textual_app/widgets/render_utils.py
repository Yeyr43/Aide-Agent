"""回合树渲染辅助 — 纯渲染工具（与节点/树结构解耦）。

从 tree_nodes.py 提取：_PrefixedLines（wrap-aware 续行前缀）、行内 Markdown
解析、表格块检测、工具参数格式化、展开内容引导线。供各节点类型复用。
"""

from __future__ import annotations

import json
import re

from rich.measure import Measurement
from rich.segment import Segment
from rich.text import Text

# 树连接符（├ / └ / │）统一灰色
CONNECTOR_STYLE = "#555555"


def _is_table_block(text: str) -> bool:
    """检测文本是否以 Markdown 表格开头（首行表头 + 次行 --- 分隔行）。

    表格无法用"首行行内 + 续行"分拆渲染（首行表头会变纯文字），需整体走 RichMarkdown。
    """
    lines = text.split("\n")
    if len(lines) < 2:
        return False
    first = lines[0].strip()
    if not (first.startswith("|") and first.count("|") >= 2):
        return False
    second = lines[1].strip()
    return bool(re.match(r"^\|[\s\-:|]+\|$", second))


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


def _guide_tail(content: Text, guide: bool) -> "_PrefixedLines":
    """展开内容续行：guide=True 每行带 │，否则纯缩进。

    用 _PrefixedLines（wrap-aware）——_guide_indented 只对显式 \n 行加前缀，
    长单行内容在终端视觉 wrap 时续行会渲染到节点/连接符列（col 0）。
    """
    if guide:
        return _PrefixedLines(content, prefix="│   ", prefix_style=CONNECTOR_STYLE)
    return _PrefixedLines(content, prefix="    ")


class _PrefixedLines:
    """把任意 renderable 的每行前置 │ 引导线（树形续行保留竖线）。

    用于正文 RichMarkdown 尾部：非末节点时续行也要带 │，否则正文换行会
    打断树左边框（断链）。guide 由调用方按节点连接符决定——末节点（└）
    传纯缩进（Padding），非末节点（├）传本包装。

    skip_first：首行不加前缀——用于"长单行正文"在终端按宽度视觉 wrap 时，
    首行已是 `├ ● 正文…`（自带连接符），仅 wrap 产生的续行需要补 │。
    """

    def __init__(self, renderable, prefix: str = "│   ", prefix_style: str = CONNECTOR_STYLE,
                 skip_first: bool = False) -> None:
        self._inner = renderable
        self._prefix = prefix
        self._skip_first = skip_first
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
            if not (first and self._skip_first):
                yield Segment(self._prefix, self._prefix_style)
            first = False
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
