"""会话总览生成 + 对话切分子系统。

从 relevance.py 拆分：话题提取、决策检测、历史总览、轮次切分。
P5: parse_overview_md + restore_overview_from_checkpoint 从 compactor.py 移植至此。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from core.storage import atomic_write_json

from .tokenizer import _ZH_STOP_WORDS

WINDOW_TURNS = 8

DECISION_KEYWORDS = re.compile(
    r'(确定|决定|选择|采用|最终|结论是|方案是|就用|还是用|'
    r'建议|推荐|修改了|创建了|删除了|更新了)'
)


def _extract_topics(text: str, max_topics: int = 3) -> list[str]:
    """从文本中提取关键词作为话题。

    策略：
    1. 取所有 2-4 字片段
    2. 过滤停用词
    3. 按频率排序取 top N
    """
    if not text.strip():
        return []

    fragments: list[str] = []
    for n in [2, 3, 4]:
        for i in range(len(text) - n + 1):
            frag = text[i:i + n]
            if not re.fullmatch(r'[一-鿿]+', frag):
                continue
            if frag in _ZH_STOP_WORDS:
                continue
            fragments.append(frag)

    if not fragments:
        return []

    counter = Counter(fragments)
    candidates: list[tuple[str, int]] = []
    for frag, count in counter.most_common(30):
        if len(frag) >= 3 and count >= 1:
            candidates.append((frag, count))
        elif len(frag) == 2 and count >= 2:
            candidates.append((frag, count))

    seen: list[str] = []
    for frag, _count in candidates:
        if any(frag in s and frag != s for s in seen):
            continue
        seen.append(frag)
        if len(seen) >= max_topics:
            break

    return seen


def _extract_decisions(text: str) -> list[str]:
    """从助手回复中提取决策/产出句子。"""
    sentences = re.split(r'[。！？\n]+', text)
    decisions: list[str] = []
    for s in sentences:
        s = s.strip()
        if not s or len(s) < 6 or len(s) > 60:
            continue
        if DECISION_KEYWORDS.search(s):
            decisions.append(s)

    if len(decisions) > 2:
        decisions = decisions[:2]
    return decisions


def _build_overview(
    session_dir: Path | None,
    older_conversation: list[dict],
) -> str:
    """规则驱动：从早期轮次生成一句总览。

    格式："此前讨论了 A、B。期间确定：C。"
    """
    def _extract_text(content: str | list) -> str:
        if isinstance(content, list):
            return " ".join(p.get("text", "") for p in content if p.get("type") == "text")
        return content or ""

    user_texts = [
        _extract_text(m.get("content", ""))
        for m in older_conversation if m.get("role") == "user"
    ]
    assistant_texts = [
        _extract_text(m.get("content", ""))
        for m in older_conversation if m.get("role") == "assistant"
    ]

    all_user = " ".join(user_texts)
    all_assistant = " ".join(assistant_texts)

    topics = _extract_topics(all_user)
    decisions = _extract_decisions(all_assistant)

    if not topics and not decisions:
        if user_texts:
            first = user_texts[0][:30]
            return f"[历史] {first}..."
        return ""

    parts: list[str] = []
    if topics:
        parts.append(f"此前讨论了{'、'.join(topics)}")
    if decisions:
        parts.append(f"期间确定：{'；'.join(decisions)}")

    return "。" .join(parts) + "。"


def _split_conversation(
    conversation: list[dict],
    window: int = WINDOW_TURNS,
) -> tuple[list[dict], list[dict]]:
    """按轮次切分对话历史。

    "一轮" = 一条 user 消息 + 后续 assistant 消息（含 tool_calls）。

    Returns:
        (older_messages, recent_messages)
    """
    user_indices = [
        i for i, m in enumerate(conversation)
        if m.get("role") == "user"
    ]

    if len(user_indices) <= window:
        return [], conversation

    cutoff = user_indices[-window]
    return conversation[:cutoff], conversation[cutoff:]


# ── overview.md 解析 + 检查点还原（从 compactor.py 移植）───────────────


def split_sections(text: str) -> dict[str, list[str]]:
    """将 Markdown 按 ## 标题分割为 {标题: 原始行列表}。

    公共原语（统一 reflector / auto / overview 三处同款解析）：
    保留每个标题下的原始行（含空行），由调用方决定如何从内容行提取结构。

    Returns:
        dict like {"话题": ["- ...", ""], ...}（重复标题合并到同一 key）
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip()
            if current not in sections:
                sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def parse_overview_md(text: str) -> dict[str, list[str]]:
    """解析 overview.md 为结构化 sections。

    将 Markdown 的 ## 标题映射为 section key，其下的 - 列表项为值。

    Returns:
        dict like {"话题": [...], "用户偏好": [...], "纠正记录": [...], "决策与结论": [...]}
    """
    sections: dict[str, list[str]] = {}
    for title, lines in split_sections(text).items():
        items = [
            ln.strip()[2:].strip()
            for ln in lines if ln.strip().startswith("- ")
        ]
        sections[title] = items
    return sections


def read_current_overview(session_dir: Path) -> str:
    """读取当前生效的会话总览。

    单一来源：overview.json 检查点的最后一条 overview_md。
    兼容旧格式：overview.json 不存在时回退读 overview.md（迁移期残留）。

    Returns:
        当前总览 markdown 文本，无则为 ""
    """
    overview_json_path = session_dir / "overview.json"
    if overview_json_path.exists():
        try:
            from core.storage import read_jsonl
            checkpoints: list[dict] = read_jsonl(overview_json_path)
        except (json.JSONDecodeError, OSError, ValueError):
            checkpoints = []
        if checkpoints:
            return checkpoints[-1].get("overview_md", "")

    # 兼容旧格式 overview.md
    overview_md_path = session_dir / "overview.md"
    if overview_md_path.exists():
        try:
            return overview_md_path.read_text(encoding="utf-8")
        except OSError:
            pass
    return ""


def restore_overview_from_checkpoint(session_dir: Path, target_turn: int) -> bool:
    """回滚时：截断 overview.json 到匹配检查点。

    当前生效版 = overview.json 最后一条检查点的 overview_md，截断后
    即还原到回滚目标轮次，无需单独维护 overview.md。

    Args:
        session_dir: 会话目录
        target_turn: 回滚目标轮次

    Returns:
        True 如果成功还原，False 如果无匹配检查点
    """
    overview_json_path = session_dir / "overview.json"

    if not overview_json_path.exists():
        return False

    try:
        from core.storage import read_jsonl
        checkpoints: list[dict] = read_jsonl(overview_json_path)
    except (json.JSONDecodeError, OSError, ValueError):
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
        # 没有检查点覆盖到 target_turn → 清空 overview.json
        atomic_write_json(overview_json_path, [])
        return False

    # 截断 overview.json 到匹配检查点（含）—— 当前生效版随之还原
    truncated = [cp for cp in checkpoints if cp.get("to_turn", 0) <= target_turn]
    atomic_write_json(overview_json_path, truncated)

    return True
