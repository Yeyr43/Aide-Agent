"""ContextPipeline — 组装六层上下文为 LLM messages 列表。

六层：
  1. Soul（agent/soul.md）— 始终首条 system 消息
  2. Tools Prompt（不可变常量）— 工具列表 + 使用策略
  3. 技能上下文（插件 ContextProvider）— 按需注入
  4. 动态 prompt（agent/*.md）— Jaccard 相关性过滤
  5. 会话总览（overview.md）— 有则注入
  6. 窗口上下文（timeline.json）— 近 N 轮全文 + 扩展 M 轮摘要索引

分层窗口策略：近 3 轮保留全文（连续操作上下文），
额外 15 轮注入 timeline 摘要（跨轮记忆），更早轮次由 overview.md 覆盖。
"""

import json
import logging
from pathlib import Path

from core.locale import build_tools_prompt, t
from core.setup import aide_dir

from .embeddings import get_embedding_engine
from .relevance import (
    _bigrams, _jaccard, _tokenize, _tfidf_score,
    _extract_topics, _extract_decisions,
    _build_overview, _split_conversation,
    _build_vocabulary, _vocab_index, flush_vocab_cache,
    _decay_factor,
)

logger = logging.getLogger(__name__)


# ── ContextPipeline ──────────────────────────────────────────────────


class ContextPipeline:
    """组装上下文，分层窗口：近 N 轮全文 + 扩展 M 轮摘要索引。

    用法:
        pipeline = ContextPipeline(agent_root=config.aide_root / "agent")
        system_msgs, trimmed_conv = await pipeline.assemble(
            session_dir, user_msg, conversation
        )
        full = system_msgs + trimmed_conv
        updated = await executor.run(full, ui=app)
    """

    # 相关性阈值（可通过构造参数覆盖）
    RELEVANCE_THRESHOLD = 0.15

    def __init__(self, agent_root: Path | None = None,
                 full_text_turns: int = 3,
                 summary_turns: int = 15,
                 relevance_threshold: float = 0.15) -> None:
        # 内存缓存
        self._cache: dict[str, str] = {}  # path → content
        self._agent_root = agent_root or (aide_dir() / "agent")
        self.full_text_turns = full_text_turns
        self.summary_turns = summary_turns
        self.relevance_threshold = relevance_threshold

    # ── 缓存管理 ──────────────────────────────────────────────────

    def _read_cached(self, path: Path) -> str:
        """读取文件，优先使用内存缓存。"""
        key = str(path)
        if key not in self._cache:
            try:
                self._cache[key] = path.read_text(encoding="utf-8")
            except OSError:
                self._cache[key] = ""
        return self._cache[key]

    def flush_cache(self) -> None:
        """刷新内存缓存 + 词汇索引（/profile update 后调用）。"""
        self._cache.clear()
        flush_vocab_cache()

    # ── 组装 ────────────────────────────────────────────────────────

    async def assemble(
        self,
        session_dir: Path | None,
        user_msg: str,
        conversation: list[dict] | None = None,
        context_providers: list | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """组装上下文为 (system_messages, trimmed_conversation)。

        Args:
            session_dir: 当前 session 目录（可能为 None）
            user_msg: 当前用户消息
            conversation: 完整对话历史（含当前轮之前的所有消息）
            context_providers: 插件/技能注册的 ContextProvider 列表

        Returns:
            (system_messages, trimmed_conversation)
            - system_messages: LLM system 消息列表
            - trimmed_conversation: 裁剪后的对话（最近 full_text_turns 轮全文）
        """
        system_parts: list[str] = []
        conv = conversation or []

        # ── 切分对话：近 N 轮全文 vs 早期轮次 ──
        older, recent = _split_conversation(conv, window=self.full_text_turns)

        # ── 第 1 层：Soul ──
        soul = self._read_cached(self._agent_root / "soul.md")
        if soul:
            system_parts.append(soul)

        # ── 第 1b 层：Tools Prompt（不可变）──
        system_parts.append(build_tools_prompt())

        # ── 1.5 层：技能/插件上下文（来自已加载的 Skills/Python 插件） ──
        if context_providers:
            for provider in context_providers:
                try:
                    injection = await provider.provide(user_msg, session_dir)
                    if injection:
                        system_parts.append(injection)
                except Exception:
                    logger.debug("Context provider failed, skipping", exc_info=True)

        # ── 第 2 层：动态 prompt（词级分词 + TF-IDF 相关性过滤） ──
        # 惰性构建词汇索引
        _build_vocabulary(self._agent_root)
        user_word_tokens, user_char_bigrams = _tokenize(user_msg)

        for fname in ["preferences.md", "workflows.md", "long_term_memory.md"]:
            prompt_text = self._read_cached(self._agent_root / fname)
            if not prompt_text:
                continue

            paragraphs = prompt_text.split("\n\n")
            relevant_sections: list[str] = []
            low_relevance: list[str] = []

            # 时间衰减：基于文件 mtime（30 天半衰期）
            file_path = self._agent_root / fname
            decay = _decay_factor(file_path)

            # 第一遍：计算所有段落的基础 TF-IDF 分数
            scored_paras: list[tuple[str, float]] = []
            for para in paragraphs:
                para = para.strip()
                if not para or para.startswith("<!--"):
                    continue

                para_word_tokens, _ = _tokenize(para)

                # 优先用 TF-IDF；fallback 到 Jaccard（词汇索引为空时）
                if _vocab_index.built and _vocab_index.N > 1:
                    score = _tfidf_score(
                        user_word_tokens, para_word_tokens,
                        df=_vocab_index.df, N=_vocab_index.N,
                    )
                else:
                    para_bigrams = _bigrams(para)
                    score = _jaccard(user_char_bigrams, para_bigrams)

                # 应用时间衰减
                score *= decay
                scored_paras.append((para, score))

            # 嵌入增强仅应用于 top-10 段落（避免每条消息上百次 ONNX 推理）
            emb_engine = get_embedding_engine()
            top_embed_indices: set[int] = set()
            if emb_engine.available:
                indexed = [(i, s) for i, (_, s) in enumerate(scored_paras) if s > 0]
                indexed.sort(key=lambda x: x[1], reverse=True)
                top_embed_indices = {i for i, _ in indexed[:10]}

            for i, (para, score) in enumerate(scored_paras):
                if i in top_embed_indices:
                    emb_sim = emb_engine.similarity(user_msg[:200], para[:200])
                    score = score * 0.7 + emb_sim * 0.3

                if score >= self.relevance_threshold:
                    relevant_sections.append(para)
                else:
                    title = para.split("\n")[0] if para else ""
                    if title and not title.startswith("#"):
                        low_relevance.append(f"- {title}")

            if relevant_sections:
                prompt_header = (
                    f"## {fname.replace('.md', '').replace('_', ' ').title()}"
                )
                section_text = "\n\n".join(relevant_sections)
                if low_relevance:
                    section_text += (
                        "\n\n" + t("ctx.others", items=", ".join(low_relevance))
                    )
                system_parts.append(f"{prompt_header}\n{section_text}")

        # ── 第 3 层：会话总览（overview.md） ──
        if session_dir is not None:
            overview_path = session_dir / "overview.md"
            if overview_path.exists():
                try:
                    overview = overview_path.read_text(encoding="utf-8")
                    system_parts.append(t("ctx.session_overview") + "\n" + overview)
                except OSError:
                    logger.debug("Failed to read overview.md, skipping")

        # ── 第 4 层：早期轮次总览 + 分层窗口摘要 ──
        if session_dir is not None and conv:
            timeline_path = session_dir / "timeline.json"
            timeline_entries: list[dict] = []
            if timeline_path.exists():
                try:
                    timeline_entries = json.loads(
                        timeline_path.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    logger.debug("Failed to read timeline.json, skipping")

            # 早期轮次 → 总览（全文窗口之前的轮次）
            if older:
                overview = _build_overview(session_dir, older)
                if overview:
                    system_parts.append(overview)

            # 分层摘要：全文窗口 + 额外摘要窗口 → 逐条索引
            # 近 full_text_turns 轮已有全文，这里提供更大跨度的快速定位线索
            if timeline_entries and recent:
                total_summary = self.full_text_turns + self.summary_turns
                recent_entries = timeline_entries[-total_summary:]
                summaries = [
                    f"- [{e.get('turn', '?')}] {e.get('summary', '')}"
                    for e in recent_entries
                ]
                if summaries:
                    recent_text = t("ctx.recent_chat") + "\n" + "\n".join(summaries)
                    system_parts.append(recent_text)

        # ── 组装最终 messages ──
        messages: list[dict] = []
        if system_parts:
            combined_system = "\n\n".join(system_parts)
            messages.append({"role": "system", "content": combined_system})

        return messages, recent
