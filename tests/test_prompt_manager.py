"""测试 prompt_manager — capture engine, entry manager, topic tracker。"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock

from core.storage import JsonStore
from core.memory.entries import EntryManager
from core.memory.tracker import TopicFrequencyTracker
from core.memory.capture import CaptureEngine


class TestEntryManager:
    """测试条目 CRUD。"""

    @pytest.fixture
    async def store(self):
        s = JsonStore()
        await s.start()
        yield s
        await s.close()

    @pytest.fixture
    def data_dir(self, tmp_path):
        d = tmp_path / "data"
        d.mkdir()
        for f in ["preferences.json", "workflows.json", "long_term_memory.json"]:
            (d / f).write_text("[]", encoding="utf-8")
        return d

    async def test_add_entry(self, store, data_dir):
        with patch('core.memory.entries.DATA_DIR', data_dir):
            mgr = EntryManager(store)
            entry = await mgr.add("preferences", "用户偏好简洁回复")
            assert entry["content"] == "用户偏好简洁回复"
            assert entry["status"] == "pending"
            assert "created_at" in entry

            loaded = await mgr.load("preferences")
            assert len(loaded) == 1

    async def test_update_entry(self, store, data_dir):
        with patch('core.memory.entries.DATA_DIR', data_dir):
            mgr = EntryManager(store)
            await mgr.add("preferences", "v1")
            await mgr.update("preferences", 0, content="v2", status="integrated")

            loaded = await mgr.load("preferences")
            assert loaded[0]["content"] == "v2"
            assert loaded[0]["status"] == "integrated"

    async def test_update_invalid_index(self, store, data_dir):
        with patch('core.memory.entries.DATA_DIR', data_dir):
            mgr = EntryManager(store)
            result = await mgr.update("preferences", 99, content="x")
            assert result is None

    async def test_get_pending(self, store, data_dir):
        with patch('core.memory.entries.DATA_DIR', data_dir):
            mgr = EntryManager(store)
            await mgr.add("preferences", "p1")
            await mgr.add("preferences", "p2")
            await mgr.update("preferences", 0, status="integrated")

            pending = await mgr.get_pending("preferences")
            assert len(pending) == 1
            assert pending[0]["content"] == "p2"

    async def test_mark_status(self, store, data_dir):
        with patch('core.memory.entries.DATA_DIR', data_dir):
            mgr = EntryManager(store)
            await mgr.add("workflows", "w1")
            assert await mgr.mark_status("workflows", 0, "orphaned") is True
            loaded = await mgr.load("workflows")
            assert loaded[0]["status"] == "orphaned"

    async def test_invalid_entry_type(self, store, data_dir):
        with patch('core.memory.entries.DATA_DIR', data_dir):
            mgr = EntryManager(store)
            with pytest.raises(ValueError, match="未知条目类型"):
                await mgr.load("invalid")

    async def test_multiple_types_independent(self, store, data_dir):
        with patch('core.memory.entries.DATA_DIR', data_dir):
            mgr = EntryManager(store)
            await mgr.add("preferences", "pref1")
            await mgr.add("workflows", "wf1")
            assert len(await mgr.load("preferences")) == 1
            assert len(await mgr.load("workflows")) == 1
            assert len(await mgr.load("long_term_memory")) == 0


class TestTopicFrequencyTracker:
    """测试主题频率追踪。"""

    @pytest.fixture
    async def store(self):
        s = JsonStore()
        await s.start()
        yield s
        await s.close()

    async def test_record_first_time(self, store, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        tf = data_dir / "topic_frequency.json"
        tf.write_text("{}", encoding="utf-8")

        with patch('core.memory.tracker.TOPIC_FILE', tf):
            tracker = TopicFrequencyTracker(store)
            await tracker.record("简洁回复", "sess1")

            data = json.loads(tf.read_text(encoding="utf-8"))
            assert "简洁回复" in data
            assert data["简洁回复"]["session_count"] == 1

    async def test_record_same_session_no_duplicate(self, store, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        tf = data_dir / "topic_frequency.json"
        tf.write_text("{}", encoding="utf-8")

        with patch('core.memory.tracker.TOPIC_FILE', tf):
            tracker = TopicFrequencyTracker(store)
            await tracker.record("key", "sess1")
            await tracker.record("key", "sess1")
            await tracker.record("key", "sess1")

            data = json.loads(tf.read_text(encoding="utf-8"))
            assert data["key"]["session_count"] == 1  # 同会话不重复计数

    async def test_record_different_sessions(self, store, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        tf = data_dir / "topic_frequency.json"
        tf.write_text("{}", encoding="utf-8")

        with patch('core.memory.tracker.TOPIC_FILE', tf):
            tracker = TopicFrequencyTracker(store)
            for sid in ["s1", "s2", "s3"]:
                await tracker.record("key", sid)

            data = json.loads(tf.read_text(encoding="utf-8"))
            assert data["key"]["session_count"] == 3

    async def test_should_capture_not_enough_sessions(self, store, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        tf = data_dir / "topic_frequency.json"
        # 只有 2 次，不满足 ≥3
        tf.write_text(json.dumps({
            "key": {"session_count": 2, "first_seen": "2026-01-01T00:00:00",
                     "last_seen": "2026-06-01T00:00:00", "sessions": ["s1", "s2"]}
        }), encoding="utf-8")

        with patch('core.memory.tracker.TOPIC_FILE', tf):
            tracker = TopicFrequencyTracker(store)
            assert await tracker.should_capture("key") is False

    async def test_should_capture_not_enough_time(self, store, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        tf = data_dir / "topic_frequency.json"
        # 3 次但时间跨度 <7 天
        tf.write_text(json.dumps({
            "key": {"session_count": 3, "first_seen": "2026-06-28T00:00:00",
                     "last_seen": "2026-06-30T00:00:00", "sessions": ["s1", "s2", "s3"]}
        }), encoding="utf-8")

        with patch('core.memory.tracker.TOPIC_FILE', tf):
            tracker = TopicFrequencyTracker(store)
            assert await tracker.should_capture("key") is False

    async def test_should_capture_met(self, store, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        tf = data_dir / "topic_frequency.json"
        tf.write_text(json.dumps({
            "key": {"session_count": 3, "first_seen": "2026-01-01T00:00:00",
                     "last_seen": "2026-06-01T00:00:00", "sessions": ["s1", "s2", "s3"]}
        }), encoding="utf-8")

        with patch('core.memory.tracker.TOPIC_FILE', tf):
            tracker = TopicFrequencyTracker(store)
            assert await tracker.should_capture("key") is True

    async def test_should_capture_unknown_key(self, store, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        tf = data_dir / "topic_frequency.json"
        tf.write_text("{}", encoding="utf-8")

        with patch('core.memory.tracker.TOPIC_FILE', tf):
            tracker = TopicFrequencyTracker(store)
            assert await tracker.should_capture("unknown") is False


class TestCaptureEngine:
    """测试条目截获引擎。"""

    @pytest.fixture
    async def store(self):
        s = JsonStore()
        await s.start()
        yield s
        await s.close()

    @pytest.fixture
    def mock_entries(self, store, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        for f in ["preferences.json", "workflows.json", "long_term_memory.json"]:
            (data_dir / f).write_text("[]", encoding="utf-8")
        with patch('core.memory.entries.DATA_DIR', data_dir):
            from core.memory.entries import EntryManager
            yield EntryManager(store)

    @pytest.fixture
    def mock_tracker(self, store, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        tf = data_dir / "topic_frequency.json"
        tf.write_text("{}", encoding="utf-8")
        with patch('core.memory.tracker.TOPIC_FILE', tf):
            yield TopicFrequencyTracker(store)

    async def test_capture_preference(self, mock_entries, mock_tracker):
        engine = CaptureEngine(mock_entries, mock_tracker)
        captured = await engine.capture(
            "我喜欢简洁的回复风格", "明白",
            session_id="test", turn=1,
        )
        assert len(captured) >= 1
        assert any("简洁" in c["content"] for c in captured)

    async def test_capture_workflow(self, mock_entries, mock_tracker):
        engine = CaptureEngine(mock_entries, mock_tracker)
        captured = await engine.capture(
            "不对，你应该先读文件再回答", "好的，我会修正",
            session_id="test", turn=1,
        )
        assert len(captured) >= 1
        assert any("不对" in c["content"] for c in captured)

    async def test_dedup_similar(self, mock_entries, mock_tracker):
        """相似内容应去重更新而非新增。"""
        engine = CaptureEngine(mock_entries, mock_tracker)

        await engine.capture(
            "我喜欢简洁的回复风格", "明白",
            session_id="test_session", turn=1,
        )
        all_entries = await mock_entries.load("preferences")
        assert len(all_entries) == 1

        # 高度相似内容（仅多了"非常"）
        await engine.capture(
            "我喜欢非常简洁的回复风格", "明白",
            session_id="test_session", turn=2,
        )
        all_entries = await mock_entries.load("preferences")
        assert len(all_entries) == 1  # 去重成功，不新增
        assert "非常简洁" in all_entries[0]["content"]  # 内容已更新

    async def test_no_capture_on_short_text(self, mock_entries, mock_tracker):
        """太短的文本不应截获。"""
        engine = CaptureEngine(mock_entries, mock_tracker)
        captured = await engine.capture(
            "我喜欢", "嗯",
            session_id="test", turn=1,
        )
        assert len(captured) == 0

    async def test_no_capture_on_normal_chat(self, mock_entries, mock_tracker):
        """普通对话不应触发截获。"""
        engine = CaptureEngine(mock_entries, mock_tracker)
        captured = await engine.capture(
            "今天天气怎么样", "今天天气不错",
            session_id="test", turn=1,
        )
        assert len(captured) == 0
