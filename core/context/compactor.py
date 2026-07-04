"""ContextCompactor — 会话压缩（/compress 触发）。

用户手动触发，低频操作。
读取 messages/turn_*.json 全部原文 → LLM 生成 overview.md → 追加 overview.json 检查点 → 清空 cache.json。

overview.md   — 人类可读的 Markdown 会话概览（注入 LLM 上下文）
overview.json — 压缩检查点日志：[{to_turn, compressed_at, overview_md}]
                回滚时从匹配检查点还原 overview.md
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..storage import JsonStore
from ..locale import t

logger = logging.getLogger(__name__)

# ── 分层压缩辅助 ──────────────────────────────────────────────────────

# 纠正/决策关键词
_CORRECTION_KEYWORDS = [
    "不对", "错了", "不是", "纠正", "应该是", "正确",
    "wrong", "correct", "no,", "not ", "should be",
    "记住", "记住这", "以后都", "以后", "always",
    "remember", "important", "关键",
]

# 分层标记常量（硬编码英文，避免 locale 膨胀）
_TIER_CRITICAL_MARKER = "CRITICAL:"
_TIER_TURN_LABEL = "Turn {n}"
_TIER_USER_LABEL = "User:"
_TIER_ASSISTANT_LABEL = "Assistant:"
_TIER_CRITICAL_SECTION = "## Critical Turns"
_TIER_DETAIL_SECTION = "## Recent Details"
_TIER_EARLY_SECTION = "## Early Turns"
_TIER_OMITTED_PREFIX = "(earlier content omitted)"


def _is_correction(text: str) -> bool:
    """判断用户消息是否为纠正/反馈。"""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in _CORRECTION_KEYWORDS)


def _has_write_tool(turn_data: dict) -> bool:
    """判断轮次中是否包含文件写入工具调用。"""
    tool_calls = turn_data.get("tool_calls", [])
    if isinstance(tool_calls, list):
        return any(
            (tc.get("name") or tc.get("function", {}).get("name", ""))
            in ("write_file", "edit_file")
            for tc in tool_calls
        )
    return False


def _classify_turn(
    turn_data: dict,
    turn_idx: int,
    total_turns: int,
    window_turns: int = 8,
) -> str:
    """分类轮次：critical / mid / early。

    - critical: 用户纠正/决策 或 文件写入操作
    - early: 超出窗口范围的旧轮次
    - mid: 窗口内的普通轮次
    """
    user_text = turn_data.get("user", "")

    if _is_correction(user_text) or _has_write_tool(turn_data):
        return "critical"

    if total_turns - turn_idx > window_turns:
        return "early"

    return "mid"


# ── 压缩 prompt（输出 Markdown，非 JSON）───────────────────────────────


def get_compact_system_prompt() -> str:
    """返回压缩用的 system prompt（已翻译）。"""
    return t("ctx.compact_system_prompt")


class ContextCompactor:
    """会话压缩器 — 输出 overview.md + overview.json 检查点。

    用法:
        compactor = ContextCompactor(provider, store)
        result = await compactor.compact(session_dir)
    """

    def __init__(self, provider, store: JsonStore) -> None:
        self._provider = provider
        self._store = store

    async def compact(self, session_dir: Path) -> str | None:
        """执行会话压缩。

        Args:
            session_dir: session 目录路径

        Returns:
            生成的 overview.md 内容，失败时返回 None
        """
        messages_dir = session_dir / "messages"
        overview_md_path = session_dir / "overview.md"
        overview_json_path = session_dir / "overview.json"
        cache_path = session_dir / "cache.json"

        # ── 1. 收集全部对话原文 ──
        if not messages_dir.exists():
            logger.warning(t("ctx.no_messages_to_compact"))
            return None

        turn_files = sorted(messages_dir.glob("turn_*.json"))
        if not turn_files:
            logger.warning(t("ctx.no_records_to_compact"))
            return None

        # ── 分层构建 transcript ──
        critical_parts: list[str] = []
        mid_parts: list[str] = []
        early_parts: list[str] = []
        max_turn = 0
        total = len(turn_files)
        for idx, tf in enumerate(turn_files):
            try:
                data = json.loads(tf.read_text(encoding="utf-8"))
                turn_num = data["turn"]
                max_turn = max(max_turn, turn_num)
                user_text = data.get("user", "")
                assistant_text = data.get("assistant", "")

                tier = _classify_turn(data, idx + 1, total, window_turns=8)

                if tier == "critical":
                    critical_parts.append(
                        f"{_TIER_CRITICAL_MARKER} {_TIER_TURN_LABEL.format(n=idx + 1)}\n"
                        f"  {_TIER_USER_LABEL}: {user_text[:300]}\n"
                        f"  {_TIER_ASSISTANT_LABEL}: {assistant_text[:300]}"
                    )
                elif tier == "mid":
                    mid_parts.append(
                        f"{_TIER_TURN_LABEL.format(n=idx + 1)}: {user_text[:120]}"
                    )
                else:  # early
                    if user_text:
                        early_parts.append(
                            f"{_TIER_TURN_LABEL.format(n=idx + 1)}: {user_text[:60]}"
                        )
            except (json.JSONDecodeError, OSError, KeyError):
                continue

        # ── 组装分层 transcript ──
        transcript_parts: list[str] = []
        if critical_parts:
            transcript_parts.append(_TIER_CRITICAL_SECTION)
            transcript_parts.extend(critical_parts)
            transcript_parts.append("")
        if mid_parts:
            transcript_parts.append(_TIER_DETAIL_SECTION)
            transcript_parts.extend(mid_parts)
            transcript_parts.append("")
        if early_parts:
            transcript_parts.append(_TIER_EARLY_SECTION)
            transcript_parts.extend(early_parts)

        full_transcript = "\n".join(transcript_parts)

        # ── 2. 读取已有 overview.md（累积压缩）──
        existing_overview = ""
        if overview_md_path.exists():
            try:
                existing_overview = overview_md_path.read_text(encoding="utf-8")
            except OSError:
                pass

        # ── 3. 组装 LLM 请求 ──
        transcript_text = full_transcript

        # 截断过长文本（保留最后 ~16000 字符，critical turns 倾向保留在后段）
        MAX_CHARS = 24000
        KEEP_CHARS = 16000

        if len(transcript_text) > MAX_CHARS:
            transcript_text = _TIER_OMITTED_PREFIX + "\n\n" + transcript_text[-KEEP_CHARS:]

        user_content = t("ctx.conversation_record") + "\n\n" + transcript_text
        if existing_overview:
            user_content += "\n\n" + t("ctx.existing_overview") + "\n" + existing_overview + "\n\n" + t("ctx.generate_overview")

        messages = [
            {"role": "system", "content": get_compact_system_prompt()},
            {"role": "user", "content": user_content},
        ]

        # ── 4. 调用 LLM（纯文本，不带 tools）──
        try:
            response_text = ""
            async for event in self._provider.chat_with_tools(messages, []):
                from ..llm_gateway import TextDelta, StreamEnd
                if isinstance(event, TextDelta):
                    response_text += event.content
                elif isinstance(event, StreamEnd):
                    break
        except TypeError:
            logger.exception(t("ctx.compact_llm_stream_error"))
            return None
        except Exception:
            logger.exception(t("ctx.compact_llm_error"))
            return None

        if not response_text.strip():
            return None

        # ── 5. 后处理：去掉可能的代码块包裹 ──
        overview_md = _clean_markdown_response(response_text)

        # ── 6. 写入 overview.md ──
        await self._store.write(overview_md_path, overview_md)

        # ── 7. 追加 overview.json 检查点 ──
        checkpoints: list[dict] = []
        if overview_json_path.exists():
            try:
                existing = json.loads(overview_json_path.read_text(encoding="utf-8"))
                if isinstance(existing, list):
                    checkpoints = existing
            except (json.JSONDecodeError, OSError):
                pass

        checkpoints.append({
            "to_turn": max_turn,
            "compressed_at": datetime.now(timezone.utc).isoformat(),
            "overview_md": overview_md,
        })
        await self._store.write(overview_json_path, checkpoints)

        # ── 8. 清空 cache.json ──
        await self._store.write(cache_path, [])

        logger.info(t("ctx.compact_done", turns=len(turn_files), turn=max_turn))
        return overview_md


def _clean_markdown_response(text: str) -> str:
    """清理 LLM 返回的 Markdown：去掉可能的代码块包裹。"""
    import re

    text = text.strip()

    # 去掉 ```markdown ... ``` 包裹
    m = re.match(r'```(?:markdown|md)?\s*\n(.*?)\n```\s*$', text, re.DOTALL)
    if m:
        return m.group(1).strip()

    # 去掉开头的 ``` 和结尾的 ```
    if text.startswith("```"):
        text = re.sub(r'^```(?:markdown|md)?\s*\n?', '', text)
        text = re.sub(r'\n?```\s*$', '', text)

    return text.strip()


def parse_overview_md(text: str) -> dict[str, list[str]]:
    """解析 overview.md 为结构化 sections。

    将 Markdown 的 ## 标题映射为 section key，其下的 - 列表项为值。

    Returns:
        dict like {"话题": [...], "用户偏好": [...], "纠正记录": [...], "决策与结论": [...]}
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip()
            if current not in sections:
                sections[current] = []
        elif stripped.startswith("- ") and current is not None:
            sections[current].append(stripped[2:].strip())
    return sections


def restore_overview_from_checkpoint(session_dir: Path, target_turn: int) -> bool:
    """回滚时：从 overview.json 找到匹配检查点，还原 overview.md。

    Args:
        session_dir: 会话目录
        target_turn: 回滚目标轮次

    Returns:
        True 如果成功还原，False 如果无匹配检查点
    """
    overview_json_path = session_dir / "overview.json"
    overview_md_path = session_dir / "overview.md"

    if not overview_json_path.exists():
        return False

    try:
        checkpoints: list[dict] = json.loads(
            overview_json_path.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError):
        return False

    if not isinstance(checkpoints, list) or not checkpoints:
        return False

    # 找到 to_turn <= target_turn 的最后一个检查点
    matched = None
    for cp in checkpoints:
        if cp.get("to_turn", 0) <= target_turn:
            matched = cp
        else:
            break

    if matched is None:
        # 没有检查点覆盖到 target_turn → 删除 overview.md
        if overview_md_path.exists():
            overview_md_path.unlink()
        # 保留 overview.json（没有任何检查点需要截断... 其实可以删了）
        return False

    # 还原 overview.md
    overview_md = matched.get("overview_md", "")
    if overview_md:
        overview_md_path.write_text(overview_md, encoding="utf-8")
    elif overview_md_path.exists():
        overview_md_path.unlink()

    # 截断 overview.json 到匹配检查点（含）
    truncated = [cp for cp in checkpoints if cp.get("to_turn", 0) <= target_turn]
    overview_json_path.write_text(
        json.dumps(truncated, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return True
