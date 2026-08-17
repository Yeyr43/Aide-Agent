"""用户消息钉顶（几何 sticky）— 从 message_list.py 提取的自包含状态机。

MessageList 继承 StickyPinMixin 获得钉顶能力。mixin 只依赖宿主提供的
数据（_user_msgs / _msg_trees，按文档顺序对齐）与 Textual 滚动容器几何
（scroll_y / max_scroll_y / size / virtual_region / mount / refresh）。

机制：钉住 = 流内消息 display:none（占位取消）+ dock:top 固定头显示消息副本。
display:none 移除的占位 == 固定头 dock 间距（等高 + 同 margin），两者抵消 →
树的位置与 max_scroll_y 不变，无需滚动补偿，树自由滚动。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.events import Resize

from .tree_nodes import TurnTree

if TYPE_CHECKING:
    from .message_list import MessageWidget

# 供 type hint 使用；运行时构造 header 在方法内惰性导入（避免与 message_list 循环导入）
_TurnTree = TurnTree


class StickyPinMixin:
    """用户消息钉顶（几何 sticky）mixin — 锚点语义见各方法 docstring。

    锚点判定用 virtual_region；被钉消息 display:none 后其坐标异常，勿在读它判定。
    """

    def _init_pin_state(self) -> None:
        """初始化钉顶状态（宿主 __init__ 末尾调用）。"""
        self._user_msgs: list[MessageWidget] = []
        self._msg_trees: list[_TurnTree | None] = []
        self._pinned_msg: MessageWidget | None = None  # 当前钉顶的用户消息（视觉固定头）
        self._pinned_msg_top: float = 0.0  # 钉住时消息的自然顶部（scroll 内容坐标）
        self._pinned_msg_height: float = 0.0  # 钉住时消息的占位高度（含 margin，释放判定用）
        self._pinned_tree: _TurnTree | None = None  # 钉住消息的回合树
        self._pinned_orig_display = ""  # 钉住前消息的 display 值（释放时恢复）
        self._sticky_header: MessageWidget | None = None  # dock:top 固定头（钉顶时显示消息副本）
        self._pin_disabled = False  # restore/clear 期间暂停钉顶判定

    def on_mount(self) -> None:
        """创建钉顶固定头（dock:top，初始隐藏，不参与流式布局）。"""
        from .message_list import MessageWidget
        header = MessageWidget("")
        header.add_class("sticky-header")
        header.styles.display = "none"
        header.styles.dock = "top"
        self._sticky_header = header
        self.mount(header)

    # ── 钉顶判定 ─────────────────────────────────────────────────────

    def _update_sticky_pin(self) -> None:
        """按当前 scroll_y 判定钉顶状态 — 幂等：仅状态变化时改 DOM。"""
        if self._pin_disabled or self._sticky_header is None:
            return
        if self._pinned_msg is not None:
            if self._pinned_should_release():
                self._release_sticky()
            else:
                # 锚点跟随（仅向前）：只切到比当前更新的消息；向后（回旧消息）
                # 只由 release 触发。滚动在消息顶边界小幅抖动（滚轮/触控板反馈）
                # 时，旧消息的 release 被死区抑制 → 不会出现"释放→切回→再释放"
                # 的来回振荡（防鬼畜）。
                try:
                    start = self._user_msgs.index(self._pinned_msg) + 1
                except ValueError:
                    start = len(self._user_msgs)
                new_target = self._active_sticky_target(start_index=start)
                if new_target is not None:
                    self._release_sticky()
                    self._engage_sticky(*new_target)
                return
        target = self._active_sticky_target()
        if target is not None:
            msg, tree, msg_top = target
            self._engage_sticky(msg, tree, msg_top)

    def _active_sticky_target(self, start_index: int = 0) -> tuple[MessageWidget, _TurnTree, float] | None:
        """找到当前应钉住的消息：(msg, tree, msg_top)。

        锚点语义：上方最近的消息顶已滑出窗口顶（msg_top < scroll_y），且消息
        不足一屏 → 钉住该消息作为上下文锚点（固定头显示消息副本，用户始终
        知道当前滚动位置属于哪条消息）。

        不依赖树的几何——多回合短树滚动到"树间隙"（上方树已整体滑过）时，
        原逻辑因要求"树尾仍可见"而永远不钉，长对话滚动时顶部失去消息锚点。

        start_index > 0 时只考虑该下标起（更）新的消息——钉住状态下锚点只向前
        跟随，向后（回旧消息）由 _pinned_should_release 负责（防边界抖动鬼畜）。
        """
        sy = self.scroll_y
        h = self.size.height
        if h <= 0:
            return None
        candidate = None
        for i in range(start_index, len(self._user_msgs)):
            msg = self._user_msgs[i]
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

    @staticmethod
    def _sticky_deadband(height: float) -> float:
        """钉顶死区（px）：消息高的一半、下限 10 上限 60。

        滚动在消息顶边界小幅抖动（滚轮/触控板反馈）时，不释放/切换锚点，
        避免钉住的输入消息框在相邻消息间来回切换（鬼畜）。
        """
        return max(10.0, min(height * 0.5, 60.0))

    def _pinned_should_release(self) -> bool:
        """当前钉住是否应解除：消息顶回到视口内一段距离（死区），或消息 ≥ 一屏。

        死区抑制边界振荡：钉住时 msg_top < sy（已滑出），若滚动只把 msg_top 带回
        视口边缘（sy ≈ msg_top）就立即释放，紧接着锚点会切回旧消息又释放，形成
        鬼畜。深层消息要求 msg_top 回到视口内 ≥ 死区才释放。

        近顶消息特例：首条消息 msg_top 只比 0 大几 px，其自然释放点 sy ≈ msg_top
        落在死区窗口内，永远到不了 sy + 死区 → 改用紧条件 msg_top >= sy（否则
        scroll_home 永不释放）。
        """
        sy = self.scroll_y
        h = self.size.height
        db = self._sticky_deadband(self._pinned_msg_height)
        if self._pinned_msg_top <= db:
            # 消息顶距内容顶部 < 死区（首条/靠顶消息）→ 顶回到视口即释放
            if self._pinned_msg_top >= sy:
                return True
        elif self._pinned_msg_top >= sy + db:
            return True  # 消息顶回到视口内一段距离 → 正常显示
        if self._pinned_msg_height >= h:
            return True  # 窗口缩小后消息 ≥ 一屏 → 钉住会盖住回复，释放
        return False

    def _engage_sticky(self, msg: MessageWidget, tree: _TurnTree, msg_top: float) -> None:
        """钉住 msg：流内隐藏 + 固定头显示消息副本。"""
        from .message_list import MessageWidget  # noqa: F401
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
