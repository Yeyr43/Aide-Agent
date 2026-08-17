"""AutoMemoryExtractor — 自动记忆提取。

每轮对话结束后静默提取可沉淀的偏好/工作流/长记忆，直接追加到三个可变 prompt
（preferences.md / workflows.md / long_term_memory.md）。

设计（对照 free-code EXTRACT_MEMORIES，收敛到 Aide 的"用户可控"原则）：
- **显式开关**：默认关，`/mem-auto on` 才启用（settings.json app.auto_memory）。
  不破坏"系统绝不自动调 LLM 更新 prompt"——开启即用户豁免。
- **只写三个可变 prompt**：不写 overview（摘要归 /reflect 管）、不建新维度。
- **直接追加**：复用 format_memory_entry + 备份，fire-and-forget 不阻塞响应。
- **增量语义**：只从本轮 user/assistant 提取，注入现有记忆清单去重。
- **互斥**：本轮已被 /reflect 覆盖（last_reflected_turn >= turn）则跳过。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from core.locale import t
from core.setup import aide_dir
from core.storage import atomic_write_text

from .entries import MemoryEntry, format_memory_entry, parse_memory_file, split_sections
from .reflector import MEMORY_FILES

logger = logging.getLogger(__name__)

# 每个记忆文件的类型前缀（与 ReflectEngine 的 mem_types 一致）
_PREFIX = {
    "preferences": "pref",
    "workflows": "wf",
    "long_term_memory": "ltm",
}

# LLM 输出 section 标题 → 记忆 key
_SECTION_KEY = {
    "Preferences": "preferences",
    "Workflows": "workflows",
    "Long-Term Memory": "long_term_memory",
    "Long Term Memory": "long_term_memory",
}

# 模块级写锁：保护"读→追加→写回"不被并发提取/反思竞态
_write_lock = asyncio.Lock()


class AutoMemoryExtractor:
    """每轮对话后静默提取新记忆条目，直接追加到三个可变 prompt。"""

    def __init__(self, provider, agent_root: Path | None = None,
                 on_cache_flush: callable | None = None) -> None:
        self._provider = provider
        self._agent_root = agent_root or (aide_dir() / "agent")
        self._on_cache_flush = on_cache_flush
        self._turn_counter = 0  # 最近一次提取的轮次（id 生成用）

    # ── 入口 ────────────────────────────────────────────────────────

    async def maybe_extract(self, session_dir: Path | None, turn: int,
                            user_msg: str, assistant_text: str,
                            turn_messages: list[dict] | None = None) -> bool:
        """每轮 chat() 末尾调用。返回是否发生了写入（测试断言用）。

        全方法静默失败（fire-and-forget 后台任务，不阻塞主对话）。
        """
        try:
            return await self._extract(session_dir, turn, user_msg, assistant_text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("自动记忆提取异常（静默）", exc_info=True)
            return False

    async def _extract(self, session_dir: Path | None, turn: int,
                       user_msg: str, assistant_text: str) -> bool:
        # 1. 开关检查（实时读 settings.json，/mem-auto 命令即时生效）
        try:
            from core.config import Config
            settings = await asyncio.to_thread(Config.load_settings)
            if not settings.get("app", {}).get("auto_memory", False):
                return False
        except Exception:
            return False

        # 2. 有最终回复才提取（空回复无信号）
        if not assistant_text or not assistant_text.strip():
            return False

        # 3. 与 /reflect 互斥：本轮已被反思覆盖则跳过
        if session_dir is not None and await self._reflected_through(session_dir, turn):
            return False

        # 4. 读现有记忆（去重 + prompt 注入）
        current = await self._read_current_memory()

        # 5. 调 LLM 提取新增条目
        response = await self._call_llm(user_msg, assistant_text, current)
        if not response:
            return False

        # 6. 解析 → 追加写入
        parsed = self._parse_sections(response)
        self._turn_counter = turn  # id 生成用
        wrote = await self._append_entries(
            current, parsed, turn, session_dir.name if session_dir else "",
        )

        # 7. 成功写后更新 marker（失败不推进，下次重试）
        if wrote and session_dir is not None:
            await self._write_marker(session_dir, turn)
        return wrote

    # ── LLM 调用 ────────────────────────────────────────────────────

    async def _call_llm(self, user_msg: str, assistant_text: str,
                        current: dict[str, str]) -> str:
        """调用 LLM 提取新增记忆条目（纯文本输出，不带工具）。"""
        system = t("mem.auto_system")
        user_prompt = self._build_user_prompt(user_msg, assistant_text, current)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
        try:
            from core.llm_gateway.provider import stream_text
            return (await stream_text(self._provider, messages)).strip()
        except Exception:
            logger.debug("自动记忆提取 LLM 调用失败（静默）", exc_info=True)
            return ""

    def _build_user_prompt(self, user_msg: str, assistant_text: str,
                           current: dict[str, str]) -> str:
        """构建 user prompt：本轮对话 + 现有记忆清单（引导 LLM 只出新条目）。"""
        parts = [t("mem.auto_user_intro")]
        parts.append(f"User: {user_msg[:600]}")
        parts.append(f"Assistant: {assistant_text[:600]}")
        parts.append("")
        parts.append(t("mem.auto_existing"))
        for key, fname in MEMORY_FILES.items():
            content = current.get(fname, "").strip()
            parts.append(f"### {key}:\n{content if content else '(空)'}\n")
        parts.append(t("mem.auto_instruction"))
        return "\n".join(parts)

    # ── 解析 ────────────────────────────────────────────────────────

    def _parse_sections(self, text: str) -> dict[str, list[str]]:
        """把 LLM 输出按 ## section 分割，提取每个 section 下的 "- 内容" 列表。

        复用 overview.split_sections 公共原语（统一三处 section 解析）。

        Returns:
            {"preferences": [...], "workflows": [...], "long_term_memory": [...]}
        """
        result: dict[str, list[str]] = {
            "preferences": [], "workflows": [], "long_term_memory": [],
        }
        for title, lines in split_sections(text).items():
            current_key = _SECTION_KEY.get(title)
            if current_key is None:
                continue
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("- "):
                    content = stripped[2:].strip()
                    if content and content not in ("(无变更)", "(无新增)", "(none)"):
                        result[current_key].append(content)
        return result

    # ── 写入 ────────────────────────────────────────────────────────

    async def _append_entries(self, current: dict[str, str],
                              parsed: dict[str, list[str]], turn: int,
                              session_id: str) -> bool:
        """把新条目追加到对应记忆文件（读→格式化→原子写，带锁）。"""
        async with _write_lock:
            wrote_any = False
            for key, contents in parsed.items():
                if not contents:
                    continue
                fname = MEMORY_FILES[key]
                filepath = self._agent_root / fname

                # 以本次提取开始时的快照为基础（单进程 asyncio，并发修改概率极低）
                base = current.get(fname, "") or ""

                # 去重：跳过已存在的条目内容
                existing = {e.content for e in parse_memory_file(base, filename=fname)}
                new_ids = {e.id for e in parse_memory_file(base, filename=fname) if e.id}
                additions: list[str] = []
                for content in contents:
                    if content in existing:
                        continue
                    new_id = self._next_id(key, new_ids)
                    new_ids.add(new_id)
                    entry = MemoryEntry(
                        id=new_id, content=content, file=fname,
                        created=datetime.now().date().isoformat(),
                        source=f"{session_id}/turn_{turn}" if session_id else "",
                    )
                    additions.append(format_memory_entry(entry))

                if not additions:
                    continue

                # 备份 + 原子追加
                try:
                    from .version import _backup_prompt
                    _backup_prompt(filepath)
                except Exception:
                    logger.debug("备份失败（静默）", exc_info=True)

                updated = (base.rstrip("\n") + "\n\n"
                           + "\n\n".join(additions) + "\n") if base.strip() else (
                    "\n\n".join(additions) + "\n")
                await asyncio.to_thread(atomic_write_text, filepath, updated)
                wrote_any = True

            if wrote_any and self._on_cache_flush:
                try:
                    self._on_cache_flush()
                except Exception:
                    pass
            return wrote_any

    def _next_id(self, key: str, existing_ids: set[str]) -> str:
        """生成不与现有条目冲突的新 id，形如 pref_a003_0。"""
        prefix = _PREFIX[key]
        seq = 0
        while True:
            candidate = f"{prefix}_a{self._turn_counter:03d}_{seq}"
            if candidate not in existing_ids:
                return candidate
            seq += 1

    # 最近一次提取的轮次（id 生成用，_extract 里设置）
    _turn_counter: int = 0

    # ── 状态读写 ────────────────────────────────────────────────────

    async def _read_current_memory(self) -> dict[str, str]:
        """读取三个记忆文件的当前内容。"""
        result: dict[str, str] = {}
        for fname in MEMORY_FILES.values():
            filepath = self._agent_root / fname
            if filepath.exists():
                try:
                    result[fname] = await asyncio.to_thread(
                        filepath.read_text, encoding="utf-8",
                    )
                except OSError:
                    result[fname] = ""
            else:
                result[fname] = ""
        return result

    async def _reflected_through(self, session_dir: Path, turn: int) -> bool:
        """本轮是否已被 /reflect 覆盖（last_reflected_turn >= turn → 跳过）。"""
        from core.sessions.manager import read_session_meta
        meta = await asyncio.to_thread(read_session_meta, session_dir)
        return meta.get("last_reflected_turn", 0) >= turn

    async def _write_marker(self, session_dir: Path, turn: int) -> None:
        """写入自动提取标记到 meta.json（last_auto_memory_turn）。"""
        from core.sessions.manager import update_session_meta
        await asyncio.to_thread(
            update_session_meta,
            session_dir,
            last_auto_memory_turn=turn,
            auto_memory_at=datetime.now(timezone.utc).isoformat(),
        )
