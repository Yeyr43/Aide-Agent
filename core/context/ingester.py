"""ContextIngester — 每轮对话后写入 session 数据。

负责：
  - 绑定当前 session 目录（由 SessionManager 创建）
  - 写入 messages/turn_{NNN}.json（完整原文存档）
  - 追加 timeline.json（一句话事件索引 + 窗口上下文摘要）

所有写操作通过 JsonStore 保证原子性。

Session 目录由 SessionManager 统一创建，ContextIngester 不重复创建。
"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from core.storage import JsonStore
from core.locale import t

logger = logging.getLogger(__name__)

# ── 摘要生成（规则模板，不用 LLM）───────────────────────────────────


def _turn_summary(user_msg: str, assistant_msg: str, tool_calls: list[dict] | None = None) -> str:
    """生成一句话事件概览，用于 timeline.json。

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
        ingester = ContextIngester(store, sessions_root)
        ingester.set_session(session_id)
        await ingester.ingest(turn, user_msg, assistant_msg, turn_messages)
    """

    def __init__(self, store: JsonStore,
                 sessions_root: Path | None = None,
                 search_index = None) -> None:
        self._store = store
        from core.setup import aide_dir
        self._sessions_root = sessions_root or (aide_dir() / "sessions")
        self._search_index = search_index  # SearchIndex | None
        self._session_id: str | None = None
        self._session_dir: Path | None = None

    # ── session 生命周期 ──────────────────────────────────────────

    def set_session(self, session_id: str) -> Path:
        """绑定当前会话（目录由 SessionManager 预先创建）。

        支持两种输入：
        - 会话 ID（如 "20260701_120000"）→ 从 _sessions_root 解析路径
        - 完整路径（如 Path("/tmp/sessions/test")）→ 直接使用

        此后 ingest() 写入该会话目录。session_id 键统一归一化为**目录名**
        （完整路径取 .name），与 SearchIndex rebuild/remove 使用的
        session_dir.name 一致——否则 add 用完整路径、rebuild/删除用目录名，
        删除会话后会残留索引条目，且 recall 时间衰减解析前 15 字符出错。

        Args:
            session_id: 会话 ID 或完整路径（str 或 Path）

        Returns:
            session 目录路径
        """
        sid = str(session_id)
        is_path = "/" in sid or "\\" in sid
        candidate = Path(sid) if is_path else self._sessions_root / sid
        key = candidate.name

        if self._session_dir is not None and self._session_id == key:
            return self._session_dir

        self._session_id = key
        self._session_dir = candidate

        # messages/ 子目录若不存在则创建（兼容从旧版本恢复的会话）
        (self._session_dir / "messages").mkdir(parents=True, exist_ok=True)

        logger.debug("ContextIngester bound to session %s", key)
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
        thinking: str = "",
    ) -> None:
        """写入一轮对话的所有数据。

        Args:
            turn: 轮次编号（从 1 开始）
            user_msg: 用户消息原文
            assistant_msg: AI 回复原文（含思考过程，如有）
            tool_calls: 工具调用详情（含结果）
            turn_messages: 本轮增量消息（只存当轮，不存完整历史）
            thinking: 本轮 LLM 思考内容（用于退出重进后恢复显示）
        """
        if self._session_dir is None:
            raise RuntimeError("session 未创建，请先调用 set_session()")

        timestamp = datetime.now(timezone.utc).isoformat()
        summary = _turn_summary(user_msg, assistant_msg, tool_calls)

        # ── 1. 写入 messages/turn_{NNN}.json（仅当轮增量消息）──
        # 单文件自包含：完整数据在 messages 内（含 tool_calls / tool 结果）。
        # 不再冗余顶层 user/assistant —— 旧格式读侧已兼容（restorer._extract_messages）。
        turn_data = {
            "turn": turn,
            "timestamp": timestamp,
            "thinking": thinking or "",
            "messages": turn_messages or [],
        }
        turn_path = self._session_dir / "messages" / f"turn_{turn:03d}.json"
        await self._store.write(turn_path, turn_data)

        # ── 1.5 更新 meta.json 的 last_active_at ──
        from core.sessions.manager import update_session_meta
        await asyncio.to_thread(
            update_session_meta, self._session_dir, last_active_at=timestamp,
        )

        # ── 2. 追加 timeline.json（JSONL 格式：每行一个 JSON 对象）──
        from core.storage import append_jsonl
        append_jsonl(self._session_dir / "timeline.json", {
            "turn": turn,
            "timestamp": timestamp,
            "summary": summary,
        })

        # ── 3. 追加全局搜索索引 ──
        if self._search_index is not None and self._session_id:
            self._search_index.add(self._session_id, turn, summary)

        logger.debug(t("ctx.ingest_turn", turn=turn, summary=summary[:60]))
