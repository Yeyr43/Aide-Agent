"""TopicFrequencyTracker — 关键词频率追踪。

维护 topic_frequency.json，用于长记忆触发判断：
  - session_count >= 3（不同会话）
  - last_seen - first_seen >= 7 天
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from core.storage import JsonStore
from core.setup import aide_dir

logger = logging.getLogger(__name__)

DATA_DIR = aide_dir() / "agent" / "data"
TOPIC_FILE = DATA_DIR / "topic_frequency.json"


class TopicFrequencyTracker:
    """追踪话题关键词的跨会话出现频率。

    用法:
        tracker = TopicFrequencyTracker(store)
        tracker = TopicFrequencyTracker(store, min_sessions=5, min_span_days=14)
        await tracker.record("简洁回复", session_id)
        if await tracker.should_capture("简洁回复"):
            # 触发长记忆截获
    """

    def __init__(self, store: JsonStore,
                 min_sessions: int = 3,
                 min_span_days: int = 7) -> None:
        self._store = store
        self.min_sessions = min_sessions
        self.min_span_days = min_span_days

    async def _load(self) -> dict:
        """加载 topic_frequency.json。"""
        data = await self._store.read(TOPIC_FILE)
        return data if data is not None else {}

    async def record(self, keyword: str, session_id: str) -> None:
        """记录一个关键词出现。

        Args:
            keyword: 关键词（小写，用于频率追踪）
            session_id: 当前会话 ID
        """
        data = await self._load()
        now = datetime.now(timezone.utc).isoformat()

        if keyword not in data:
            data[keyword] = {
                "session_count": 1,
                "first_seen": now,
                "last_seen": now,
                "sessions": [session_id],
            }
        else:
            entry = data[keyword]
            entry["last_seen"] = now
            if session_id not in entry["sessions"]:
                entry["sessions"].append(session_id)
                entry["session_count"] = len(entry["sessions"])

        await self._store.write(TOPIC_FILE, data)

    async def should_capture(self, keyword: str) -> bool:
        """判断是否满足长记忆触发条件。

        条件：≥ min_sessions 次不同会话 且 时间跨度 ≥ min_span_days 天。

        Returns:
            True 表示应该截获
        """
        data = await self._load()
        entry = data.get(keyword)
        if entry is None:
            return False

        session_count = entry.get("session_count", 0)
        if session_count < self.min_sessions:
            return False

        try:
            first = datetime.fromisoformat(entry["first_seen"])
            last = datetime.fromisoformat(entry["last_seen"])
            span = last - first
            return span >= timedelta(days=self.min_span_days)
        except (ValueError, KeyError):
            return False
