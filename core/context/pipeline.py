"""ContextPipeline — 上下文优先级队列。

不再使用固定六层拼接，而是：
  1. 收集所有候选上下文片段（Soul、Tools、技能、记忆段落、overview、timeline）
  2. 按与当前用户消息的相关性评分排序
  3. 按 token 预算填充 —— 相关的浮上来，无关的沉下去

Pinned（始终保留）：Soul、Tools Prompt
Scored（按相关性排序）：记忆条目、技能注入、会话总览、timeline

P5: 切换为优先级队列模型，消除固定层级假设。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.locale import build_tools_prompt, t
from core.memory import MEMORY_FILES
from core.memory.entries import parse_memory_file
from core.setup import aide_dir

from .tokenizer import (
    VocabularyIndex, _build_vocabulary, _tokenize, _jaccard, _bigrams,
    _tfidf_score, _decay_factor, _expand_query, flush_vocab_cache,
)
from .overview import _build_overview, _split_conversation
from .token_counter import estimate_tokens

logger = logging.getLogger(__name__)


# ── ContextFragment ────────────────────────────────────────────────────


@dataclass
class ContextFragment:
    """一个上下文片段：来源类型、内容、token 估算、相关性评分。

    score 是排序键（relevance × recency × feedback）；relevance 决定
    是否进入候选（内容相关性 × 类型权重），衰减/反馈只调优先级。
    """
    type: str           # "soul" | "tools" | "skill" | "memory" | "overview" | "timeline"
    content: str
    tokens: int = 0     # 估算 token 数（构造时计算）
    score: float = 0.0  # 排序键 = relevance × recency × feedback
    relevance: float = 0.0  # 内容相关性 × 类型权重（决定是否注入）
    recency: float = 1.0    # 时间衰减（仅 memory 有意义）
    pinned: bool = False  # 始终包含，不参与排序
    metadata: dict = None  # 额外元数据（如 entry_id）

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.tokens == 0 and self.content:
            self.tokens = max(estimate_tokens(self.content), 1)


# ── 评分常量 ─────────────────────────────────────────────────────────

# 来源类型权重：决定各来源"同样相关时谁更优先"（替代旧的魔法基础分）
TYPE_BASE = {
    "memory": 1.0,     # 记忆是核心个性化上下文
    "overview": 0.8,   # 会话总览
    "skill": 0.7,      # 技能主动注入
    "history": 0.7,    # 相关早期轮次完整回填
    "timeline": 0.5,   # 轮次摘要
}
# 不参与时间衰减的记忆文件（长期记忆语义上永不过期）
NON_DECAY_FILES = {"long_term_memory.md"}
DEFAULT_DECAY_DAYS = 30
# overview/timeline 是本会话背景，总有基础注入分（内容更相关时更靠前）。
# 与记忆不同——它们天然是"当前会话的上下文"，不应被全局相关性严格过滤。
FRAGMENT_BASE_RELEVANCE = {"overview": 0.6, "timeline": 0.4}
# 历史回填：最多取最近 N 个 early 轮次的完整内容进候选（评分/预算再筛）
HISTORY_MAX_TURNS = 10


def _msg_content_text(content) -> str:
    """提取消息 content 的纯文本（兼容 str 与多模态 list）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def _split_turns(messages: list[dict]) -> list[list[dict]]:
    """把扁平消息列表切成轮次（每个 user 消息开启一轮）。"""
    turns: list[list[dict]] = []
    current: list[dict] = []
    for m in messages:
        if m.get("role") == "user" and current:
            turns.append(current)
            current = []
        current.append(m)
    if current:
        turns.append(current)
    return turns


def _turn_text(messages: list[dict]) -> str:
    """轮次纯文本（user/assistant 消息，跳过 tool 细节）。"""
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "")
        if role not in ("user", "assistant"):
            continue
        text = _msg_content_text(m.get("content", ""))
        if text:
            parts.append(text)
    return "\n".join(parts)


def _entry_decay(entry, file_path: Path) -> float:
    """记忆条目时间衰减：按条目 created（frontmatter），而非整个文件 mtime。

    长期记忆（long_term_memory.md）不衰减。无 created 的旧条目回退文件 mtime。
    """
    if file_path.name in NON_DECAY_FILES:
        return 1.0
    created = getattr(entry, "created", "") or ""
    if created:
        try:
            d = datetime.strptime(created[:10], "%Y-%m-%d").date()
            age_days = (datetime.now().date() - d).days
            if age_days > 0:
                return 0.5 ** (age_days / DEFAULT_DECAY_DAYS)
        except ValueError:
            pass
    return _decay_factor(file_path)


# ── ContextPipeline ──────────────────────────────────────────────────


class ContextPipeline:
    """上下文优先级队列 — 按任务相关性动态排序，token 预算驱动。

    用法:
        pipeline = ContextPipeline(agent_root=config.aide_root / "agent")
        system_msgs, trimmed_conv = await pipeline.assemble(
            session_dir, user_msg, conversation
        )
        full = system_msgs + trimmed_conv
        updated = await executor.run(full, ui=app)
    """

    # 相关性阈值
    RELEVANCE_THRESHOLD = 0.15
    # system 上下文占总窗口的比例
    SYSTEM_BUDGET_RATIO = 0.40
    # 默认上下文窗口
    DEFAULT_CONTEXT_WINDOW = 128000

    def __init__(self, agent_root: Path | None = None,
                 full_text_turns: int = 3,
                 summary_turns: int = 15,
                 relevance_threshold: float = 0.15,
                 context_window: int = 128000,
                 feedback_store=None,
                 sessions_root: Path | None = None) -> None:
        self._cache: dict[str, str] = {}
        self._agent_root = agent_root or (aide_dir() / "agent")
        self._sessions_root = sessions_root  # 词汇表补充的会话摘要来源
        self.full_text_turns = full_text_turns
        self.summary_turns = summary_turns
        self.relevance_threshold = relevance_threshold
        self.context_window = context_window
        self._vocab_index = VocabularyIndex()
        self._feedback_store = feedback_store   # FeedbackStore | None
        self._last_memory_fragments: list = []  # 本轮注入的记忆片段

    # ── 缓存管理 ──────────────────────────────────────────────────

    async def _read_cached(self, path: Path) -> str:
        key = str(path)
        if key not in self._cache:
            try:
                self._cache[key] = await asyncio.to_thread(
                    path.read_text, encoding="utf-8",
                )
            except OSError:
                self._cache[key] = ""
        return self._cache[key]

    def flush_cache(self) -> None:
        """刷新内存缓存 + 词汇索引（/reflect 后调用）。"""
        self._cache.clear()
        flush_vocab_cache()
        self._vocab_index = _build_vocabulary(self._agent_root, self._sessions_root)

    # ── 组装 ────────────────────────────────────────────────────────

    async def assemble(
        self,
        session_dir: Path | None,
        user_msg: str,
        conversation: list[dict] | None = None,
        context_providers: list | None = None,
        tool_descriptions: list[str] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """组装上下文为 (system_messages, trimmed_conversation)。

        三步流程：收集 → 评分 → 打包。
        """
        conv = conversation or []
        older, recent = _split_conversation(conv, window=self.full_text_turns)

        # Step 1: 收集所有候选片段
        fragments = await self._collect_fragments(
            session_dir, older, recent, context_providers, tool_descriptions,
        )

        # Step 2: 评分（内容相关性为主序）
        fragments = await self._score_fragments(fragments, user_msg)

        # 评分后收集本轮 memory 片段（与打包一致的过滤标准，供 FeedbackVerifier 使用）
        self._last_memory_fragments = [
            f for f in fragments
            if f.type == "memory" and f.relevance >= self.relevance_threshold
        ]

        # Step 3: 打包（pinned 置顶，按分数填充 token 预算）
        token_budget = int(self.context_window * self.SYSTEM_BUDGET_RATIO)
        combined_system = self._pack_fragments(fragments, token_budget)

        messages: list[dict] = []
        if combined_system:
            messages.append({"role": "system", "content": combined_system})

        return messages, recent

    # ── 收集 ────────────────────────────────────────────────────────

    async def _collect_fragments(
        self,
        session_dir: Path | None,
        older: list[dict],
        recent: list[dict],
        context_providers: list | None,
        tool_descriptions: list[str] | None,
    ) -> list[ContextFragment]:
        """收集所有候选上下文片段。"""
        fragments: list[ContextFragment] = []

        # Soul（pinned）
        soul = await self._read_cached(self._agent_root /"soul.md")
        if soul:
            fragments.append(ContextFragment(
                type="soul", content=soul, pinned=True,
            ))

        # Tools Prompt（pinned）
        tools_prompt = build_tools_prompt(tool_descriptions)
        fragments.append(ContextFragment(
            type="tools", content=tools_prompt, pinned=True,
        ))

        # 技能上下文（pinned per provider — 技能主动声明了相关性）
        if context_providers:
            for provider in context_providers:
                try:
                    injection = await provider.provide("", session_dir)
                    if injection:
                        fragments.append(ContextFragment(
                            type="skill", content=injection,
                        ))
                except Exception:
                    logger.debug("Context provider failed, skipping", exc_info=True)

        # 记忆文件（结构化解析，兼容 frontmatter + 旧格式）
        for fname in MEMORY_FILES.values():
            prompt_text = await self._read_cached(self._agent_root /fname)
            if not prompt_text:
                continue
            file_path = self._agent_root / fname

            entries = parse_memory_file(prompt_text, filename=fname)
            for entry in entries:
                if not entry.content:
                    continue
                # 时间衰减：按条目 created（长记忆不衰减），无 created 回退文件 mtime
                recency = _entry_decay(entry, file_path)
                meta = {}
                if entry.id:
                    meta["entry_id"] = entry.id
                fragments.append(ContextFragment(
                    type="memory", content=entry.content,
                    recency=recency, metadata=meta,
                ))

        # 会话总览（overview.json 最后一条检查点，兼容旧 overview.md）
        if session_dir is not None:
            from core.context.overview import read_current_overview
            overview = await asyncio.to_thread(read_current_overview, session_dir)
            if overview:
                fragments.append(ContextFragment(
                    type="overview",
                    content=t("ctx.session_overview") + "\n" + overview,
                ))

            # 早期轮次总览
            if older:
                overview_text = _build_overview(session_dir, older)
                if overview_text:
                    fragments.append(ContextFragment(
                        type="timeline", content=overview_text,
                    ))

                # 方向 2：相关早期轮次完整内容回填（评分/预算再筛，只注入相关的）
                for turn_msgs in _split_turns(older)[-HISTORY_MAX_TURNS:]:
                    text = _turn_text(turn_msgs)
                    if text:
                        fragments.append(ContextFragment(
                            type="history", content=text,
                        ))

            # Timeline 摘要
            if recent:
                timeline_path = session_dir / "timeline.json"
                if timeline_path.exists():
                    try:
                        from core.storage import read_jsonl
                        entries = read_jsonl(timeline_path)
                        total = self.full_text_turns + self.summary_turns
                        recent_entries = entries[-total:]
                        summaries = [
                            f"- [{e.get('turn', '?')}] {e.get('summary', '')}"
                            for e in recent_entries
                        ]
                        if summaries:
                            timeline_text = t("ctx.recent_chat") + "\n" + "\n".join(summaries)
                            fragments.append(ContextFragment(
                                type="timeline", content=timeline_text,
                            ))
                    except (OSError, json.JSONDecodeError):
                        logger.debug("Failed to read timeline.json, skipping")

        return fragments

    # ── 评分 ────────────────────────────────────────────────────────

    async def _score_fragments(self, fragments: list[ContextFragment],
                         user_msg: str) -> list[ContextFragment]:
        """用 TF-IDF/Jaccard 对非 pinned 片段评分。

        评分公式（内容相关性为主序）：
          relevance = content_corr × type_weight   # 决定是否注入
          score     = relevance × recency × feedback  # 决定注入顺序

        衰减/反馈只调优先级，不决定"是否注入"——不相关的即便再新
        也不会注入，相关的即便很久前也会进入候选。
        """
        if not user_msg.strip():
            return fragments

        # 构建词汇索引（I/O 在 thread pool 中执行）
        self._vocab_index = await asyncio.to_thread(
            _build_vocabulary, self._agent_root, self._sessions_root,
        )
        user_word_tokens, user_char_bigrams = _tokenize(
            user_msg, vocab=self._vocab_index.vocab,
        )
        expanded = _expand_query(user_msg)
        user_tokens_expanded = user_word_tokens | {
            t for t in expanded if len(t) >= 2
        }

        for frag in fragments:
            if frag.pinned:
                frag.score = 1.0
                continue

            para_word_tokens, _ = _tokenize(
                frag.content, vocab=self._vocab_index.vocab,
            )

            # 内容相关性（主排序键）。词级 TF-IDF 优先（N=1 也走词级，
            # 否则 char bigram Jaccard 对短文本过于苛刻）。
            if self._vocab_index.built and self._vocab_index.vocab:
                content_corr = _tfidf_score(
                    user_tokens_expanded, para_word_tokens,
                    df=self._vocab_index.df, N=self._vocab_index.N,
                )
            else:
                content_corr = _jaccard(user_char_bigrams, _bigrams(frag.content))

            type_weight = TYPE_BASE.get(frag.type, 0.5)
            # overview/timeline 有基础注入分（会话背景），其余按纯内容相关性
            floor = FRAGMENT_BASE_RELEVANCE.get(frag.type, 0.0)
            frag.relevance = max(content_corr, floor) * type_weight

            # 排序键：衰减（memory）/ 反馈（memory）只调优先级
            recency = frag.recency
            feedback = 1.0
            if frag.type == "memory" and self._feedback_store:
                entry_id = (frag.metadata or {}).get("entry_id", "")
                fw = self._feedback_store.get_weight(frag.content, entry_id=entry_id)
                if fw != 1.0:
                    feedback = fw
            frag.score = frag.relevance * recency * feedback

        return fragments

    # ── 打包 ────────────────────────────────────────────────────────

    def _pack_fragments(self, fragments: list[ContextFragment],
                        token_budget: int) -> str:
        """按 score 降序排列，pinned 置顶，填满 token 预算。

        过滤看 relevance（内容相关性 × 类型权重，不含衰减），
        同分时短片段优先（token 效率）。
        """
        # 分离 pinned 和 scored
        pinned = [f for f in fragments if f.pinned]
        scored = sorted(
            [f for f in fragments if not f.pinned],
            key=lambda f: (f.score, -f.tokens), reverse=True,
        )

        # pinned 优先
        used = 0
        parts: list[str] = []

        for frag in pinned:
            parts.append(frag.content)
            used += frag.tokens

        # 按分数填充 scored，不超过预算
        for frag in scored:
            if frag.relevance < self.relevance_threshold:
                continue  # 低于相关性阈值的注入候选不注入
            if used + frag.tokens > token_budget:
                continue  # 超出预算
            parts.append(frag.content)
            used += frag.tokens

        return "\n\n".join(parts)

    def get_last_memory_fragments(self) -> list:
        """返回本轮上下文注入的记忆片段（供 FeedbackVerifier 使用）。"""
        return self._last_memory_fragments
