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
    """一个上下文片段：来源类型、内容、token 估算、相关性评分。"""
    type: str           # "soul" | "tools" | "skill" | "memory" | "overview" | "timeline"
    content: str
    tokens: int = 0     # 估算 token 数（构造时计算）
    score: float = 0.0  # 与当前用户消息的相关性 (0-1)
    pinned: bool = False  # 始终包含，不参与排序
    metadata: dict = None  # 额外元数据（如 entry_id）

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.tokens == 0 and self.content:
            self.tokens = max(estimate_tokens(self.content), 1)


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
                 feedback_store=None) -> None:
        self._cache: dict[str, str] = {}
        self._agent_root = agent_root or (aide_dir() / "agent")
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
        self._vocab_index = _build_vocabulary(self._agent_root)

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

        # 保存本轮 memory 片段供 FeedbackVerifier 使用
        self._last_memory_fragments = [
            f for f in fragments if f.type == "memory" and f.score > self.relevance_threshold
        ]

        # Step 2: 评分
        fragments = await self._score_fragments(fragments, user_msg)

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
                            type="skill", content=injection, score=0.6,
                        ))
                except Exception:
                    logger.debug("Context provider failed, skipping", exc_info=True)

        # 记忆文件（结构化解析，兼容 frontmatter + 旧格式）
        for fname in MEMORY_FILES.values():
            prompt_text = await self._read_cached(self._agent_root /fname)
            if not prompt_text:
                continue
            file_path = self._agent_root / fname
            decay = _decay_factor(file_path)

            entries = parse_memory_file(prompt_text, filename=fname)
            for entry in entries:
                if not entry.content:
                    continue
                score = _decay_factor(file_path) * 0.5  # 基础分 × 衰减
                # frontmatter 中的 weight 叠加
                if entry.has_meta and entry.weight != 1.0:
                    score *= entry.weight
                meta = {}
                if entry.id:
                    meta["entry_id"] = entry.id
                fragments.append(ContextFragment(
                    type="memory", content=entry.content, score=score,
                    metadata=meta,
                ))

        # 会话总览（overview.json 最后一条检查点，兼容旧 overview.md）
        if session_dir is not None:
            from core.context.overview import read_current_overview
            overview = await asyncio.to_thread(read_current_overview, session_dir)
            if overview:
                fragments.append(ContextFragment(
                    type="overview",
                    content=t("ctx.session_overview") + "\n" + overview,
                    score=0.4,  # 中等相关性
                ))

            # 早期轮次总览
            if older:
                overview_text = _build_overview(session_dir, older)
                if overview_text:
                    fragments.append(ContextFragment(
                        type="timeline", content=overview_text, score=0.25,
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
                                type="timeline", content=timeline_text, score=0.2,
                            ))
                    except (OSError, json.JSONDecodeError):
                        logger.debug("Failed to read timeline.json, skipping")

        return fragments

    def _compute_memory_score(self, content: str, fname: str, decay: float) -> float:
        """计算单条记忆的初始评分（不含 query 相关性）——仅含时间衰减。"""
        return decay * 0.5  # 基础分 × 衰减

    # ── 评分 ────────────────────────────────────────────────────────

    async def _score_fragments(self, fragments: list[ContextFragment],
                         user_msg: str) -> list[ContextFragment]:
        """用 TF-IDF/Jaccard 对非 pinned 片段评分。

        Pinned 片段保持 score=1.0，不参与评分。
        记忆片段在已有时间衰减的基础上叠加内容相关性。
        """
        if not user_msg.strip():
            return fragments

        # 构建词汇索引（I/O 在 thread pool 中执行）
        self._vocab_index = await asyncio.to_thread(
            _build_vocabulary, self._agent_root,
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

            # TF-IDF or Jaccard fallback
            if self._vocab_index.built and self._vocab_index.N > 1:
                content_score = _tfidf_score(
                    user_tokens_expanded, para_word_tokens,
                    df=self._vocab_index.df, N=self._vocab_index.N,
                )
            else:
                para_bigrams = _bigrams(frag.content)
                content_score = _jaccard(user_char_bigrams, para_bigrams)

            # 叠加内容相关性到已有基础分（时间衰减）
            if frag.type == "memory":
                frag.score *= (1.0 + content_score)
                # 反馈闭环：偏离过的约束提权（优先用 stable id）
                if self._feedback_store:
                    entry_id = (frag.metadata or {}).get("entry_id", "")
                    fw = self._feedback_store.get_weight(frag.content, entry_id=entry_id)
                    if fw != 1.0:
                        frag.score *= fw
            else:
                frag.score += content_score * 0.5

        return fragments

    # ── 打包 ────────────────────────────────────────────────────────

    def _pack_fragments(self, fragments: list[ContextFragment],
                        token_budget: int) -> str:
        """按分数降序排列，pinned 置顶，填满 token 预算。"""
        # 分离 pinned 和 scored
        pinned = [f for f in fragments if f.pinned]
        scored = sorted(
            [f for f in fragments if not f.pinned],
            key=lambda f: f.score, reverse=True,
        )

        # pinned 优先
        used = 0
        parts: list[str] = []

        for frag in pinned:
            parts.append(frag.content)
            used += frag.tokens

        # 按分数填充 scored，不超过预算
        for frag in scored:
            if frag.score < self.relevance_threshold:
                continue  # 低于阈值的不注入
            if used + frag.tokens > token_budget:
                continue  # 超出预算
            parts.append(frag.content)
            used += frag.tokens

        return "\n\n".join(parts)

    def get_last_memory_fragments(self) -> list:
        """返回本轮上下文注入的记忆片段（供 FeedbackVerifier 使用）。"""
        return self._last_memory_fragments
