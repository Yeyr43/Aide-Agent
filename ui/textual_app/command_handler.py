"""命令处理 + 确认流 — 从 AideApp 提取，减少 app.py 体量。

CommandHandler 持有 app 引用以访问 kernel/session/ingester/widgets。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.commands.builtin.handlers import _rebuild_conversation_from_disk
from core.locale import t

if TYPE_CHECKING:
    from .app import AideApp
    from .widgets.message_list import MessageList
    from .widgets.input_box import InputBox


class CommandHandler:
    """命令执行 + 确认流处理器。

    从 AideApp 中提取，保持 app.py 精简。
    """

    def __init__(self, app: AideApp) -> None:
        self._app = app

    # ── 确认流判断 ─────────────────────────────────────────────────

    async def handle_confirmation(self, text: str, msg_list: MessageList) -> bool:
        """处理 /rollback 和 /clear 的确认流。

        Returns:
            True 如果输入已被确认流消费（不应再走正常对话流程）
        """
        session = self._app._session

        if session.pending_rollback:
            session.pending_rollback = False
            target = session.pending_rollback_turn
            session.pending_rollback_turn = 0
            if text.strip().lower() in ("确认", "yes", "y"):
                self._do_rollback(target, msg_list)
            else:
                msg_list.add_command_result(t("ui.cmd_handler.rollback_cancelled"))
            return True

        if session.pending_clear:
            session.pending_clear = False
            if text.strip().lower() in ("确认", "yes", "y"):
                await self._do_clear_session(msg_list)
            else:
                msg_list.add_command_result(t("ui.cmd_handler.clear_cancelled"))
            return True

        return False

    # ── 命令路由 ───────────────────────────────────────────────────

    async def run_command(self, cmd_def, args: str, msg_list: MessageList,
                          input_box: InputBox, text: str) -> None:
        """执行命令并显示结果。根据 CommandDefinition.kind 选择执行模式。"""
        msg_list.add_user_message(text)

        if cmd_def.kind == "maintenance":
            self._start_maintenance(input_box, cmd_def)
            return

        if cmd_def.kind == "confirm":
            self._app._session.pending_clear = True
            name = self._app._session.name
            if name:
                msg_list.add_command_result(
                    t("ui.cmd_handler.confirm_delete_named", name=name)
                )
            else:
                msg_list.add_command_result(
                    t("ui.cmd_handler.confirm_delete")
                )
            return

        try:
            result = await cmd_def.handler(self._app, args)
            self._app._session.last_ai_text = result
            msg_list.add_command_result(result)
        except Exception as e:
            msg_list.add_error(t("ui.cmd_handler.cmd_failed", e=e))

    # ── 维护模式 ───────────────────────────────────────────────────

    def _start_maintenance(self, input_box: InputBox, cmd_def) -> None:
        """进入维护模式（/compact 或 /profile update）。"""
        label = cmd_def.name.lstrip("/")
        input_box.disabled = True
        input_box.placeholder = f"*{label}...*"
        input_box.add_class("maintenance")
        self._app._session.is_maintenance = True
        if cmd_def.name == "/compact":
            self._app.compress_worker()
        else:
            self._app.profile_update_worker()

    def exit_maintenance(self) -> None:
        """退出维护模式，恢复输入框。"""
        self._app._session.is_maintenance = False
        input_box = self._app.query_one("#input", InputBox)
        input_box.disabled = False
        input_box._placeholder = t("ui.widget.input_placeholder")
        input_box.remove_class("maintenance")
        input_box.focus()

    # ── Rollback / Clear ───────────────────────────────────────────

    def _do_rollback(self, target_turn: int, msg_list: MessageList) -> None:
        """执行会话回滚：文件系统操作 + 内存重建 + UI 重渲染。"""
        session_dir = self._app._ingester._session_dir
        if session_dir is None:
            msg_list.add_command_result(t("ui.cmd_handler.session_missing"))
            return

        current_turn = self._app._session.turn

        try:
            self._app._kernel.rollback_session(session_dir, target_turn)
        except ValueError as e:
            msg_list.add_error(t("ui.cmd_handler.rollback_failed", e=e))
            return

        _rebuild_conversation_from_disk(self._app, session_dir, target_turn)

        msg_list.clear()
        msg_list.restore_conversation(self._app._session.conversation)

        deleted = current_turn - target_turn
        msg_list.add_command_result(
            t("ui.cmd_handler.rollback_done", target=target_turn, deleted=deleted)
        )

    async def _do_clear_session(self, msg_list: MessageList) -> None:
        """删除当前会话并回到首页。"""
        session_id = None
        ingester = self._app._ingester
        if hasattr(ingester, '_session_dir') and ingester._session_dir:
            session_id = ingester._session_dir.name

        if session_id:
            await self._app._kernel.delete_session(session_id)

        ingester._session_dir = None
        ingester._session_id = None
        self._app._session.reset()

        msg_list.add_command_result(t("ui.cmd_handler.session_deleted"))
        self._app.action_go_home()
