"""SessionContext — 当前会话的运行时状态。

P4 Batch 2: 封装散落在 AideApp 中的 10+ 个会话相关属性。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SessionContext:
    """当前会话的运行时状态。

    由 AideApp 持有，chat_worker / command handlers 通过它访问会话状态。
    """
    is_ensured: bool = False
    session_dir: Path | None = None
    name: str = ""
    turn: int = 0
    conversation: list[dict] = field(default_factory=list)
    last_user_text: str = ""
    last_ai_text: str = ""
    is_maintenance: bool = False
    pending_clear: bool = False
    pending_rollback: bool = False
    pending_rollback_turn: int = 0

    def reset(self) -> None:
        """重置为初始状态（删除会话后调用）。"""
        self.is_ensured = False
        self.session_dir = None
        self.name = ""
        self.turn = 0
        self.conversation.clear()
        self.last_user_text = ""
        self.last_ai_text = ""
        self.is_maintenance = False
        self.pending_clear = False
        self.pending_rollback = False
        self.pending_rollback_turn = 0
