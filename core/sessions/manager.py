"""SessionManager — 会话 CRUD + 智能标题。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class SessionInfo:
    id: str
    name: str
    created_at: str = ""
    last_active_at: str = ""   # 最后活动时间（ISO 格式）
    turn_count: int = 0


class SessionManager:
    """会话生命周期管理。"""

    def __init__(self, sessions_root: Path) -> None:
        self._root = sessions_root

    def create(self, first_msg: str) -> SessionInfo:
        """创建新会话：生成目录 + meta.json。"""
        now = datetime.now(timezone.utc)
        base_id = now.strftime("%Y%m%d_%H%M%S")
        session_id = base_id
        session_dir = self._root / session_id
        # 防止同一秒内多次创建的碰撞
        suffix = 1
        while session_dir.exists():
            session_id = f"{base_id}_{suffix}"
            session_dir = self._root / session_id
            suffix += 1
        session_dir.mkdir(parents=True)
        (session_dir / "messages").mkdir()

        name = self.derive_title(first_msg)
        meta = {"name": name, "created_at": datetime.now(timezone.utc).isoformat()}
        (session_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")

        return SessionInfo(id=session_id, name=name, created_at=meta["created_at"])

    def list_all(self) -> list[SessionInfo]:
        """列出所有会话（按最后活动时间倒序 — 最近活跃的排最前）。"""
        if not self._root.exists():
            return []

        sessions: list[SessionInfo] = []
        for entry in self._root.iterdir():
            if not entry.is_dir():
                continue
            info = self._load_info(entry)
            if info:
                sessions.append(info)

        # 按 last_active_at 倒序，空字符串排最后
        sessions.sort(key=lambda s: s.last_active_at or "0000", reverse=True)
        return sessions

    def get(self, session_id: str) -> SessionInfo | None:
        session_dir = self._root / session_id
        if not session_dir.is_dir():
            return None
        return self._load_info(session_dir)

    def delete(self, session_id: str) -> bool:
        import shutil
        session_dir = self._root / session_id
        if not session_dir.is_dir():
            return False
        shutil.rmtree(session_dir)
        return True

    def rollback(self, session_dir: Path, target_turn: int) -> int:
        """回滚到指定轮次，删除该轮之后的所有记录。

        副作用：
          - 删除 messages/turn_{N+1}.json 到 turn_{M}.json
          - 截断 timeline.json 到前 target_turn 条
          - 截断 cache.json 到前 target_turn 条
          - 从 overview.json 检查点还原匹配的 overview.md

        Args:
            session_dir: 会话目录路径
            target_turn: 目标轮次（保留此轮及之前的所有数据）

        Returns:
            回滚后的轮数 (= target_turn)

        Raises:
            ValueError: target_turn 不合法
        """
        messages_dir = session_dir / "messages"
        if not messages_dir.exists():
            raise ValueError("会话消息目录不存在")

        # 统计当前轮数
        turn_files = sorted(messages_dir.glob("turn_*.json"))
        current_turn = len(turn_files)
        if target_turn < 0:
            raise ValueError(f"轮数不能为负数: {target_turn}")
        if target_turn >= current_turn:
            raise ValueError(
                f"当前已是第 {current_turn} 轮，无法回滚到第 {target_turn} 轮"
            )

        # 1. 删除 target_turn 之后的 turn 文件
        for tf in turn_files:
            try:
                turn_num = int(tf.stem.split("_", 1)[1])
                if turn_num > target_turn:
                    tf.unlink()
            except (ValueError, IndexError):
                pass

        # 2. 截断 timeline.json
        self._truncate_json_array(
            session_dir / "timeline.json",
            target_turn,
            key="turn",
        )

        # 3. 截断 cache.json
        self._truncate_json_array(
            session_dir / "cache.json",
            target_turn,
            key="turn",
        )

        # 4. 还原 overview.md + 截断 overview.json 检查点
        from core.context.compactor import restore_overview_from_checkpoint
        restore_overview_from_checkpoint(session_dir, target_turn)

        return target_turn

    @staticmethod
    def _truncate_json_array(path: Path, max_turn: int, key: str = "turn") -> None:
        """截断 JSON 数组文件，保留 key <= max_turn 的条目。"""
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                truncated = [e for e in data if e.get(key, 0) <= max_turn]
                path.write_text(
                    json.dumps(truncated, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except (json.JSONDecodeError, OSError):
            pass

    def _load_info(self, session_dir: Path) -> SessionInfo | None:
        meta_path = session_dir / "meta.json"
        name = session_dir.name
        created_at = ""
        last_active_at = ""
        turn_count = 0

        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                name = meta.get("name", name)
                created_at = meta.get("created_at", "")
            except (json.JSONDecodeError, OSError):
                pass

        # 统计轮数 + 最后活动时间
        messages_dir = session_dir / "messages"
        if messages_dir.exists():
            turn_files = sorted(messages_dir.glob("turn_*.json"))
            turn_count = len(turn_files)
            if turn_files:
                # 最后活动时间 = 最新 turn 文件的修改时间
                last_mtime = turn_files[-1].stat().st_mtime
                last_active_at = datetime.fromtimestamp(
                    last_mtime, tz=timezone.utc,
                ).isoformat()

        # 回退：从 timeline.json 获取最后活动时间
        if not last_active_at:
            timeline = session_dir / "timeline.json"
            if timeline.exists():
                try:
                    data = json.loads(timeline.read_text(encoding="utf-8"))
                    if data:
                        ts = data[-1].get("timestamp", "")
                        if ts:
                            last_active_at = ts
                except (json.JSONDecodeError, OSError, IndexError):
                    pass

        # 最终回退：使用目录修改时间
        if not last_active_at:
            last_active_at = datetime.fromtimestamp(
                session_dir.stat().st_mtime, tz=timezone.utc,
            ).isoformat()

        return SessionInfo(
            id=session_dir.name,
            name=name,
            created_at=created_at,
            last_active_at=last_active_at,
            turn_count=turn_count,
        )

    @staticmethod
    def derive_title(text: str, max_len: int = 20) -> str:
        """智能标题 — 规则引擎，零 LLM。"""
        text = text.strip()
        m = re.match(r'^(.*?)[。！？\n]', text)
        if m:
            text = m.group(1).strip()
        if not text:
            return "新对话"

        prefixes = [
            '能不能帮我', '可不可以帮我', '请帮我', '帮我',
            '能不能', '可不可以', '你可以', '你能',
            '怎么', '如何', '什么是', '什么',
            '我想', '我要', '我需要', '请', '来',
        ]
        for p in sorted(prefixes, key=len, reverse=True):
            if text.startswith(p) and len(text) > len(p) + 2:
                text = text[len(p):].strip()
                break

        if len(text) > max_len:
            text = text[:max_len] + "…"

        return text or "新对话"
