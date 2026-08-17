"""消息流组件 — 树形回合展示。

每个 assistant 回合 = 一棵 TurnTree（tree_nodes.py）。
用户消息保留气泡框（MessageWidget），与树之间空一行。
流式：think 展开 → 结束折叠；工具精简单行；正文流式 Markdown。
交互：左键双击折叠/展开（可折叠节点）；右键点击复制。
钉顶（sticky）：用户消息顶滑出窗口顶、但其回合树仍在窗口中时，把消息
钉在窗口顶部（固定头 + 流内 display:none，占位抵消 → 树零扰动正常滚动）；
消息树被钉住标题完全遮挡（视觉消失）或消息可正常显示时解除，避免与下一个
消息框冲突。不区分消息是否足一屏；消息 ≥ 一屏时跳过（钉住会盖住整窗、回复不可见）。
"""

import json
import logging
import time

from rich.panel import Panel
from rich.markup import escape
from rich.text import Text
from textual.containers import VerticalScroll
from textual.events import Click, Resize
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
        self._pinned = True  # 滚动吸附：在底部时跟随输出，用户上翻解除
        self._user_msgs: list[MessageWidget] = []  # 按文档顺序的用户消息
        self._msg_trees: list[TurnTree | None] = []  # 与 _user_msgs 对齐的回合树（几何钉顶判定）
        self._pinned_msg: MessageWidget | None = None  # 当前钉顶的用户消息（视觉固定头）
        self._pinned_msg_top: float = 0.0  # 钉住时消息的自然顶部（scroll 内容坐标）
        self._pinned_msg_height: float = 0.0  # 钉住时消息的占位高度（含 margin，释放判定用）
        self._pinned_tree: TurnTree | None = None  # 钉住消息的回合树
        self._pinned_orig_display = ""  # 钉住前消息的 display 值（释放时恢复）
        self._sticky_header: MessageWidget | None = None  # dock:top 固定头（钉顶时显示消息副本）
        self._pin_disabled = False  # restore/clear 期间暂停钉顶判定

    def on_mount(self) -> None:
        """创建钉顶固定头（dock:top，初始隐藏，不参与流式布局）。"""
        header = MessageWidget("")
        header.add_class("sticky-header")
        header.styles.display = "none"
        header.styles.dock = "top"
        self._sticky_header = header
        self.mount(header)

    # ── 回合树管理 ──────────────────────────────────────────────

    def _ensure_turn(self) -> TurnTree:
        if self._current_turn is None:
            tree = TurnTree()
            tree.add_class("turn-tree")
            self.mount(tree)
            self._current_turn = tree
            self._associate_tree(tree)
        return self._current_turn

    def _associate_tree(self, tree: TurnTree) -> None:
        """把新建的回合树关联到最新用户消息（无用户消息的孤立树不关联）。"""
        for i in range(len(self._user_msgs) - 1, -1, -1):
            if self._msg_trees[i] is None:
                self._msg_trees[i] = tree
                break

    def _close_open_text(self) -> None:
        """折叠当前 think + 收尾当前正文（若存在）。"""
        if self._think_node is not None:
            self._think_node.stop_breathing()
            self._think_node.finish()
            self._think_node = None
        if self._body_node is not None:
            self._body_node.stop_breathing()
            self._body_node.finish()
            self._body_node = None

    def _close_turn(self) -> None:
        """关闭当前回合树（下一条用户消息 / 清空）。"""
        self._close_open_text()
        self._current_turn = None
        self._tool_fifo.clear()
        self._tool_start_times.clear()
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
            renderable=Panel(content, border_style="#555555"),
            image_paths=image_paths if image_paths else None,
            file_paths=all_file_paths if all_file_paths else None,
        )
        msg.add_class("user-message")
        self.mount(msg)
        self._user_msgs.append(msg)
        self._msg_trees.append(None)
        self._pinned = True  # 输入新消息 → 强制回到底部并吸附（即使此前上翻过）
        self._scroll_end()

    # ── 思考 ────────────────────────────────────────────────────

    def add_thinking_chunk(self, chunk: str) -> None:
        if self._think_node is None:
            tree = self._ensure_turn()
            self._think_node = ThinkNode()
            tree.add_node(self._think_node, "think")
            self._think_node.start_breathing()  # 思考流式中 → ● 呼吸
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
        node.start_breathing()  # 工具执行中 → ● 呼吸
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
        node.stop_breathing()  # 工具完成 → 停呼吸
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
            self._scroll_end()
            return
        node.stop_breathing()  # 工具出错 → 停呼吸
        node.set_error(error)
        node.set_duration(self._elapsed(id(node)))
        self._scroll_end()

    # ── 正文 ────────────────────────────────────────────────────

    def add_ai_chunk(self, chunk: str) -> None:
        if self._think_node is not None:  # 正文开始 → 思考折叠
            self._think_node.stop_breathing()
            self._think_node.finish()
            self._think_node = None
        tree = self._ensure_turn()
        if self._body_node is None:
            self._body_node = BodyNode(code_theme=self._code_theme)
            tree.add_node(self._body_node, "body")
            self._body_node.start_breathing()  # 正文流式中 → ● 呼吸
        self._body_node.append_chunk(chunk)
        self._turn_ai_text += chunk
        self._scroll_end()

    def finish_ai_message(self) -> str:
        """收尾当前流式文本（think 折叠、正文冻结为 Markdown），不关闭回合树。

        回合树在用户下一条消息时由 add_user_message 关闭——保证一次用户回合
        （含多次 FC 迭代的 think/工具/正文）显示为一棵连续的树。
        """
        self._close_open_text()
        return self._turn_ai_text

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
        self._release_sticky()
        self._current_turn = None
        self._think_node = None
        self._body_node = None
        self._tool_fifo.clear()
        self._tool_start_times.clear()
        self._turn_ai_text = ""
        self._user_msgs = []
        self._msg_trees = []
        for child in list(self.children):
            if child is self._sticky_header:
                continue  # 固定头保留，不随内容清空
            child.remove()

    def restore_conversation(self, turns: list[dict]) -> None:
        """按轮重建回合树 — 恢复 think / 工具 / 正文的完整细节。

        每轮记录来自 core.sessions.restorer.restore_turns：
            {"turn": N, "thinking": str, "messages": [raw msgs]}

        重建规则（镜像实时流程）：
          - 用户消息 → 气泡
          - thinking → 思考节点（折叠态）
          - assistant 带 tool_calls → 工具节点（按 tool_call_id 配对结果）
          - tool → 填充工具结果（错误结果标红）
          - assistant 带正文 → 正文节点（已完成态）
        """
        self._pin_disabled = True  # 重建期间暂停钉顶判定（布局不稳定）
        try:
            self._restore_turns(turns)
        finally:
            self._pin_disabled = False
            self._update_sticky_pin()  # 恢复后按最终布局判定一次
            self._scroll_end()

    def _restore_turns(self, turns: list[dict]) -> None:
        """restore_conversation 的实际重建循环（钉顶判定暂停期间执行）。"""
        self.clear()
        for turn in turns:
            msgs = turn.get("messages") or []
            thinking = (turn.get("thinking") or "").strip()

            # 1) 用户消息 → 气泡（关闭上一轮树，开启新一轮）
            for msg in msgs:
                if msg.get("role") == "user":
                    text, images = _parse_multimodal_content(msg.get("content", ""))
                    if text or images:
                        file_paths = msg.get("_image_paths") or images
                        self.add_user_message(text or "", file_paths=file_paths)
                    break

            # 2) 思考节点：优先逐条（assistant 消息带 _thinking，插回工具调用间）；
            #    旧格式（仅聚合 thinking）回退到顶部一个节点
            has_per_msg_thinking = any(
                isinstance(m.get("_thinking"), str) and m["_thinking"].strip()
                for m in msgs if m.get("role") == "assistant"
            )
            tree = self._ensure_turn()
            if not has_per_msg_thinking and thinking:
                node = ThinkNode()
                tree.add_node(node, "think")
                node.append_chunk(thinking)
                node.finish()

            # 3) 工具 / 正文节点（逐条思考插入到对应 assistant 消息前）
            pending_tools: list[ToolNode] = []
            for msg in msgs:
                role = msg.get("role", "")
                if role == "assistant":
                    if has_per_msg_thinking:
                        th = (msg.get("_thinking") or "").strip()
                        if th:
                            tn = ThinkNode()
                            tree.add_node(tn, "think")
                            tn.append_chunk(th)
                            tn.finish()
                    if msg.get("tool_calls"):
                        for tc in msg["tool_calls"]:
                            fn = tc.get("function") or {}
                            name = fn.get("name", "tool")
                            args = _parse_tool_args(fn.get("arguments"))
                            node = ToolNode(name, args, code_theme=self._code_theme)
                            tree.add_node(node, "tool")
                            pending_tools.append(node)
                    elif msg.get("content"):
                        text, _ = _parse_multimodal_content(msg["content"])
                        if text:
                            # 清理旧数据残留的 XML 工具块（修复前未提取的 <tool_call>/<invoke> 落盘了）
                            from core.kernel.xml_tool_parser import strip_xml_tool_blocks
                            text = strip_xml_tool_blocks(text)
                            body = BodyNode(code_theme=self._code_theme)
                            body.set_finished_text(text)
                            tree.add_node(body, "body")
                elif role == "tool" and pending_tools:
                    node = pending_tools.pop(0)
                    content = str(msg.get("content", ""))
                    if _is_tool_error(content):
                        node.set_error(content)
                    else:
                        node.set_result(content)

            # 每轮结束关闭状态（下一轮 / 用户新消息时重建树）
            self._close_turn()

    # ── 用户消息钉顶（几何 sticky：消息顶滑出窗口但回合树仍在窗口）──────────

    def _update_sticky_pin(self) -> None:
        """按当前 scroll_y 判定钉顶状态 — 幂等：仅状态变化时改 DOM。

        机制：钉住 = 流内消息 display:none（占位取消）+ dock:top 固定头显示
        消息副本。display:none 移除的占位 == 固定头 dock 间距（等高 + 同 margin），
        两者抵消 → 树的位置与 max_scroll_y 不变，无需滚动补偿，树自由滚动。
        """
        if self._pin_disabled or self._sticky_header is None:
            return
        if self._pinned_msg is not None:
            if self._pinned_should_release():
                self._release_sticky()
            else:
                # 锚点跟随：滚动使下一消息顶滑出 → 切换到下一消息
                new_target = self._active_sticky_target()
                if new_target is not None and new_target[0] is not self._pinned_msg:
                    self._release_sticky()
                    self._engage_sticky(*new_target)
                return
        target = self._active_sticky_target()
        if target is not None:
            msg, tree, msg_top = target
            self._engage_sticky(msg, tree, msg_top)

    def _active_sticky_target(self) -> tuple[MessageWidget, TurnTree, float] | None:
        """找到当前应钉住的消息：(msg, tree, msg_top)。

        锚点语义：上方最近的消息顶已滑出窗口顶（msg_top < scroll_y），且消息
        不足一屏 → 钉住该消息作为上下文锚点（固定头显示消息副本，用户始终
        知道当前滚动位置属于哪条消息）。

        不依赖树的几何——多回合短树滚动到"树间隙"（上方树已整体滑过）时，
        原逻辑因要求"树尾仍可见"而永远不钉，长对话滚动时顶部失去消息锚点。
        """
        sy = self.scroll_y
        h = self.size.height
        if h <= 0:
            return None
        candidate = None
        for i, msg in enumerate(self._user_msgs):
            if not msg.is_mounted:
                continue
            try:
                box = msg.virtual_region
                region = msg.virtual_region_with_margin
            except Exception:
                continue
            if not box.size or not region.size:
                continue  # 尚未布局
            msg_top = float(box.y)  # 气泡盒顶（不含上边距）
            if msg_top >= sy:
                break  # 其后消息顶更大，未滑出
            if region.height >= h:
                continue  # 消息 ≥ 一屏：钉住会盖住回复，跳过
            tree = self._msg_trees[i] if i < len(self._msg_trees) else None
            if tree is None or not tree.is_mounted:
                continue
            candidate = (msg, tree, msg_top)  # 上方最近滑出的消息
        return candidate

    def _pinned_should_release(self) -> bool:
        """当前钉住是否应解除：消息可正常显示（顶回到视口）或消息 ≥ 一屏。"""
        sy = self.scroll_y
        h = self.size.height
        if self._pinned_msg_top >= sy:
            return True  # 消息顶回到视口 → 正常显示
        if self._pinned_msg_height >= h:
            return True  # 窗口缩小后消息 ≥ 一屏 → 钉住会盖住回复，释放
        return False

    def _engage_sticky(self, msg: MessageWidget, tree: TurnTree, msg_top: float) -> None:
        """钉住 msg：流内隐藏 + 固定头显示消息副本。"""
        header = self._sticky_header
        if header is None:
            return
        try:
            header.update(msg.content)  # 复制气泡（Panel）
            header._plain_content = msg._plain_content
            header._image_paths = msg._image_paths
            header._file_paths = msg._file_paths
            header.styles.height = msg.size.height  # 与消息同高 → dock 间距 == 消息占位
        except Exception:
            pass
        self._pinned_msg = msg
        self._pinned_msg_top = msg_top
        try:
            self._pinned_msg_height = float(msg.virtual_region_with_margin.height)
        except Exception:
            self._pinned_msg_height = 0.0
        self._pinned_tree = tree
        self._pinned_orig_display = msg.styles.display
        header.styles.display = "block"
        msg.styles.display = "none"
        self.refresh()  # 强制重绘消息区：避免钉住切换时的残留/重叠

    def _release_sticky(self) -> None:
        """解除钉顶：隐藏固定头，恢复流内消息显示。"""
        if self._sticky_header is not None:
            self._sticky_header.styles.display = "none"
        if self._pinned_msg is not None:
            self._pinned_msg.styles.display = self._pinned_orig_display
        self._pinned_msg = None
        self._pinned_msg_top = 0.0
        self._pinned_msg_height = 0.0
        self._pinned_tree = None
        self._pinned_orig_display = ""
        self.refresh()  # 强制重绘消息区：避免解除钉顶时的残留/重叠

    def on_resize(self, event: Resize) -> None:
        """窗口尺寸变化 → 重新判定钉顶（钉住消息可能因缩小而不适用）。"""
        self._update_sticky_pin()

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        """吸附状态判定：在底部 → 跟随输出；上翻 → 解除吸附。钉顶独立于吸附。

        覆盖 Textual 的 reactive watcher（super() 保留滚动条位置更新）。
        滚动走 reflow_visible 快速路径只重绘几何变化的组件；dock 固定头几何
        不变 → 每次滚动都显式重绘固定头所在行，避免内容在它下方滚动时残留/重叠。
        """
        super().watch_scroll_y(old_value, new_value)
        self._pinned = new_value >= self.max_scroll_y - 0.5
        self._update_sticky_pin()
        if self._pinned_msg is not None:
            # 钉住期间每次滚动整体重绘消息区（同 ScrollView 模式）：固定头几何不变，
            # 快速路径不会重绘它所在行，树滚动穿越边界时局部刷新会残留/重叠/错位
            self.refresh()

    def _scroll_end(self) -> None:
        """跟随输出滚动到底部 — 仅在吸附状态执行。

        用户上翻（解除吸附）后不强制滚动，否则流式渲染时每次 _scroll_end
        都把视图拽回底部（"鬼畜"）。
        """
        if self._pinned:
            self.scroll_end(animate=False)


# ── 多模态 content 解析 ─────────────────────────────────────────────────

def _parse_tool_args(raw_args) -> dict:
    """解析工具调用参数（JSON 字符串或已解析 dict），失败返回空 dict。"""
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        try:
            return json.loads(raw_args)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


_TOOL_ERROR_PREFIXES = ("错误：", "错误:", "工具执行异常：", "⚠️ 高风险操作已被阻止")


def _is_tool_error(content: str) -> bool:
    """识别工具结果中的错误内容（fc_loop 错误喂回时的前缀）。"""
    return content.startswith(_TOOL_ERROR_PREFIXES)


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
