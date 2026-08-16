"""树组件层测试 — 纯函数 + 节点渲染（不依赖完整 App）。"""
import re
from unittest.mock import patch

import pytest
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text
from textual.widgets import Static

from ui.textual_app.widgets.tree_nodes import (
    should_separate, _format_args, _guide_indented, _render_inline_markdown,
    TurnTree, ThinkNode, ToolNode, BodyNode, ErrorNode, SystemNode,
)


def _console_segments(renderable):
    """用真实 Console 渲染 renderable，返回 (text, style_str) 段列表。

    Text 的命名主题样式（markdown.code 等）只在渲染期解析，
    因此断言样式必须渲染后看，不能只看 Text.spans。
    """
    c = Console(force_terminal=False, width=200)
    return [(seg.text, str(seg.style))
            for seg in c.render(renderable, c.options)
            if seg.text]


@pytest.fixture(autouse=True)
def _no_active_app_requirement():
    """Textual 8.x 中 Static.update() 需要 active App（否则抛 NoActiveAppError）。

    本测试只关心 _build_renderable() 的渲染逻辑，不关心 App 刷新，
    故将 update 置为 no-op，隔离节点渲染与 App 依赖。
    """
    with patch.object(Static, "update", lambda self, *args, **kwargs: None):
        yield


class TestShouldSeparate:
    def test_first_node_no_separator(self):
        assert should_separate(None, "think") is False

    def test_same_kind_no_separator(self):
        assert should_separate("tool", "tool") is False

    def test_different_kind_separates(self):
        assert should_separate("think", "tool") is True
        assert should_separate("tool", "body") is True


class TestGuideIndented:
    def test_text_aligned_to_label_column(self):
        """内容文本从第 4 列开始，与节点标签文本列对齐（│ ● 前缀 4 字符）。"""
        t = _guide_indented("hello")
        assert t.plain == "│   hello"  # │(0) 空格(1) 空格(2) 空格(3) hello(4)
        assert t.plain.index("hello") == 4

    def test_multiline_each_line_aligned(self):
        t = _guide_indented("a\nb")
        assert t.plain.split("\n")[1].index("b") == 4


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

    def test_body_finished_multi_line_uses_markdown_tail(self):
        node = BodyNode()
        node.append_chunk("正文第一行\n正文**加粗**第二行")
        node.finish()
        r = node._build_renderable()
        assert isinstance(r, Group)
        # 首段是节点行（│ ● 正文第一行），第二段是 Markdown 尾部
        assert "正文第一行" in r.renderables[0].plain
        assert "第二行" not in r.renderables[0].plain

    def test_body_finished_single_line_stays_on_node_row(self):
        node = BodyNode()
        node.append_chunk("正文第一行")
        node.finish()
        r = node._build_renderable()
        assert isinstance(r, Text)
        assert "正文第一行" in r.plain

    def test_body_streaming_with_newline_uses_markdown_tail(self):
        """流式正文换行后：首行接节点行，尾部 RichMarkdown 缩进到文本列。"""
        node = BodyNode()
        node.append_chunk("第一行\n第二行")
        r = node._build_renderable()
        assert isinstance(r, Group)
        assert "第一行" in r.renderables[0].plain
        tail = r.renderables[1]
        assert isinstance(tail, Padding)
        assert isinstance(tail.renderable, Markdown)
        # 左填充 4 空格 → 缩进到文本列（第 4 列）
        segs = _console_segments(tail)
        assert any(text == "    " for text, _ in segs)
        assert any("第二行" in text for text, _ in segs)

    def test_error_node_prefix(self):
        node = ErrorNode("401 认证失败")
        r = node._build_renderable()
        assert "401" in r.plain

    def test_system_node_prefix(self):
        node = SystemNode("命令完成")
        r = node._build_renderable()
        assert "命令完成" in r.plain


class TestBulletColors:
    """● 按节点类型着色（工具绿 / 系统黄 / 正文白）；连接符统一灰。"""

    def test_tool_bullet_green(self):
        node = ToolNode("read_file", {"path": "x"})
        node.set_result("ok")
        r = node._build_renderable()
        assert any("5cb85c" in str(s.style) for s in r.spans)

    def test_system_bullet_yellow(self):
        node = SystemNode("系统消息")
        r = node._build_renderable()
        assert any("d0b000" in str(s.style) for s in r.spans)

    def test_body_bullet_white_and_connector_gray(self):
        node = BodyNode()
        node.append_chunk("正文第一行")
        r = node._build_renderable()
        assert isinstance(r, Text)
        styles = [str(s.style) for s in r.spans]
        assert any("ffffff" in st for st in styles)  # ● 白
        assert any("555555" in st for st in styles)  # 连接符统一灰

    def test_body_connector_matches_tool_connector(self):
        """BodyNode 与 ToolNode 连接符同为统一灰（回归：body 曾无样式导致 ├/└ 色差）。"""
        tool = ToolNode("read_file", {"path": "x"})
        tool.set_result("ok")
        body = BodyNode()
        body.append_chunk("正文")
        tool_conns = [str(s.style) for s in tool._build_renderable().spans]
        body_conns = [str(s.style) for s in body._build_renderable().spans]
        assert any("555555" in st for st in tool_conns)
        assert any("555555" in st for st in body_conns)


class TestRenderInlineMarkdown:
    """行内 Markdown → 带样式 Text（首行专用转换器）。"""

    def test_plain_passthrough(self):
        t = _render_inline_markdown("普通文本 123")
        assert t.plain == "普通文本 123"

    def test_bold(self):
        assert _render_inline_markdown("**加粗**").plain == "加粗"

    def test_code(self):
        assert _render_inline_markdown("`code`").plain == "code"

    def test_italic(self):
        assert _render_inline_markdown("*斜体*").plain == "斜体"

    def test_strike(self):
        assert _render_inline_markdown("~~删除~~").plain == "删除"

    def test_link(self):
        assert _render_inline_markdown("[链接](https://x.com)").plain == "链接"

    def test_mixed(self):
        t = _render_inline_markdown("前 **b** 中 `c` 后 *i*")
        assert t.plain == "前 b 中 c 后 i"

    def test_unclosed_bold_stays_literal(self):
        """流式语义：还没写完的 ** 保持字面量。"""
        assert _render_inline_markdown("**未闭合").plain == "**未闭合"

    def test_code_does_not_bold_inside(self):
        t = _render_inline_markdown("`**code**`")
        assert t.plain == "**code**"
        assert len(t.spans) == 1  # 整个是 code 段

    def test_brackets_not_treated_as_markup(self):
        assert _render_inline_markdown("[200] 和 <tag>").plain == "[200] 和 <tag>"

    def test_code_style_matches_body(self):
        """行内代码用 markdown.code 主题样式 — 与正文 RichMarkdown 一致。"""
        segs = _console_segments(_render_inline_markdown("`code`"))
        assert any(text == "code" and "bold cyan on black" in style
                   for text, style in segs)

    def test_bold_style_matches_body(self):
        segs = _console_segments(_render_inline_markdown("**加粗**"))
        assert any(text == "加粗" and "bold" in style for text, style in segs)

    def test_italic_style(self):
        segs = _console_segments(_render_inline_markdown("*斜体*"))
        assert any(text == "斜体" and "italic" in style for text, style in segs)


class TestBodyStreamingMarkdown:
    """流式正文实时 Markdown 渲染 + 节流。"""

    def test_streaming_markdown_tail_bold_rendered(self):
        node = BodyNode()
        node.append_chunk("正文第一行\n**加粗**尾部")
        r = node._build_renderable()
        assert isinstance(r, Group)
        # 尾部是 RichMarkdown，加粗被渲染（首行 node_line 不含尾部）
        assert "正文第一行" in r.renderables[0].plain
        assert "加粗" not in r.renderables[0].plain
        segs = _console_segments(r.renderables[1])
        assert any(text == "加粗" and "bold" in style for text, style in segs)

    def test_streaming_single_line_is_text_with_inline(self):
        node = BodyNode()
        node.append_chunk("**好的** 继续")
        r = node._build_renderable()
        assert isinstance(r, Text)
        assert "好的 继续" in r.plain

    def test_streaming_first_line_inline_bold_applied(self):
        node = BodyNode()
        node.append_chunk("**好的**\n正文")
        r = node._build_renderable()
        assert isinstance(r, Group)
        segs = _console_segments(r.renderables[0])
        assert any(text == "好的" and "bold" in style for text, style in segs)

    def test_throttle_skips_renders_within_window(self):
        node = BodyNode(stream_throttle=0.08)
        node._buffer = "第一行\n"  # 已有换行 → 走节流路径
        node._last_stream_render = 0.0
        clock = iter([0.05, 0.20])
        with patch("ui.textual_app.widgets.tree_nodes.time.monotonic",
                   side_effect=lambda: next(clock)), \
             patch.object(BodyNode, "_refresh") as mock:
            node.append_chunk("a")  # 0.05 < 0.08 → 跳过
            node.append_chunk("b")  # 0.20 ≥ 0.08 → 渲染
        assert mock.call_count == 1

    def test_no_newline_renders_every_chunk(self):
        node = BodyNode()
        with patch.object(BodyNode, "_refresh") as mock:
            node.append_chunk("a")
            node.append_chunk("b")
        assert mock.call_count == 2

    def test_finish_forces_render_within_throttle(self):
        node = BodyNode(stream_throttle=0.08)
        node._buffer = "第一行\n"
        node._last_stream_render = 1.0
        with patch("ui.textual_app.widgets.tree_nodes.time.monotonic",
                   return_value=1.03), \
             patch.object(BodyNode, "_refresh") as mock:
            node.append_chunk("a")  # 1.03-1.0 < 0.08 → 跳过
            node.finish()           # 强制渲染
        assert mock.call_count == 1

    def test_finished_plain_is_raw_buffer(self):
        node = BodyNode()
        node.append_chunk("**正文**\n第二行")
        node.finish()
        assert node._plain == "**正文**\n第二行"


class TestBreathing:
    """进行中节点呼吸效果：● 原色 ↔ 压暗色。"""

    def test_dim_color_darkens(self):
        from ui.textual_app.widgets.tree_nodes import _dim_color
        assert _dim_color("#5cb85c") == "#295229"
        assert _dim_color("#ffffff") == "#727272"
        assert _dim_color("not-a-color") == "not-a-color"  # 解析失败原样返回

    def test_bright_phase_uses_original_color(self):
        """正弦峰值（相位 π/2）→ 亮度因子 1.0 → 原色。"""
        import math
        node = ToolNode("read_file", {"path": "x"})
        node._breath_interval = object()  # 模拟呼吸中（不启动真实定时器）
        node._breath_phase = math.pi / 2
        from ui.textual_app.widgets.tree_nodes import BULLET_TOOL
        assert node._active_bullet_color() == BULLET_TOOL

    def test_dark_phase_dims_to_min(self):
        """正弦谷底（相位 3π/2）→ 亮度因子 0.45 → 最暗色。"""
        import math
        from ui.textual_app.widgets.tree_nodes import _dim_color, BULLET_TOOL
        node = ToolNode("read_file", {"path": "x"})
        node._breath_interval = object()
        node._breath_phase = 3 * math.pi / 2
        assert node._active_bullet_color() == _dim_color(BULLET_TOOL)

    def test_gradient_interpolates_between_bright_and_dark(self):
        """渐变：相位 0 应落在原色与最暗色之间（平滑插值而非硬开关）。"""
        import math
        from ui.textual_app.widgets.tree_nodes import _scale_color, BULLET_TOOL
        node = ToolNode("read_file", {"path": "x"})
        node._breath_interval = object()
        node._breath_phase = 0.0  # sin=0 → 亮度因子 0.725
        mid = node._active_bullet_color()
        assert mid != BULLET_TOOL
        assert mid != _scale_color(BULLET_TOOL, 0.45)
        assert mid == _scale_color(BULLET_TOOL, 0.725)

    def test_not_breathing_uses_original(self):
        from ui.textual_app.widgets.tree_nodes import BULLET_TOOL
        node = ToolNode("read_file", {"path": "x"})
        assert node._active_bullet_color() == BULLET_TOOL

    def test_tool_error_color_applied_when_not_breathing(self):
        """出错时 stop_breathing 已先执行 → 显示完整错误红。"""
        from ui.textual_app.widgets.tree_nodes import BULLET_ERROR
        node = ToolNode("read_file", {"path": "x"})
        node.set_error("boom")
        assert node._active_bullet_color() == BULLET_ERROR
