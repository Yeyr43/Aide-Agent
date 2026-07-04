"""EntryManager — 条目目录 CRUD。

管理 agent/data/ 下三个条目 JSON 文件：
  - preferences.json
  - workflows.json
  - long_term_memory.json

每条记录结构：
  {"content": "…", "source": {"session_id": "…", "turn": N},
   "status": "pending|integrated|replaced|orphaned",
   "created_at": "ISO", "updated_at": "ISO"}
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from core.storage import JsonStore
from core.locale import t
from core.setup import aide_dir

logger = logging.getLogger(__name__)

DATA_DIR = aide_dir() / "agent" / "data"

ENTRY_FILES = {
    "preferences": "preferences.json",
    "workflows": "workflows.json",
    "long_term_memory": "long_term_memory.json",
}


class EntryManager:
    """条目目录管理器。

    用法:
        entries = EntryManager(store)
        all_prefs = await entries.load("preferences")
        await entries.add("preferences", {"content": "…", "source": {…}})
    """

    def __init__(self, store: JsonStore) -> None:
        self._store = store

    def _path(self, entry_type: str) -> Path:
        fname = ENTRY_FILES.get(entry_type)
        if fname is None:
            raise ValueError(t("mem.unknown_entry_type", type=entry_type, valid=list(ENTRY_FILES)))
        return DATA_DIR / fname

    async def load(self, entry_type: str) -> list[dict]:
        """加载全部条目。"""
        path = self._path(entry_type)
        data = await self._store.read(path)
        return data if data is not None else []

    async def _save(self, entry_type: str, entries: list[dict]) -> None:
        path = self._path(entry_type)
        await self._store.write(path, entries)

    async def add(self, entry_type: str, content: str,
                  source: dict | None = None) -> dict:
        """添加新条目（status=pending）。"""
        entries = await self.load(entry_type)
        now = datetime.now(timezone.utc).isoformat()

        entry = {
            "content": content,
            "source": source or {},
            "status": "pending",
            "created_at": now,
            "updated_at": now,
        }
        entries.append(entry)
        await self._save(entry_type, entries)

        logger.debug(f"新增条目 [{entry_type}]: {content[:60]}…")
        return entry

    async def update(self, entry_type: str, index: int,
                     content: str | None = None,
                     status: str | None = None) -> dict | None:
        """更新指定索引的条目。

        Args:
            index: 条目在列表中的索引
            content: 新内容（None 表示不变）
            status: 新状态（None 表示不变）

        Returns:
            更新后的条目，索引无效返回 None
        """
        entries = await self.load(entry_type)
        if index < 0 or index >= len(entries):
            return None

        now = datetime.now(timezone.utc).isoformat()
        entry = entries[index]
        if content is not None:
            entry["content"] = content
        if status is not None:
            entry["status"] = status
        entry["updated_at"] = now

        await self._save(entry_type, entries)
        return entry

    async def get_pending(self, entry_type: str) -> list[dict]:
        """获取所有 status=pending 的条目。"""
        entries = await self.load(entry_type)
        return [e for e in entries if e.get("status") == "pending"]

    async def mark_status(self, entry_type: str, index: int, status: str) -> bool:
        """更新单条条目的状态。"""
        result = await self.update(entry_type, index, status=status)
        return result is not None
