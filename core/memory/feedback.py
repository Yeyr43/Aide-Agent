"""FeedbackVerifier + FeedbackStore — 反馈闭环引擎。

每轮对话后验证 AI 响应是否遵循了注入的记忆约束。
偏离的约束自动提权，让下轮注入时排序更靠前。
零 LLM 调用，纯规则启发式检查，<10ms。

用法:
    store = FeedbackStore(agent_root)
    verifier = FeedbackVerifier(store)
    # 每轮末尾:
    verifier.verify(fragments, assistant_text, user_msg, turn_messages, session_id, turn)
    # Pipeline 评分时叠加:
    weight = store.get_weight(fragment_content)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from core.context.tokenizer import _detect_language
from core.storage import atomic_write_json

logger = logging.getLogger(__name__)

# ── 约束提取正则 ────────────────────────────────────────────────────────

# 语言偏好
_LANG_ZH_RE = re.compile(
    r'(用中文|中文回答|回复用中文|请说中文|中文回复|写中文|'
    r'用汉语|汉语回答|说中文|用简体|繁体中文)'
)
_LANG_EN_RE = re.compile(
    r'(in English|reply in English|use English|answer in English|'
    r'speak English|write in English|English only|prefer English)'
)

# 长度/风格偏好
_CONCISE_RE = re.compile(
    r'(简洁|简短|简明|简练|简略|扼要|言简意赅|精炼|别废话|少说|'
    r'不要长篇大论|别啰嗦|别太啰嗦|少写点|精简|'
    r'concise|brief|short|terse|succinct|'
    r'not too long|don\'t be verbose|keep it short|be brief|'
    r'to the point|no fluff|no filler|cut the fluff)'
)
_DETAILED_RE = re.compile(
    r'(详细|详尽|具体|细致|仔细|深入|全面|完整|'
    r'多说一点|多写一点|多写点|充分|'
    r'detailed|thorough|elaborate|in depth|comprehensive|'
    r'explain fully|go into detail|be specific|expand)'
)


# ── FeedbackStore ───────────────────────────────────────────────────────


class FeedbackStore:
    """反馈权重存储 — 跨会话持久化到 agent/.feedback.json。

    constraint_key: sha1(content[:80])[:12]，用于去重匹配。
    """

    def __init__(self, agent_root: Path | None = None) -> None:
        from core.setup import aide_dir
        self._agent_root = agent_root or (aide_dir() / "agent")
        self._path = self._agent_root / ".feedback.json"
        self._data: dict[str, dict] = {}
        self.load()

    def get_weight(self, text: str, entry_id: str = "") -> float:
        """获取约束的当前权重。优先用 entry_id，fallback 到 content hash。"""
        key = self._resolve_key(text, entry_id)
        entry = self._data.get(key)
        return entry.get("weight", 1.0) if entry else 1.0

    def record(self, text: str, constraint_type: str,
               compliant: bool, session_id: str = "",
               turn: int = 0, entry_id: str = "") -> float:
        """记录一次验证结果，返回新权重。

        - 偏离 (compliant=False): weight *= 1.5（提权）
        - 遵循 (compliant=True): weight = max(weight * 0.95, 1.0)（缓慢衰减）
        """
        key = self._resolve_key(text, entry_id)
        if key not in self._data:
            self._data[key] = {
                "weight": 1.0,
                "deviations": 0,
                "type": constraint_type,
                "text_preview": text[:60],
                "last_session": session_id,
                "last_turn": turn,
            }
            # 如果有 stable id，记录它
            if entry_id:
                self._data[key]["id"] = entry_id

        entry = self._data[key]
        if compliant:
            entry["weight"] = max(entry["weight"] * 0.95, 1.0)
        else:
            entry["weight"] *= 1.5
            entry["deviations"] += 1
        entry["last_turn"] = turn
        if session_id:
            entry["last_session"] = session_id

        return entry["weight"]

    def load(self) -> None:
        """从磁盘加载权重数据。"""
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def save(self) -> None:
        """原子写入权重数据到磁盘。"""
        atomic_write_json(self._path, self._data)

    @staticmethod
    def _resolve_key(text: str, entry_id: str = "") -> str:
        """优先用 stable id，fallback 到内容 hash。"""
        if entry_id:
            return entry_id
        return hashlib.sha1(text[:80].encode()).hexdigest()[:12]


# ── FeedbackVerifier ────────────────────────────────────────────────────


class FeedbackVerifier:
    """反馈验证器 — 每轮对话后运行，检查 AI 响应是否遵循了记忆约束。

    L1: 语言检测 — "用中文" vs 实际输出语言
    L2: 长度/风格 — "简洁" / "详细" vs 实际输出 token 数
    """

    # 简洁阈值：超出此 token 数视为偏离"简洁"约束
    CONCISE_MAX_TOKENS = 300
    # 详细阈值：低于此 token 数视为偏离"详细"约束
    DETAILED_MIN_TOKENS = 100

    def __init__(self, store: FeedbackStore) -> None:
        self._store = store

    def verify(
        self,
        fragments: list,
        assistant_text: str,
        user_msg: str = "",
        turn_messages: list | None = None,
        session_id: str = "",
        turn: int = 0,
    ) -> None:
        """对每条记忆 fragment 运行约束验证。

        Args:
            fragments: ContextFragment 列表（本轮被注入的 memory 类型片段）
            assistant_text: LLM 的最终文本响应
            user_msg: 用户消息（备用）
            turn_messages: 本轮所有 FC 循环消息（备用）
            session_id: 当前会话 ID
            turn: 当前轮次
        """
        if not assistant_text.strip():
            return

        for frag in fragments:
            content = getattr(frag, 'content', str(frag)) if not isinstance(frag, str) else frag
            if not content:
                continue
            # 提取结构化 entry id（优先用 stable id）
            entry_id = ""
            frag_meta = getattr(frag, 'metadata', None) or {}
            if isinstance(frag_meta, dict):
                entry_id = frag_meta.get("entry_id", "")

            lang_constraint = self._extract_language_constraint(content)
            if lang_constraint:
                self._verify_language(content, assistant_text, lang_constraint, session_id, turn, entry_id)

            length_constraint = self._extract_length_constraint(content)
            if length_constraint:
                self._verify_length(content, assistant_text, length_constraint, session_id, turn, entry_id)

        # 验证后落盘
        self._store.save()
        logger.debug(f"反馈验证完成: turn={turn}")

    # ── L1: 语言验证 ──────────────────────────────────────────────────

    @staticmethod
    def _extract_language_constraint(text: str) -> str | None:
        """从约束文本中提取目标语言。返回 "zh" / "en" / None。"""
        if _LANG_ZH_RE.search(text):
            return "zh"
        if _LANG_EN_RE.search(text):
            return "en"
        return None

    def _verify_language(self, constraint: str, response: str,
                         expected_lang: str, session_id: str, turn: int,
                         entry_id: str = "") -> None:
        actual_lang = _detect_language(response)
        compliant = (actual_lang == expected_lang)
        new_weight = self._store.record(
            constraint, "language", compliant, session_id, turn, entry_id=entry_id,
        )
        if not compliant:
            logger.debug(
                f"语言偏离: 期望={expected_lang} 实际={actual_lang} "
                f"约束={constraint[:40]}... 新权重={new_weight:.2f}"
            )

    # ── L2: 长度/风格验证 ─────────────────────────────────────────────

    @staticmethod
    def _extract_length_constraint(text: str) -> str | None:
        """从约束文本中提取长度偏好。返回 "concise" / "detailed" / None。"""
        if _CONCISE_RE.search(text):
            return "concise"
        if _DETAILED_RE.search(text):
            return "detailed"
        return None

    def _verify_length(self, constraint: str, response: str,
                       style: str, session_id: str, turn: int,
                       entry_id: str = "") -> None:
        from core.context.token_counter import estimate_tokens
        token_count = estimate_tokens(response)

        if style == "concise":
            compliant = token_count <= self.CONCISE_MAX_TOKENS
        else:  # detailed
            compliant = token_count >= self.DETAILED_MIN_TOKENS

        new_weight = self._store.record(
            constraint, f"length_{style}", compliant, session_id, turn, entry_id=entry_id,
        )
        if not compliant:
            logger.debug(
                f"长度偏离: style={style} tokens={token_count} "
                f"约束={constraint[:40]}... 新权重={new_weight:.2f}"
            )
