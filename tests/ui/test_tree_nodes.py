"""树组件层测试 — 纯函数 + 节点渲染（不依赖完整 App）。"""
import re
from unittest.mock import patch

import pytest
from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text
from textual.widgets import Static

from ui.textual_app.widgets.tree_nodes import (
    should_separate, _format_args, _guide_indented, TurnTree, ThinkNode,
    ToolNode, BodyNode, ErrorNode, SystemNode,
)


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

    def test_body_streaming_continuation_aligned_to_text_column(self):
        """流式正文：首行接节点行，后续行缩进到文本列（第 4 列）。"""
        node = BodyNode()
        node.append_chunk("第一行\n第二行")
        r = node._build_renderable()
        assert isinstance(r, Text)
        lines = r.plain.split("\n")
        assert "● 第一行" in lines[0]
        assert lines[0].index("第一行") == 4  # 节点行文本列 = 第 4 列
        assert lines[1] == "    第二行"  # 4 空格 = │ ● 到文本列的宽度

    def test_error_node_prefix(self):
        node = ErrorNode("401 认证失败")
        r = node._build_renderable()
        assert "401" in r.plain

    def test_system_node_prefix(self):
        node = SystemNode("命令完成")
        r = node._build_renderable()
        assert "命令完成" in r.plain
