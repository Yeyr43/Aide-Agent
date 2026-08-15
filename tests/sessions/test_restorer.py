"""Tests for core.sessions.restorer — session conversation restoration from disk."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from core.sessions.restorer import (
    restore_session, restore_turns, restore_session_full,
    _msg_to_entry, _extract_messages,
)


class TestMsgToEntry:
    """Test _msg_to_entry — raw message dict → clean conversation entry."""

    def test_user_message(self):
        result = _msg_to_entry({"role": "user", "content": "hello"})
        assert result == {"role": "user", "content": "hello"}

    def test_assistant_message(self):
        result = _msg_to_entry({"role": "assistant", "content": "hi there"})
        assert result == {"role": "assistant", "content": "hi there"}

    def test_tool_message(self):
        result = _msg_to_entry({
            "role": "tool",
            "content": "result",
            "tool_call_id": "call_abc",
        })
        assert result["role"] == "tool"
        assert result["content"] == "result"
        assert result["tool_call_id"] == "call_abc"

    def test_system_message_is_filtered(self):
        result = _msg_to_entry({"role": "system", "content": "system prompt"})
        assert result is None

    def test_empty_content_is_filtered(self):
        result = _msg_to_entry({"role": "user", "content": ""})
        assert result is None

    def test_unknown_role_is_filtered(self):
        result = _msg_to_entry({"role": "unknown", "content": "something"})
        assert result is None

    def test_preserves_tool_calls_field(self):
        """Tool_calls preserved when assistant has tool_calls with content."""
        msg = {
            "role": "assistant",
            "content": "I will call a tool",
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": "echo", "arguments": "{}"}}],
        }
        result = _msg_to_entry(msg)
        assert result is not None
        assert "tool_calls" in result

    def test_assistant_tool_calls_without_content_preserved(self):
        """Assistant 带 tool_calls 但空 content 必须保留——否则其后的 tool 消息失配。

        DeepSeek 严格校验：tool 消息必须紧跟带 tool_calls 的 assistant 消息。
        """
        result = _msg_to_entry({
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": "echo", "arguments": "{}"}}],
        })
        assert result is not None
        assert result["role"] == "assistant"
        assert result["content"] == ""
        assert "tool_calls" in result

    def test_preserves_name_field(self):
        result = _msg_to_entry({"role": "user", "content": "hi", "name": "alice"})
        assert result is not None
        assert result["name"] == "alice"

    def test_preserves_image_paths(self):
        result = _msg_to_entry({
            "role": "user", "content": "look",
            "_image_paths": ["/tmp/img.png"],
        })
        assert result is not None
        assert "_image_paths" in result
        assert result["_image_paths"] == ["/tmp/img.png"]

    def test_missing_role_returns_none(self):
        result = _msg_to_entry({"content": "no role"})
        assert result is None


class TestExtractMessages:
    """Test _extract_messages — normalize turn file data to message list."""

    def test_new_format_messages(self):
        data = {"messages": [{"role": "user", "content": "q"}]}
        assert _extract_messages(data) == [{"role": "user", "content": "q"}]

    def test_old_format_conversation(self):
        data = {"conversation": [{"role": "user", "content": "q"}]}
        assert _extract_messages(data) == [{"role": "user", "content": "q"}]

    def test_legacy_user_assistant(self):
        data = {"user": "q", "assistant": "a"}
        assert _extract_messages(data) == [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]

    def test_empty_returns_empty(self):
        assert _extract_messages({}) == []


class TestRestoreTurns:
    """Test restore_turns — per-turn structured records for UI tree rebuild."""

    def _write_turn(self, session_dir, num, **data):
        msgs_dir = session_dir / "messages"
        msgs_dir.mkdir(parents=True, exist_ok=True)
        payload = {"turn": num}
        payload.update(data)
        (msgs_dir / f"turn_{num:03d}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_returns_thinking_and_raw_messages(self, tmp_path):
        """每轮记录应包含 thinking + 完整消息（含带 tool_calls 的 assistant）。"""
        session_dir = tmp_path / "s1"
        self._write_turn(session_dir, 1,
            thinking="逐步推理",
            messages=[
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "read_file", "arguments": '{"path":"a"}'}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "result"},
                {"role": "assistant", "content": "done"},
            ])

        turns = restore_turns(tmp_path, "s1")
        assert len(turns) == 1
        assert turns[0]["turn"] == 1
        assert turns[0]["thinking"] == "逐步推理"
        roles = [m["role"] for m in turns[0]["messages"]]
        assert roles == ["user", "assistant", "tool", "assistant"]
        # 带 tool_calls 的 assistant 消息原样保留
        assert turns[0]["messages"][1].get("tool_calls")

    def test_max_turn_filters(self, tmp_path):
        session_dir = tmp_path / "s2"
        self._write_turn(session_dir, 1, messages=[{"role": "user", "content": "q1"}])
        self._write_turn(session_dir, 2, messages=[{"role": "user", "content": "q2"}])

        turns = restore_turns(tmp_path, "s2", max_turn=1)
        assert [t["turn"] for t in turns] == [1]

    def test_corrupt_file_skipped(self, tmp_path):
        session_dir = tmp_path / "s3"
        (session_dir / "messages").mkdir(parents=True)
        (session_dir / "messages" / "turn_001.json").write_text("bad{{{")
        self._write_turn(session_dir, 2, messages=[{"role": "user", "content": "ok"}])

        turns = restore_turns(tmp_path, "s3")
        assert len(turns) == 1
        assert turns[0]["turn"] == 2

    def test_missing_dir_returns_empty(self, tmp_path):
        assert restore_turns(tmp_path, "nope") == []


class TestRestoreSession:
    """Test restore_session — read turn files from disk and rebuild conversation."""

    # ── Failure cases (no disk IO needed) ──────────────────────────────

    def test_session_dir_missing(self, tmp_path):
        conv, turns = restore_session(tmp_path, "nonexistent")
        assert conv == []
        assert turns == 0

    def test_messages_dir_missing(self, tmp_path):
        session_dir = tmp_path / "20260704_120000"
        session_dir.mkdir()
        # no messages/ subdir
        conv, turns = restore_session(tmp_path, "20260704_120000")
        assert conv == []
        assert turns == 0

    def test_empty_messages_dir(self, tmp_path):
        session_dir = tmp_path / "20260704_120000"
        (session_dir / "messages").mkdir(parents=True)
        conv, turns = restore_session(tmp_path, "20260704_120000")
        assert conv == []
        assert turns == 0

    # ── New format: incremental messages list ──────────────────────────

    def test_new_format_single_turn(self, tmp_path):
        session_dir = tmp_path / "s1"
        msgs_dir = session_dir / "messages"
        msgs_dir.mkdir(parents=True)

        turn_data = {
            "turn": 1,
            "user": "hello",
            "assistant": "hi there",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ],
        }
        (msgs_dir / "turn_001.json").write_text(json.dumps(turn_data))

        conv, turns = restore_session(tmp_path, "s1")
        assert turns == 1
        assert len(conv) == 2
        assert conv[0] == {"role": "user", "content": "hello"}
        assert conv[1] == {"role": "assistant", "content": "hi there"}

    def test_new_format_multi_turn(self, tmp_path):
        session_dir = tmp_path / "s2"
        msgs_dir = session_dir / "messages"
        msgs_dir.mkdir(parents=True)

        (msgs_dir / "turn_001.json").write_text(json.dumps({
            "turn": 1,
            "messages": [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
            ],
        }))
        (msgs_dir / "turn_002.json").write_text(json.dumps({
            "turn": 2,
            "messages": [
                {"role": "user", "content": "q2"},
                {"role": "assistant", "content": "a2"},
            ],
        }))

        conv, turns = restore_session(tmp_path, "s2")
        assert turns == 2
        assert len(conv) == 4
        assert conv[0]["content"] == "q1"

    # ── Old format: conversation snapshot ──────────────────────────────

    def test_old_format_conversation_field(self, tmp_path):
        session_dir = tmp_path / "s3"
        msgs_dir = session_dir / "messages"
        msgs_dir.mkdir(parents=True)

        (msgs_dir / "turn_001.json").write_text(json.dumps({
            "turn": 1,
            "conversation": [
                {"role": "user", "content": "old q"},
                {"role": "assistant", "content": "old a"},
            ],
        }))

        conv, turns = restore_session(tmp_path, "s3")
        assert turns == 1
        assert len(conv) == 2
        assert conv[0]["content"] == "old q"

    # ── Legacy format: user/assistant fields ───────────────────────────

    def test_legacy_format_user_assistant(self, tmp_path):
        session_dir = tmp_path / "s4"
        msgs_dir = session_dir / "messages"
        msgs_dir.mkdir(parents=True)

        (msgs_dir / "turn_001.json").write_text(json.dumps({
            "turn": 1,
            "user": "legacy q",
            "assistant": "legacy a",
        }))

        conv, turns = restore_session(tmp_path, "s4")
        assert turns == 1
        assert len(conv) == 2
        assert conv[0] == {"role": "user", "content": "legacy q"}
        assert conv[1] == {"role": "assistant", "content": "legacy a"}

    def test_legacy_format_user_only(self, tmp_path):
        session_dir = tmp_path / "s5"
        msgs_dir = session_dir / "messages"
        msgs_dir.mkdir(parents=True)

        (msgs_dir / "turn_001.json").write_text(json.dumps({
            "turn": 1,
            "user": "user only",
        }))

        conv, turns = restore_session(tmp_path, "s5")
        assert len(conv) == 1
        assert conv[0]["role"] == "user"

    # ── Corrupt files are skipped ─────────────────────────────────────

    def test_corrupt_json_is_skipped(self, tmp_path):
        session_dir = tmp_path / "s6"
        msgs_dir = session_dir / "messages"
        msgs_dir.mkdir(parents=True)

        (msgs_dir / "turn_001.json").write_text("not valid json{{{")
        (msgs_dir / "turn_002.json").write_text(json.dumps({
            "turn": 2,
            "messages": [{"role": "user", "content": "valid"}],
        }))

        conv, turns = restore_session(tmp_path, "s6")
        # Corrupt file skipped, only valid one counts
        assert turns == 2  # glob returns 2 files, but only 1 parsed
        assert len(conv) == 1
        assert conv[0]["content"] == "valid"

    def test_missing_file_permission_handled(self, tmp_path):
        """File read errors are tolerated (corrupt file skipped)."""
        session_dir = tmp_path / "s7"
        msgs_dir = session_dir / "messages"
        msgs_dir.mkdir(parents=True)

        # File that exists but contains invalid JSON
        (msgs_dir / "turn_001.json").write_text("{invalid json content")
        (msgs_dir / "turn_002.json").write_text(json.dumps({
            "turn": 2,
            "messages": [{"role": "user", "content": "surviving"}],
        }))

        conv, turns = restore_session(tmp_path, "s7")
        # Corrupt file skipped, only valid message survives
        assert len(conv) >= 1

    # ── Tool calls preserved (DeepSeek pairing) ───────────────────────

    def test_tool_call_structure_preserved(self, tmp_path):
        """恢复后 tool 消息必须仍有前置 assistant+tool_calls 配对。"""
        session_dir = tmp_path / "s9"
        msgs_dir = session_dir / "messages"
        msgs_dir.mkdir(parents=True)

        (msgs_dir / "turn_001.json").write_text(
            json.dumps({
                "turn": 1,
                "messages": [
                    {"role": "user", "content": "查一下"},
                    {"role": "assistant", "content": "", "tool_calls": [
                        {"id": "call_1", "type": "function",
                         "function": {"name": "search_in_files",
                                      "arguments": '{"query": "TODO"}'}}]},
                    {"role": "tool", "tool_call_id": "call_1",
                     "content": "core/agent.py: TODO"},
                    {"role": "assistant", "content": "找到了 TODO"},
                ],
            }, ensure_ascii=False), encoding="utf-8")

        conv, turns = restore_session(tmp_path, "s9")
        assert turns == 1
        roles = [m["role"] for m in conv]
        assert roles == ["user", "assistant", "tool", "assistant"]
        # tool 消息前必须是带 tool_calls 的 assistant 消息
        assert conv[1]["role"] == "assistant"
        assert conv[1].get("tool_calls")
        assert conv[2]["role"] == "tool"
        assert conv[2]["tool_call_id"] == "call_1"
        assert conv[2]["tool_call_id"] == conv[1]["tool_calls"][0]["id"]

    # ── Filters system messages ───────────────────────────────────────

    def test_system_messages_filtered_from_restore(self, tmp_path):
        session_dir = tmp_path / "s8"
        msgs_dir = session_dir / "messages"
        msgs_dir.mkdir(parents=True)

        (msgs_dir / "turn_001.json").write_text(json.dumps({
            "turn": 1,
            "messages": [
                {"role": "system", "content": "prompt"},
                {"role": "user", "content": "real msg"},
                {"role": "assistant", "content": "real reply"},
            ],
        }))

        conv, turns = restore_session(tmp_path, "s8")
        # System message is filtered out
        roles = [m["role"] for m in conv]
        assert "system" not in roles
        assert len(conv) == 2

    def test_full_returns_turns(self, tmp_path):
        """restore_session_full 一次读盘返回 (conversation, turn_count, turns)。

        app 层需要 conversation + 按轮结构化记录时用它，避免把同一批
        turn 文件 glob + read + json.loads 两遍。
        """
        session_dir = tmp_path / "s_full"
        msgs_dir = session_dir / "messages"
        msgs_dir.mkdir(parents=True)
        (msgs_dir / "turn_001.json").write_text(json.dumps({
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
        }))

        conv, turn_count, turns = restore_session_full(tmp_path, "s_full")
        # conversation 与旧 restore_session 完全一致（兼容）
        old_conv, old_turns = restore_session(tmp_path, "s_full")
        assert conv == old_conv
        assert turn_count == old_turns == 1
        # turns 是结构化记录（含 thinking，供 UI 重建树）
        assert len(turns) == 1
        assert turns[0]["turn"] == 1
        assert turns[0]["thinking"] == "先搜索"
        assert turns[0]["messages"][1]["tool_calls"][0]["id"] == "c1"

    def test_full_missing_dir(self, tmp_path):
        conv, turn_count, turns = restore_session_full(tmp_path, "nope")
        assert conv == []
        assert turn_count == 0
        assert turns == []
