"""ContextIngester — 每轮对话后写入 session 数据。

负责：
  - 首条消息时延迟创建 session 目录
  - 写入 messages/turn_{NNN}.json（完整原文存档）
  - 追加 timeline.json（一句话事件索引）
  - 追加 cache.json（累积窗口上下文摘要）

所有写操作通过 JsonStore 保证原子性。
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from core.storage import JsonStore
from core.locale import t

from core.setup import aide_dir

logger = logging.getLogger(__name__)

# 所有 session 的父目录
SESSIONS_ROOT = aide_dir() / "sessions"

# ── 摘要生成（规则模板，不用 LLM）───────────────────────────────────


def _turn_summary(user_msg: str, assistant_msg: str, tool_calls: list[dict] | None = None) -> str:
    """生成一句话事件概览，用于 timeline.json 和 cache.json。

    纯规则生成，<1ms。
    """
    user_preview = user_msg[:80].replace("\n", " ").strip()
    if len(user_msg) > 80:
        user_preview += "…"

    if tool_calls:
        tool_names = ", ".join(
            tc.get("function", {}).get("name", "?") for tc in tool_calls
        )
        return t("ctx.ingest_tool_call", tools=tool_names, preview=user_preview)

    return user_preview


# ── ContextIngester ───────────────────────────────────────────────────


class ContextIngester:
    """每轮对话后摄取写入。

    用法:
        ingester = ContextIngester(store)
        session_id = await ingester.ensure_session()  # 首条消息时
        await ingester.ingest(session_id, turn, user_msg, assistant_msg, tool_calls)
    """

    def __init__(self, store: JsonStore) -> None:
        self._store = store
        self._session_id: str | None = None
        self._session_dir: Path | None = None

    # ── session 生命周期 ──────────────────────────────────────────

    def ensure_session(self, session_id: str | None = None) -> Path:
        """确保 session 目录存在，延迟创建。

        首次调用时创建 session 目录。
        之后返回已有目录。

        Args:
            session_id: 会话 ID（格式 YYYYMMDD_HHMMSS），为空则自动生成

        Returns:
            session 目录路径
        """
        if self._session_dir is not None:
            return self._session_dir

        if session_id is None:
            session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        self._session_id = session_id
        self._session_dir = SESSIONS_ROOT / session_id

        # 创建子目录
        self._session_dir.mkdir(parents=True, exist_ok=True)
        (self._session_dir / "messages").mkdir(exist_ok=True)

        logger.info(t("ctx.ingest_session_start", id=session_id))
        return self._session_dir

    @property
    def session_id(self) -> str | None:
        return self._session_id

    # ── 摄取 ───────────────────────────────────────────────────────

    async def ingest(
        self,
        turn: int,
        user_msg: str,
        assistant_msg: str,
        tool_calls: list[dict] | None = None,
        turn_messages: list[dict] | None = None,
    ) -> None:
        """写入一轮对话的所有数据。

        Args:
            turn: 轮次编号（从 1 开始）
            user_msg: 用户消息原文
            assistant_msg: AI 回复原文（含思考过程，如有）
            tool_calls: 工具调用详情（含结果）
            turn_messages: 本轮增量消息（只存当轮，不存完整历史）
        """
        if self._session_dir is None:
            raise RuntimeError("session 未创建，请先调用 ensure_session()")

        timestamp = datetime.now(timezone.utc).isoformat()
        summary = _turn_summary(user_msg, assistant_msg, tool_calls)

        # ── 1. 写入 messages/turn_{NNN}.json（仅当轮增量消息）──
        turn_data = {
            "turn": turn,
            "timestamp": timestamp,
            "user": user_msg,
            "assistant": assistant_msg,
            "tool_calls": tool_calls or [],
            "messages": turn_messages or [],  # 仅本轮增量消息
        }
        turn_path = self._session_dir / "messages" / f"turn_{turn:03d}.json"
        await self._store.write(turn_path, turn_data)

        # ── 2. 追加 timeline.json ──
        timeline_path = self._session_dir / "timeline.json"
        timeline = await self._store.read(timeline_path) or []
        timeline.append({
            "turn": turn,
            "timestamp": timestamp,
            "summary": summary,
        })
        await self._store.write(timeline_path, timeline)

        # ── 3. 追加 cache.json ──
        cache_path = self._session_dir / "cache.json"
        cache = await self._store.read(cache_path) or []
        cache.append({
            "turn": turn,
            "summary": summary,
        })
        await self._store.write(cache_path, cache)

        logger.debug(t("ctx.ingest_turn", turn=turn, summary=summary[:60]))
