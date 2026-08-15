"""MessageList 树形流式集成测试（Textual run_test harness）。"""
from pathlib import Path

import pytest
from textual.app import App
from textual.containers import Vertical
from textual.widgets import Static

from ui.textual_app.widgets.message_list import MessageList

# 真实 app.tcss：让测试布局与实际运行一致（含 .turn-tree height:auto）
_APP_TCSS = (Path(__file__).resolve().parents[2]
             / "ui" / "textual_app" / "app.tcss").read_text(encoding="utf-8")


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
        r = think._build_renderable()
        assert "思考" not in r.plain


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


@pytest.mark.asyncio
async def test_one_turn_tree_across_tool_flow():
    """一次用户回合（think→工具→最终回答）应是一棵连续的树。"""
    app = MessageListTestApp()
    async with app.run_test():
        ml = app.ml
        # 第一次 LLM 调用：think + 部分正文 → on_text_done
        ml.add_thinking_chunk("思考中")
        ml.add_ai_chunk("让我看看")
        ml.finish_ai_message()
        # 工具执行
        ml.add_tool_start("read_file", {"path": "x"})
        ml.add_tool_done("read_file", "内容")
        # 第二次 LLM 调用：think + 最终回答 → on_text_done
        ml.add_thinking_chunk("继续")
        ml.add_ai_chunk("答案是 42")
        ml.finish_ai_message()
        # 整个回合应只有一棵树，正文跨段累计
        assert len(app.query(".turn-tree")) == 1
        assert ml._turn_ai_text == "让我看看答案是 42"


@pytest.mark.asyncio
async def test_tree_connectors_first_middle_last():
    """树连接符：首节点 └，新节点加入自动降级为 ├，末节点 └。"""
    app = MessageListTestApp()
    async with app.run_test():
        ml = app.ml
        ml.add_thinking_chunk("思考")                 # 首节点 → └
        ml.add_tool_start("read_file", {"path": "x"}) # 前一个降级 → ├，新节点 → └
        ml.add_tool_done("read_file", "r")
        ml.add_ai_chunk("回答")                       # 末节点 → └，前一个降级 ├
        nodes = [w for w in app.query(".tree-node")]
        assert len(nodes) == 3
        assert nodes[0]._connector == "├"
        assert nodes[1]._connector == "├"
        assert nodes[2]._connector == "└"


@pytest.mark.asyncio
async def test_restore_rebuilds_tree_details():
    """restore_conversation 按轮重建 think/工具/正文完整树（不丢细节）。"""
    app = MessageListTestApp()
    turns = [
        {
            "turn": 1,
            "thinking": "先搜索",
            "messages": [
                {"role": "user", "content": "查 TODO"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "search_in_files",
                                  "arguments": '{"query": "TODO"}'}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "a.py: TODO"},
                {"role": "assistant", "content": "找到了。"},
            ],
        },
        {
            "turn": 2,
            "thinking": "直接回",
            "messages": [
                {"role": "user", "content": "好的"},
                {"role": "assistant", "content": "不客气。"},
            ],
        },
    ]
    async with app.run_test():
        ml = app.ml
        ml.restore_conversation(turns)
        nodes = [w for w in app.query(".tree-node")]
        # turn1: think + tool + body; turn2: think + body
        assert [type(w).__name__ for w in nodes] == [
            "ThinkNode", "ToolNode", "BodyNode", "ThinkNode", "BodyNode",
        ]
        assert len(app.query(".turn-tree")) == 2
        # 工具结果已回填
        tool = nodes[1]
        assert tool._plain == "a.py: TODO"
        # 思考内容已恢复（折叠态，可展开）
        think = nodes[0]
        assert think._thinking == "先搜索"
        # 正文已完成
        body = nodes[2]
        assert body._buffer == "找到了。"
        assert body._finished is True


@pytest.mark.asyncio
async def test_restore_marks_tool_error_red():
    """恢复时工具错误结果应标记为错误态（红 ●）。"""
    app = MessageListTestApp()
    turns = [
        {
            "turn": 1,
            "thinking": "",
            "messages": [
                {"role": "user", "content": "跑一下"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "run_shell",
                                  "arguments": '{"command": "rm -rf"}'}}]},
                {"role": "tool", "tool_call_id": "c1",
                 "content": "⚠️ 高风险操作已被阻止"},
            ],
        },
    ]
    async with app.run_test():
        ml = app.ml
        ml.restore_conversation(turns)
        tool = [w for w in app.query(".tree-node")
                if type(w).__name__ == "ToolNode"][0]
        assert tool._is_error is True
        assert "高风险操作已被阻止" in tool._plain


class ScrollLayoutTestApp(App):
    """镜像 AideApp 对话页结构，加载真实 app.tcss。"""

    CSS = _APP_TCSS

    def compose(self):
        yield Static("", id="session-label")
        yield MessageList(id="messages")
        with Vertical(id="bottom-area"):
            yield Static("", id="input")
        yield Static("", id="status-bar")


@pytest.mark.asyncio
async def test_long_body_makes_message_list_scrollable():
    """回归：长正文不裁剪 — TurnTree 随内容生长（height:auto），MessageList 可滚动。

    根因：TurnTree 是 Vertical（Textual 默认 height:1fr + overflow:hidden），
    若固定填充视口，正文超出即被裁剪，滚动容器虚拟高度永远 ≤ 视口 → 无法上下翻滚。
    """
    app = ScrollLayoutTestApp()
    async with app.run_test(size=(100, 30)) as pilot:
        ml = app.query_one("#messages", MessageList)
        paras = "\n\n".join(f"第{i}段：这是一个段落，包含一些说明文字和内容。"
                            for i in range(40))
        ml.add_user_message("帮我写个总结")
        ml.add_ai_chunk(paras)
        ml.finish_ai_message()
        await pilot.pause()

        turn = ml.query_one(".turn-tree")
        # TurnTree 不被视口裁剪：布局尺寸 == 内容虚拟高度
        assert turn.size.height == turn.virtual_size.height
        assert turn.virtual_size.height > ml.size.height
        # MessageList 可滚动到更早内容
        assert ml.max_scroll_y > 0
        ml.scroll_to(y=ml.max_scroll_y)
        await pilot.pause()
        assert ml.scroll_y > 0
