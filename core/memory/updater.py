"""PromptUpdater — Prompt 全量重构（/profile update 触发）。

用户手动触发，更新期间暂停对话。
流程：
  1. 收集 pending 条目
  2. 回溯源会话（前后各 5 轮）
  3. 备份当前 prompt（P5: 版本化）
  4. LLM 整合 → 全量覆盖 agent/*.md
  5. 更新条目状态 → 刷新缓存
"""

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .entries import EntryManager
from core.locale import t
from core.setup import aide_dir

logger = logging.getLogger(__name__)

AGENT_ROOT = aide_dir() / "agent"
SESSIONS_ROOT = aide_dir() / "sessions"
BACKUPS_DIR = aide_dir() / "backups"


# ── 版本管理 ──────────────────────────────────────────────────────────

def _backup_prompt(prompt_path: Path) -> str | None:
    """备份当前 prompt 文件到 backups/ 目录。

    Args:
        prompt_path: agent/*.md 文件路径

    Returns:
        备份文件名，文件不存在时返回 None
    """
    if not prompt_path.exists():
        return None

    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_name = f"{prompt_path.name}_{timestamp}.backup"
    backup_path = BACKUPS_DIR / backup_name
    shutil.copy2(prompt_path, backup_path)
    logger.info(f"Prompt 已备份: {backup_name}")
    return backup_name


def _append_version_log(filename: str, backup_name: str) -> None:
    """追加版本记录到 backups/version_log.json。

    Args:
        filename: 原始 prompt 文件名（如 "preferences.md"）
        backup_name: 备份文件名
    """
    log_path = BACKUPS_DIR / "version_log.json"
    version_log: dict = {}

    if log_path.exists():
        try:
            version_log = json.loads(log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            version_log = {}

    if filename not in version_log:
        version_log[filename] = []

    backup_file = BACKUPS_DIR / backup_name
    size = backup_file.stat().st_size if backup_file.exists() else 0

    version_log[filename].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "backup": backup_name,
        "size": size,
    })

    log_path.write_text(json.dumps(version_log, ensure_ascii=False, indent=2), encoding="utf-8")


def rollback_prompt(prompt_type: str, n: int = 0) -> tuple[bool, str]:
    """回滚 prompt 到第 N 个历史版本。

    Args:
        prompt_type: "preferences" | "workflows" | "long_term_memory"
        n: 0 = 最新备份, 1 = 上一个, ...

    Returns:
        (success, message)
    """
    filename = f"{prompt_type}.md"
    log_path = BACKUPS_DIR / "version_log.json"

    if not log_path.exists():
        return False, f"无版本历史 — {log_path} 不存在"

    try:
        version_log = json.loads(log_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False, "版本日志损坏，无法回滚"

    history = version_log.get(filename, [])
    if not history:
        return False, f"{prompt_type} 无备份记录"

    if n < 0 or n >= len(history):
        return False, f"无效的备份编号 {n}（可用 0-{len(history)-1}）"

    # 取倒数第 n 个（最新的索引最大）
    entry = history[-(n + 1)]
    backup_name = entry["backup"]
    backup_path = BACKUPS_DIR / backup_name

    if not backup_path.exists():
        return False, f"备份文件丢失: {backup_name}"

    # 恢复
    prompt_path = AGENT_ROOT / filename
    shutil.copy2(backup_path, prompt_path)

    logger.info(f"Prompt {filename} 已回滚到 {backup_name}")
    return True, f"{filename} 已回滚到备份 {backup_name}（{entry['timestamp']}）"

# ── 更新 prompt 模板 ─────────────────────────────────────────────────

def _get_update_prompts() -> dict:
    """返回 UPDATE_PROMPTS 配置字典（含已翻译的 label 和 system prompt）。"""
    return {
        "preferences": {
            "file": "preferences.md",
            "label": t("mem.label_preferences"),
            "system": t("mem.update_preferences_system"),
        },
        "workflows": {
            "file": "workflows.md",
            "label": t("mem.label_workflows"),
            "system": t("mem.update_workflows_system"),
        },
        "long_term_memory": {
            "file": "long_term_memory.md",
            "label": t("mem.label_long_term_memory"),
            "system": t("mem.update_long_term_memory_system"),
        },
    }


# ── PromptUpdater ─────────────────────────────────────────────────────


class PromptUpdater:
    """Prompt 全量重构器。

    用法:
        updater = PromptUpdater(provider, entries, on_cache_flush)
        result = await updater.update_all()
    """

    def __init__(self, provider, entries: EntryManager,
                 on_cache_flush: callable = None) -> None:
        self._provider = provider
        self._entries = entries
        self._on_cache_flush = on_cache_flush  # Assembler 缓存刷新回调

    async def update_all(self) -> dict[str, bool]:
        """对所有三种 prompt 类型执行全量更新。

        Returns:
            {"preferences": True/False, "workflows": True/False, "long_term_memory": True/False}
        """
        if self._provider is None:
            logger.error(t("mem.no_provider"))
            return {"preferences": False, "workflows": False, "long_term_memory": False}

        results = {}

        for entry_type, config in _get_update_prompts().items():
            success = await self._update_type(entry_type, config)
            results[entry_type] = success

        # 刷新 Assembler 缓存
        if self._on_cache_flush:
            self._on_cache_flush()

        logger.info(t("mem.update_complete", results=results))
        return results

    async def _update_type(self, entry_type: str, config: dict) -> bool:
        """更新单类 prompt。"""
        # ── 1. 收集 pending 条目 ──
        pending = await self._entries.get_pending(entry_type)
        if not pending:
            logger.info(t("mem.no_pending", label=config['label']))
            return True

        # ── 2. 回溯源会话，标记丢失的源会话为 orphaned ──
        source_context, orphaned_indices = await self._gather_source_context(pending, entry_type)

        # 标记孤儿条目
        for orphaned_idx in orphaned_indices:
            pending_entry = pending[orphaned_idx]
            # 需要在 all_entries 中找到对应位置
            all_current = await self._entries.load(entry_type)
            for i, e in enumerate(all_current):
                if (e.get("content") == pending_entry.get("content") and
                    e.get("status") == "pending"):
                    await self._entries.mark_status(entry_type, i, "orphaned")
                    logger.info(f"[{config['label']}] 条目标记 orphaned: {e['content'][:40]}...")
                    break

        # ── 3. 读取当前 prompt ──
        prompt_path = AGENT_ROOT / config["file"]
        current_prompt = ""
        if prompt_path.exists():
            current_prompt = prompt_path.read_text(encoding="utf-8")

        # ── 4. 组装 LLM 请求 ──
        pending_text = "\n".join(
            f"- {e.get('content', '')}" for e in pending
        )

        user_msg = t("mem.updater_user_template",
            current_prompt=current_prompt if current_prompt else "",
            signals=source_context if source_context else "",
            pending_entries=pending_text,
        )

        messages = [
            {"role": "system", "content": config["system"]},
            {"role": "user", "content": user_msg},
        ]

        # ── 5. 调用 LLM ──
        try:
            new_prompt = ""
            async for event in self._provider.chat_with_tools(messages, []):
                from core.llm_gateway import TextDelta, StreamEnd
                if isinstance(event, TextDelta):
                    new_prompt += event.content
                elif isinstance(event, StreamEnd):
                    break
        except TypeError:
            logger.exception(f"[{config['label']}] LLM 流处理类型错误")
            return False
        except Exception as e:
            logger.exception(f"[{config['label']}] LLM 调用失败")
            return False

        if not new_prompt.strip():
            logger.warning(f"[{config['label']}] LLM 返回空内容")
            return False

        # ── 5.5. 备份当前 prompt（P5: 版本化）──
        backup_name = _backup_prompt(prompt_path)
        if backup_name:
            _append_version_log(config["file"], backup_name)

        # ── 6. 写入新 prompt ──
        prompt_path.write_text(new_prompt.strip(), encoding="utf-8")

        # ── 7. 标记条目为 integrated ──
        all_entries = await self._entries.load(entry_type)
        for i, e in enumerate(all_entries):
            if e.get("status") == "pending":
                await self._entries.mark_status(entry_type, i, "integrated")

        logger.info(t("mem.integrated", label=config['label'], n=len(pending)))
        return True

    async def _gather_source_context(self, pending_entries: list[dict],
                                      entry_type: str) -> tuple[str, list[int]]:
        """回溯源会话，收集 pending 条目产生时的上下文。

        Returns:
            (context_text, orphaned_indices)
        """
        contexts: list[str] = []
        orphaned: list[int] = []

        for idx, entry in enumerate(pending_entries):
            source = entry.get("source", {})
            session_id = source.get("session_id", "")
            turn = source.get("turn", 0)

            if not session_id or not turn:
                continue

            session_dir = SESSIONS_ROOT / session_id
            messages_dir = session_dir / "messages"

            if not messages_dir.exists():
                # 源会话丢失 → 标记 orphaned
                logger.warning(f"源会话丢失: {session_id}")
                orphaned.append(idx)
                continue

            # 读取会话总览用于快速了解会话背景
            overview_path = session_dir / "overview.md"
            if overview_path.exists():
                try:
                    from core.context.compactor import parse_overview_md
                    text = overview_path.read_text(encoding="utf-8")
                    sections = parse_overview_md(text)
                    topics = sections.get("话题", [])
                    if topics:
                        contexts.append(f"[会话 {session_id}] 背景: {'; '.join(topics[:3])}")
                except (OSError, Exception):
                    pass

            # 读取前后各 5 轮原文
            for offset in range(-5, 6):
                t = turn + offset
                if t < 1:
                    continue

                turn_path = messages_dir / f"turn_{t:03d}.json"
                if not turn_path.exists():
                    continue

                try:
                    data = json.loads(turn_path.read_text(encoding="utf-8"))
                    marker = " ← 条目产生轮" if offset == 0 else ""
                    contexts.append(
                        f"[轮 {t}{marker}] 用户: {data.get('user','')[:120]}"
                    )
                except (json.JSONDecodeError, OSError):
                    continue

        if not contexts:
            return "", orphaned

        return "\n".join(contexts[:30]), orphaned  # 最多 30 条上下文，避免过长
