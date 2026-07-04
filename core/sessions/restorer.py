"""会话恢复 — 从磁盘读取 turn_NNN.json 重建 conversation 列表。

支持新旧两种格式：增量消息列表（新）和完整快照（旧）。
"""

from __future__ import annotations

import json
from pathlib import Path


def _msg_to_entry(msg: dict) -> dict | None:
    """将原始消息字典转为干净的 conversation entry。

    保留 tool_call_id、tool_calls、name、_image_paths 字段
    （DeepSeek 等 API 严格要求 tool_call_id 存在）。
    """
    role = msg.get("role", "")
    content = msg.get("content", "")
    if role not in ("user", "assistant", "tool") or not content:
        return None

    entry: dict = {"role": role, "content": content}
    for key in ("tool_call_id", "tool_calls", "name", "_image_paths"):
        if key in msg:
            entry[key] = msg[key]
    return entry


def restore_session(sessions_root: Path, session_id: str) -> tuple[list[dict], int]:
    """从磁盘恢复会话对话历史。

    Args:
        sessions_root: sessions/ 根目录路径
        session_id: 会话 ID（目录名，如 "20260704_120000"）

    Returns:
        (conversation, turn_count) — conversation 为空列表且 turn 为 0 表示恢复失败
    """
    session_dir = sessions_root / session_id
    if not session_dir.exists():
        return [], 0

    messages_dir = session_dir / "messages"
    if not messages_dir.exists():
        return [], 0

    turn_files = sorted(messages_dir.glob("turn_*.json"))
    if not turn_files:
        return [], 0

    turn_count = len(turn_files)
    conversation: list[dict] = []

    for tf in turn_files:
        try:
            data = json.loads(tf.read_text(encoding="utf-8"))
            msgs = data.get("messages")
            if msgs:
                # 新格式：增量消息列表
                for msg in msgs:
                    entry = _msg_to_entry(msg)
                    if entry is not None:
                        conversation.append(entry)
            else:
                # 回退：旧格式 — conversation 完整快照或 user/assistant 字段
                conv = data.get("conversation", [])
                if conv:
                    for msg in conv:
                        entry = _msg_to_entry(msg)
                        if entry is not None:
                            conversation.append(entry)
                else:
                    user = data.get("user", "")
                    if user:
                        conversation.append({"role": "user", "content": user})
                    assistant = data.get("assistant", "")
                    if assistant:
                        conversation.append({"role": "assistant", "content": assistant})
        except (json.JSONDecodeError, OSError):
            continue

    return conversation, turn_count
