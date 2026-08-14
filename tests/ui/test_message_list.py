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
