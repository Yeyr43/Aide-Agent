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


@pytest.mark.asyncio
async def test_scroll_pin_follows_bottom_unless_user_scrolls_up():
    """滚动吸附：底部跟随输出，上翻解除，输入强制回底。

    回归"输出时上翻鬼畜"：流式渲染时 _scroll_end 无条件滚底把视图拽回底部。
    修复后仅吸附状态（在底部）跟随，用户上翻解除吸附。
    """
    app = ScrollLayoutTestApp()
    async with app.run_test(size=(100, 30)) as pilot:
        ml = app.query_one("#messages", MessageList)
        paras = "\n\n".join(f"第{i}段：这是一个段落，包含一些说明文字和内容。"
                            for i in range(40))
        ml.add_user_message("问题")
        ml.add_ai_chunk(paras)
        ml.finish_ai_message()
        await pilot.pause(0.2)  # 等布局刷新 max_scroll_y（全量跑时序更慢）

        # 可滚动（长内容超出视口）
        assert ml.max_scroll_y > 0
        # 默认吸附在底部
        assert ml._pinned is True
        assert ml.scroll_y >= ml.max_scroll_y - 0.5

        # 用户上翻 → 解除吸附
        ml.scroll_home(animate=False)
        await pilot.pause(0.1)
        assert ml._pinned is False
        assert ml.scroll_y < 1

        # 上翻后新内容不滚回底部（不再鬼畜）
        ml.add_ai_chunk("\n\n上翻期间新增的输出不应抢滚动位置。")
        await pilot.pause(0.1)
        assert ml._pinned is False
        assert ml.scroll_y < ml.max_scroll_y

        # 滚回底部 → 重新吸附
        ml.scroll_end(animate=False)
        await pilot.pause(0.1)
        assert ml._pinned is True

        # 输入新消息 → 强制回底并吸附（即使此前上翻）
        ml.scroll_home(animate=False)
        await pilot.pause(0.1)
        assert ml._pinned is False
        ml.add_user_message("新问题")
        await pilot.pause(0.1)
        assert ml._pinned is True
        assert ml.scroll_y >= ml.max_scroll_y - 0.5


@pytest.mark.asyncio
async def test_sticky_pins_short_message_top_while_streaming():
    """短消息 + 长回复 → 消息钉在窗口顶部（不区分消息是否足一屏）。

    回归需求：短消息也应钉住（v2 只钉超窗高消息）。钉住 = 流内消息
    display:none + 固定头显示消息副本；树正常显示，滚动范围零扰动。
    """
    app = ScrollLayoutTestApp()
    async with app.run_test(size=(100, 30)) as pilot:
        ml = app.query_one("#messages", MessageList)
        ml.add_user_message("帮我写个总结")
        await pilot.pause(0.1)
        msg = app.query(".user-message")[-1]
        msg_h = msg.size.height
        assert msg_h < ml.size.height  # 短消息（不足一屏）

        paras = "\n\n".join(f"第{i}段：这是一个段落，包含一些说明文字和内容。"
                            for i in range(40))
        ml.add_ai_chunk(paras)
        ml.finish_ai_message()
        await pilot.pause(0.2)

        # 已钉住：流内消息隐藏 + 固定头显示消息副本
        assert ml._pinned_msg is msg
        assert msg.styles.display == "none"
        header = ml._sticky_header
        assert header.styles.display == "block"
        assert header._plain_content == "帮我写个总结"
        assert int(header.styles.height.value) == msg_h
        # 钉住位置紧贴顶部标题栏：header 屏内 y == 2（标题栏 1 行 + #messages 上 padding 1 行）
        assert header.region.y == 2, f"应紧贴标题栏（屏内 y=2），实际 {header.region.y}"

        # 树可滚动（钉住不改变滚动范围：长内容超出视口）
        assert ml.max_scroll_y > ml.size.height

        # 流式继续：钉住保持
        ml.add_ai_chunk("\n\n更多回复内容，继续向下生长。")
        await pilot.pause(0.1)
        assert ml._pinned_msg is msg


@pytest.mark.asyncio
async def test_sticky_pin_does_not_hold_scroll():
    """钉住 ≠ 锁滚动：树中可自由滚动、位置不被拽回（钉住是视觉固定头）。"""
    app = ScrollLayoutTestApp()
    async with app.run_test(size=(100, 30)) as pilot:
        ml = app.query_one("#messages", MessageList)
        ml.add_user_message("问题")
        paras = "\n\n".join(f"第{i}段：这是一个段落，包含一些说明文字和内容。"
                            for i in range(40))
        ml.add_ai_chunk(paras)
        ml.finish_ai_message()
        await pilot.pause(0.2)
        assert ml._pinned_msg is not None  # 已钉住

        tree = ml._msg_trees[0]
        t_top = tree.virtual_region_with_margin.y
        for offset in (8, 20, 5):
            ml.scroll_to(y=t_top + offset, animate=False)
            await pilot.pause(0.1)
            assert abs(ml.scroll_y - (t_top + offset)) <= 1  # 位置保持
            assert ml._pinned_msg is not None                # 仍钉住


@pytest.mark.asyncio
async def test_sticky_releases_when_message_fits():
    """消息顶回到视口（可正常显示）→ 钉住解除，消息恢复流内显示。

    相同内容下 max_scroll_y 钉住/释放前后一致（display:none 占位与
    dock 间距抵消 → 零扰动）。
    """
    app = ScrollLayoutTestApp()
    async with app.run_test(size=(100, 30)) as pilot:
        ml = app.query_one("#messages", MessageList)
        ml.add_user_message("问题")
        paras = "\n\n".join(f"第{i}段：这是一个段落，包含一些说明文字和内容。"
                            for i in range(40))
        ml.add_ai_chunk(paras)
        ml.finish_ai_message()
        await pilot.pause(0.2)
        assert ml._pinned_msg is not None
        max_pinned = ml.max_scroll_y

        ml.scroll_home(animate=False)
        await pilot.pause(0.1)
        assert ml._pinned_msg is None
        msg = app.query(".user-message")[-1]
        assert msg.styles.display == "block"  # 恢复显示
        assert abs(ml.max_scroll_y - max_pinned) <= 1  # 滚动范围不变


@pytest.mark.asyncio
async def test_sticky_switches_between_messages():
    """多轮对话：滚动经过前一轮 → 钉住切到下一轮（消息树滑出窗口即释放）。"""
    app = ScrollLayoutTestApp()
    async with app.run_test(size=(100, 30)) as pilot:
        ml = app.query_one("#messages", MessageList)
        ml.add_user_message("第一问")
        paras1 = "\n\n".join(f"第一轮第{i}段：内容。" for i in range(30))
        ml.add_ai_chunk(paras1)
        ml.finish_ai_message()
        await pilot.pause(0.1)
        ml.add_user_message("第二问")
        paras2 = "\n\n".join(f"第二轮第{i}段：内容。" for i in range(30))
        ml.add_ai_chunk(paras2)
        ml.finish_ai_message()
        await pilot.pause(0.2)

        msgs = app.query(".user-message")
        msg1, msg2 = msgs[-2], msgs[-1]

        # 底部：第二问钉住（第一问的树已滑出窗口）
        assert ml._pinned_msg is msg2

        # 滚到第一轮树中部：第一问钉住（其树在窗口中，第二问尚未进入视口）
        tree1 = ml._msg_trees[0]
        t1_top = tree1.virtual_region_with_margin.y
        ml.scroll_to(y=t1_top + 5, animate=False)
        await pilot.pause(0.1)
        assert ml._pinned_msg is msg1

        # 滚到顶部：无钉住（第一问可正常显示）
        ml.scroll_home(animate=False)
        await pilot.pause(0.1)
        assert ml._pinned_msg is None


@pytest.mark.asyncio
async def test_sticky_tall_message_scrolls_naturally():
    """消息 ≥ 一屏：钉住会盖住回复（违背"消息树正常显示"）→ 跳过，自然滚动。"""
    app = ScrollLayoutTestApp()
    async with app.run_test(size=(100, 30)) as pilot:
        ml = app.query_one("#messages", MessageList)
        long = "\n\n".join(f"第{i}行：很长的用户消息，用来撑高气泡框超过一屏。"
                           for i in range(40))
        ml.add_user_message(long)
        await pilot.pause(0.2)
        msg = app.query(".user-message")[-1]
        assert msg.size.height > ml.size.height  # 确实超窗高

        paras = "\n\n".join(f"第{i}段：这是回复内容。" for i in range(40))
        ml.add_ai_chunk(paras)
        ml.finish_ai_message()
        await pilot.pause(0.2)

        # 不钉住（自然滚动），树可自由滚动
        assert ml._pinned_msg is None
        tree = ml._msg_trees[0]
        t_top = tree.virtual_region_with_margin.y
        ml.scroll_to(y=t_top + 5, animate=False)
        await pilot.pause(0.1)
        assert abs(ml.scroll_y - (t_top + 5)) <= 1
        assert ml._pinned_msg is None


@pytest.mark.asyncio
async def test_sticky_clear_resets_pin():
    """clear() 解除钉顶并复位状态（固定头隐藏、消息恢复显示）。"""
    app = ScrollLayoutTestApp()
    async with app.run_test(size=(100, 30)) as pilot:
        ml = app.query_one("#messages", MessageList)
        ml.add_user_message("问题")
        paras = "\n\n".join(f"第{i}段：内容。" for i in range(40))
        ml.add_ai_chunk(paras)
        ml.finish_ai_message()
        await pilot.pause(0.2)
        assert ml._pinned_msg is not None

        ml.clear()
        await pilot.pause(0.1)
        assert ml._pinned_msg is None
        assert ml._sticky_header.styles.display == "none"
        assert ml._user_msgs == []
        assert ml._msg_trees == []


@pytest.mark.asyncio
async def test_pinned_box_cjk_not_split_by_tree_cut():
    """树连接符列不再产生 compositor cut，切断钉住框的双宽 CJK 字符。

    回归：树滚到钉住框背后时，.tree-node/.tree-guide 若用 margin-left 缩进，
    其区域左边缘落在列 11 → 该处产生 cut，框内容跨列 11 的双宽字符（如"题"）
    被切断显示为空白。改用 padding-left 后区域左边缘回到列 2，字符不再被切。
    """
    app = ScrollLayoutTestApp()
    async with app.run_test(size=(80, 24)) as pilot:
        ml = app.query_one("#messages", MessageList)
        ml.add_user_message("我的问题")   # "题" 在列 10-11，横跨列 11
        ml.add_thinking_chunk("思考中")
        ml.add_ai_chunk("正文第一行内容。")
        for i in range(30):
            ml.add_ai_chunk(f"第{i}段：内容比较长用来撑高，abcdefghijklmn。")
        ml.finish_ai_message()
        await pilot.pause(0.2)
        assert ml._pinned_msg is not None
        hdr = ml._sticky_header
        content_y = hdr.region.y + 1  # 框内容行（中间行）

        # 扫多个 sy：树第一行（连接符）从框下方滚到框内容行背后
        for sy in range(3, 8):
            ml.scroll_to(y=sy, animate=False, immediate=True)
            await pilot.pause(0.04)
            assert ml._pinned_msg is not None, f"sy={sy} 应处于钉住状态"
            strips = app.screen._compositor.render_strips()
            text = strips[content_y].text
            assert "我的问题" in text, f"sy={sy} 钉住框内容被树 cut 切断: {text[:30]!r}"


@pytest.mark.asyncio
async def test_spacing_message_tree_compact():
    """间距：消息↔自己的树 1 行（紧凑）；上一棵树尾↔下一消息框 4 行（避免钉顶冲突）。

    纵向 margin 折叠为相邻两者较大值，故盒到盒间距即折叠结果。
    """
    app = ScrollLayoutTestApp()
    async with app.run_test(size=(100, 30)) as pilot:
        ml = app.query_one("#messages", MessageList)
        ml.add_user_message("第一问")
        ml.add_ai_chunk("第一轮回复。")
        ml.finish_ai_message()
        await pilot.pause(0.1)
        ml.add_user_message("第二问")
        ml.add_ai_chunk("第二轮回复。")
        ml.finish_ai_message()
        await pilot.pause(0.2)

        msgs = app.query(".user-message")
        m1, m2 = msgs[-2], msgs[-1]
        t1 = ml._msg_trees[0]
        gap_mt = t1.virtual_region.y - m1.virtual_region.bottom
        gap_tm = m2.virtual_region.y - t1.virtual_region.bottom
        assert gap_mt == 1, f"消息↔自己的树应 1 行，实际 {gap_mt}"
        assert gap_tm == 4, f"树尾↔下一消息框应 4 行，实际 {gap_tm}"


@pytest.mark.asyncio
async def test_sticky_releases_when_tree_hidden_behind_header():
    """消息树被钉住标题完全遮挡（视觉消失）→ 钉住释放，不与下一消息框冲突。

    回归诊断：树被标题遮挡后几何上仍在窗口中，旧逻辑钉住不释放 → 与下一消息框并排冲突。
    修复：树尾进入标题波段（t.bottom <= sy + 消息占位高）即释放。
    """
    app = ScrollLayoutTestApp()
    async with app.run_test(size=(100, 30)) as pilot:
        ml = app.query_one("#messages", MessageList)
        ml.add_user_message("第一问")
        ml.add_ai_chunk("第一轮回复。")   # 短树
        ml.finish_ai_message()
        await pilot.pause(0.1)
        ml.add_user_message("第二问")
        paras = "\n\n".join(f"第{i}段：内容。" for i in range(40))
        ml.add_ai_chunk(paras)             # 长树（把可滚动区撑大，目标位置可达）
        ml.finish_ai_message()
        await pilot.pause(0.2)

        msgs = app.query(".user-message")
        m1, m2 = msgs[-2], msgs[-1]
        t1 = ml._msg_trees[0]

        # 回顶 → 无钉住，读取未钉住几何
        ml.scroll_home(animate=False)
        await pilot.pause(0.1)
        assert ml._pinned_msg is None
        H1 = m1.virtual_region_with_margin.height
        t1b = t1.virtual_region_with_margin.bottom

        # 树尾可见 → M1 钉住
        ml.scroll_to(y=m1.virtual_region.y + 1, animate=False)
        await pilot.pause(0.1)
        assert ml._pinned_msg is m1, "树尾可见时应钉住 M1"

        # 树尾进入标题波段（完全遮挡）→ M1 释放
        ml.scroll_to(y=t1b - H1, animate=False)
        await pilot.pause(0.1)
        assert ml._pinned_msg is None, "树被标题完全遮挡时 M1 应释放（无钉住）"
