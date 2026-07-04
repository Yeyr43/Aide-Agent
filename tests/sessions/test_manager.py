from pathlib import Path
import json
import pytest
from core.sessions.manager import SessionManager, SessionInfo


def _make_turn_files(session_dir: Path, count: int) -> None:
    """Helper: 创建模拟的 turn 文件 + timeline.json + cache.json。"""
    messages_dir = session_dir / "messages"
    messages_dir.mkdir(parents=True, exist_ok=True)

    timeline = []
    cache = []
    for i in range(1, count + 1):
        turn_data = {
            "turn": i,
            "timestamp": f"2026-07-03T00:00:{i:02d}Z",
            "user": f"user msg {i}",
            "assistant": f"assistant reply {i}",
            "tool_calls": [],
            "conversation": [
                {"role": "user", "content": f"user msg {i}"},
                {"role": "assistant", "content": f"assistant reply {i}"},
            ],
        }
        (messages_dir / f"turn_{i:03d}.json").write_text(
            json.dumps(turn_data, ensure_ascii=False), encoding="utf-8",
        )
        timeline.append({"turn": i, "timestamp": f"2026-07-03T00:00:{i:02d}Z", "summary": f"turn {i}"})
        cache.append({"turn": i, "summary": f"turn {i}"})

    (session_dir / "timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (session_dir / "cache.json").write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8",
    )


class TestSessionManager:
    def test_create_session(self, tmp_path):
        mgr = SessionManager(tmp_path / "sessions")
        info = mgr.create("帮我写个Python脚本")
        assert info.name != "新对话"
        assert (tmp_path / "sessions" / info.id).is_dir()
        assert (tmp_path / "sessions" / info.id / "meta.json").exists()

    def test_list_all(self, tmp_path):
        mgr = SessionManager(tmp_path / "sessions")
        mgr.create("first")
        mgr.create("second")
        sessions = mgr.list_all()
        assert len(sessions) == 2

    def test_get(self, tmp_path):
        mgr = SessionManager(tmp_path / "sessions")
        info = mgr.create("test")
        found = mgr.get(info.id)
        assert found is not None
        assert found.name == info.name

    def test_delete(self, tmp_path):
        mgr = SessionManager(tmp_path / "sessions")
        info = mgr.create("to delete")
        assert mgr.delete(info.id) is True
        assert not (tmp_path / "sessions" / info.id).exists()

    def test_derive_title_truncates(self):
        title = SessionManager.derive_title("帮我写一个Python脚本来处理CSV文件中的数据")
        assert len(title) <= 21  # 20 + "…"

    def test_derive_title_removes_prefix(self):
        title = SessionManager.derive_title("请帮我写个爬虫")
        assert not title.startswith("请帮我")
        assert len(title) > 0


class TestRollback:
    """SessionManager.rollback() 测试。"""

    def test_rollback_deletes_turns_after_target(self, tmp_path):
        session_dir = tmp_path / "sessions" / "20260703_test"
        _make_turn_files(session_dir, 5)

        mgr = SessionManager(tmp_path / "sessions")
        result = mgr.rollback(session_dir, 3)

        assert result == 3
        # turn_001, turn_002, turn_003 应保留
        assert (session_dir / "messages" / "turn_001.json").exists()
        assert (session_dir / "messages" / "turn_003.json").exists()
        # turn_004, turn_005 应删除
        assert not (session_dir / "messages" / "turn_004.json").exists()
        assert not (session_dir / "messages" / "turn_005.json").exists()

    def test_rollback_truncates_timeline_and_cache(self, tmp_path):
        session_dir = tmp_path / "sessions" / "20260703_test"
        _make_turn_files(session_dir, 4)

        mgr = SessionManager(tmp_path / "sessions")
        mgr.rollback(session_dir, 2)

        timeline = json.loads((session_dir / "timeline.json").read_text(encoding="utf-8"))
        cache = json.loads((session_dir / "cache.json").read_text(encoding="utf-8"))
        assert len(timeline) == 2
        assert len(cache) == 2
        assert [e["turn"] for e in timeline] == [1, 2]

    def test_rollback_handles_overview_checkpoints(self, tmp_path):
        """回滚时从 overview.json 检查点还原 overview.md。"""
        session_dir = tmp_path / "sessions" / "20260703_test"
        _make_turn_files(session_dir, 3)

        # 创建检查点：第 2 轮压缩过
        (session_dir / "overview.json").write_text(
            json.dumps([
                {"to_turn": 2, "compressed_at": "t1", "overview_md": "## 会话概览\n- 早期话题"},
            ], ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (session_dir / "overview.md").write_text("## 会话概览\n- 最新话题", encoding="utf-8")

        mgr = SessionManager(tmp_path / "sessions")
        mgr.rollback(session_dir, 2)

        # overview.md 应为检查点中的版本
        restored = (session_dir / "overview.md").read_text(encoding="utf-8")
        assert "早期话题" in restored
        # overview.json 应截断到匹配检查点
        checkpoints = json.loads((session_dir / "overview.json").read_text(encoding="utf-8"))
        assert len(checkpoints) == 1
        assert checkpoints[0]["to_turn"] == 2

    def test_rollback_to_zero_raises(self, tmp_path):
        session_dir = tmp_path / "sessions" / "20260703_test"
        _make_turn_files(session_dir, 3)

        mgr = SessionManager(tmp_path / "sessions")
        with pytest.raises(ValueError, match="不能为负数"):
            mgr.rollback(session_dir, -1)

    def test_rollback_past_current_raises(self, tmp_path):
        session_dir = tmp_path / "sessions" / "20260703_test"
        _make_turn_files(session_dir, 3)

        mgr = SessionManager(tmp_path / "sessions")
        with pytest.raises(ValueError, match="无法回滚"):
            mgr.rollback(session_dir, 5)

    def test_rollback_to_last_turn_is_noop_boundary(self, tmp_path):
        """回滚到当前最后一轮（target = current - 1）是合法的。"""
        session_dir = tmp_path / "sessions" / "20260703_test"
        _make_turn_files(session_dir, 3)

        mgr = SessionManager(tmp_path / "sessions")
        result = mgr.rollback(session_dir, 2)

        assert result == 2
        assert (session_dir / "messages" / "turn_002.json").exists()
        assert not (session_dir / "messages" / "turn_003.json").exists()

    def test_rollback_nonexistent_dir_raises(self, tmp_path):
        session_dir = tmp_path / "sessions" / "nonexistent"

        mgr = SessionManager(tmp_path / "sessions")
        with pytest.raises(ValueError, match="不存在"):
            mgr.rollback(session_dir, 1)
