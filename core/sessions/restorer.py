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
    注意：assistant 消息即使 content 为空，只要带 tool_calls 就必须保留
    —— 否则其后的 tool 消息会失去前置 assistant 配对，导致下一轮 LLM
    调用被拒绝（tool 消息必须紧跟带 tool_calls 的 assistant 消息）。
    """
    role = msg.get("role", "")
    content = msg.get("content", "")
    if role not in ("user", "assistant", "tool"):
        return None
    tool_calls = msg.get("tool_calls")
    if not content and not (role == "assistant" and tool_calls):
        return None

    entry: dict = {"role": role, "content": content}
    for key in ("tool_call_id", "tool_calls", "name", "_image_paths"):
        if key in msg:
            entry[key] = msg[key]
    return entry


def _extract_messages(data: dict) -> list[dict]:
    """从 turn 文件数据中提取消息列表，兼容三种格式。

    新格式：messages（增量消息列表）
    旧格式：conversation（完整快照）
    更旧格式：user / assistant 字符串字段
    """
    msgs = data.get("messages") or data.get("conversation") or []
    if msgs:
        return msgs
    user = data.get("user", "")
    assistant = data.get("assistant", "")
    out: list[dict] = []
    if user:
        out.append({"role": "user", "content": user})
    if assistant:
        out.append({"role": "assistant", "content": assistant})
    return out


def restore_turns(
    sessions_root: Path,
    session_id: str,
    max_turn: int | None = None,
) -> list[dict]:
    """从磁盘恢复每轮的结构化记录（供 UI 重建回合树）。

    相比 restore_session 的扁平 conversation，此函数保留每轮的边界、
    thinking 内容和原始消息（含 tool_calls / tool_call_id），
    以便前端还原 think / 工具 / 正文节点的完整树形细节。

    Args:
        sessions_root: sessions/ 根目录路径
        session_id: 会话 ID（目录名）
        max_turn: 若指定，只返回 <= max_turn 的轮次（回滚后使用）

    Returns:
        [{"turn": N, "thinking": str, "messages": [...]}, ...]
    """
    session_dir = sessions_root / session_id
    if not session_dir.exists():
        return []
    messages_dir = session_dir / "messages"
    if not messages_dir.exists():
        return []

    turns: list[dict] = []
    for tf in sorted(messages_dir.glob("turn_*.json")):
        try:
            turn_num = int(tf.stem.split("_", 1)[1])
        except (ValueError, IndexError):
            continue
        if max_turn is not None and turn_num > max_turn:
            continue
        try:
            data = json.loads(tf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        msgs = _extract_messages(data)
        if not msgs:
            continue
        turns.append({
            "turn": data.get("turn", turn_num),
            "thinking": data.get("thinking", "") or "",
            "messages": msgs,
        })
    return turns


def restore_session(sessions_root: Path, session_id: str) -> tuple[list[dict], int]:
    """从磁盘恢复会话对话历史。

    Args:
        sessions_root: sessions/ 根目录路径
        session_id: 会话 ID（目录名，如 "20260704_120000"）

    Returns:
        (conversation, turn_count) — conversation 为空列表且 turn 为 0 表示恢复失败
    """
    session_dir = sessions_root / session_id
    messages_dir = session_dir / "messages" if session_dir.exists() else None
    turn_files = sorted(messages_dir.glob("turn_*.json")) if messages_dir and messages_dir.exists() else []

    conversation: list[dict] = []
    for turn in restore_turns(sessions_root, session_id):
        for msg in turn["messages"]:
            entry = _msg_to_entry(msg)
            if entry is not None:
                conversation.append(entry)

    return conversation, len(turn_files)
