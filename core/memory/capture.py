"""CaptureEngine — 条目截获引擎（纯规则，<100ms）。

每轮对话后运行，从用户/AI 消息中截获三类信号：
  1. 偏好 — 显式声明 + 隐式偏好（中/英双语）
  2. 工作流 — 纠正 + 流程建议（中/英双语）
  3. 长记忆 — 用户明确指令 + 跨会话频率触发（TopicFrequencyTracker）

截获后用 bigram Jaccard 去重（阈值 0.6），避免条目膨胀。
"""

import asyncio
import logging
import re
from typing import Callable

from core.context.relevance import _bigrams, _jaccard, _tokenize

from .entries import EntryManager
from .tracker import TopicFrequencyTracker

logger = logging.getLogger(__name__)

# ── 语言检测 ─────────────────────────────────────────────────────────

def _detect_language(text: str) -> str:
    """检测消息语言。

    优先判断：包含 CJK 字符 → 'zh'；
    否则 ASCII 占比 >= 50% → 'en'，否则 'zh'。
    """
    if not text:
        return "zh"

    # CJK 字符检测（U+4E00..U+9FFF 基本汉字）
    has_cjk = any('一' <= c <= '鿿' for c in text)
    if has_cjk:
        return "zh"

    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return "en" if (ascii_chars / len(text)) >= 0.5 else "zh"

# ── 截获规则 ─────────────────────────────────────────────────────────

# —— 中文：显式偏好 ——
_PREFERENCE_PATTERNS = [
    # "我喜欢/偏好/习惯/希望/想要…"（明确的偏好声明）
    r'(?:我(?:喜欢|偏好|习惯|想要|希望|讨厌|不喜欢|受不了))(?:[^。.!！？?\n]{2,60})',
    # "永远都/总是/尽量/别再…"
    r'(?:(?:永远都?|总是|尽量|别再|不要再|别忘了)[^。.!！？?\n]{2,60})',
]

# —— 中文：隐式偏好 ——
_IMPLICIT_PREFERENCE_PATTERNS = [
    # "能不能(更)简洁/快/详细一点"
    r'(?:能不能(?:更[^。.!！？?\n]{0,4})?(?:简洁|快|慢|详细|简单|短)[一点些])[^。.!！？?\n]{0,20}',
    # "这个/你的方案/回答不(太|够)(好|对|行|满意)"
    r'(?:(?:这个|你的)(?:方案|回答|代码|写法|思路)[^\n]{0,6}不(?:太|够|是)(?:好|对|行|满意|理想|合适)[^。.!！？?\n]{0,20})',
    # "我更(倾向于|偏向|喜欢)…"
    r'(?:我更(?:倾向于|偏向|偏好|喜欢|想))[^。.!！？?\n]{2,60}',
    # "还是…吧" (reluctant acceptance / preference)
    r'(?:还是[^。.!！？?\n]{2,40}吧)',
    # "不如…" (suggesting alternative)
    r'(?:不如[^。.!！？?\n]{2,60})',
    # "最好(还是)…" (expressing desired behavior)
    r'(?:最好(?:还是)?[^。.!！？?\n]{2,60})',
    # "别(太|那么)…" (negative preference)
    r'(?:别(?:太|那么)[^。.!！？?\n]{2,60})',
]

# —— 中文：显式工作流 ——
_WORKFLOW_PATTERNS = [
    # "不对/错了/应该是/正确的是…"
    r'(?:(?:不对|错了|应该是|正确的是|其实是)[^。.!！？?\n]{2,60})',
    # "下次/以后" + 应该/要/记得/改成/注意
    r'(?:(?:下次|以后)(?:应该|要|记得|改成|注意)[^。.!！？?\n]{2,60})',
]

# —— 中文：隐式纠正 ——
_IMPLICIT_CORRECTION_PATTERNS = [
    # "你再(看看|想想|检查一下|确认|读)…"
    r'(?:你再(?:看看|想想|检查一下|确认一下|读一下|搜一下)[^。.!！？?\n]{0,40})',
    # "应该先…再…" (workflow ordering)
    r'(?:应该先[^。.!！？?\n]{2,60})',
    # "不是…而是…" (correction with alternative)
    r'(?:不是[^。.!！？?\n]{2,40}而是[^。.!！？?\n]{2,40})',
    # "这个(还|仍)有(问题|bug|错误)" (error persistence)
    r'(?:这个(?:还|仍|依然)[^\n]{0,10}有(?:问题|bug|错误|毛病)[^。.!！？?\n]{0,20})',
    # "还没(解决|修好|改对|改好|搞定)" (unresolved)
    r'(?:还没(?:解决|修好|改对|改好|搞定|处理)[^。.!！？?\n]{0,30})',
]

# —— 中文：长记忆 ——
_LONG_MEMORY_PATTERNS = [
    # 用户明确指令："记住…"、"别忘了…"、"记下来…"
    r'(?:(?:你?记住|别忘了|记下来|帮我记住)[，,\s]*)(?:[^。.!！？?\n]{2,80})',
]

# —— 中文：隐式长记忆（弱信号 — 需频率门槛）──
_IMPLICIT_LONG_MEMORY_PATTERNS = [
    # "我一直/长期/常年" (persistent facts — 需要跨会话验证)
    r'(?:我(?:一直|长期|常年|从小就)[^。.!！？?\n]{2,60})',
]

# —— 中文：隐式长记忆（强信号 — 单次即截获）──
_IMPLICIT_LONG_MEMORY_STRONG_PATTERNS = [
    # "我的工作/项目/团队/公司/岗位/方向/领域" (personal identity)
    r'(?:(?:我的?)(?:工作|项目|团队|公司|部门|方向|领域|岗位|职位|身份)[^\n]{0,6}(?:是|在|做)[^。.!！？?\n]{2,60})',
    # "我们公司/团队/项目/部门" (organizational context)
    r'(?:我们(?:公司|团队|项目|部门|组)[^。.!！？?\n]{2,60})',
    # "我家在/我住在/我在...上班/工作" (personal location/job)
    r'(?:(?:我家|我住|我住在|我在[^。.!！？?\n]{0,10}(?:上班|工作|就职))[^。.!！？?\n]{2,60})',
    # "我的技术栈/我用/我擅长/我主要用" (skill set)
    r'(?:(?:我的)?(?:技术栈|常用工具)|我(?:擅长|主要用|习惯用|一直在用)[^。.!！？?\n]{2,60})',
]

# —— 英文：偏好 ——
_EN_PREFERENCE_PATTERNS = [
    # "I prefer/like/want/hate/dislike/love/enjoy ..."
    r'(?i)(?:I\s+(?:prefer|like|want|hate|dislike|love|enjoy|need)\s+[\w\s]{4,80})',
    # "(always|never|don't|do not) ..."
    r'(?i)(?:(?:always|never|don\'?t\s+want|do\s+not\s+want)\s+[\w\s]{4,80})',
    # "(can you|please) (be more)? (concise|brief|detailed|specific|thorough) ..."
    r'(?i)(?:(?:can\s+you|please|pls)\s+(?:be\s+(?:more\s+)?)?(?:concise|brief|detailed|specific|thorough|verbose|short))[\w\s]{2,60}',
    # "I'd rather / I would rather / I'd prefer"
    r"(?i)(?:I'd\s+(?:rather|prefer)|I\s+would\s+(?:rather|prefer))[\w\s]{4,80}",
]

# —— 英文：工作流 ——
_EN_WORKFLOW_PATTERNS = [
    # "(wrong|incorrect|no, that's not|that is not) ..."
    r"(?i)(?:(?:wrong|incorrect|no,?\s*that\'?s?\s+not|that\s+is\s+not)\s+[\w\s]{4,80})",
    # "(next time|in the future|going forward) (should|do|remember|try|use|make sure) ..."
    r"(?i)(?:(?:next\s+time|in\s+the\s+future|going\s+forward|from\s+now\s+on)\s+(?:should|do|remember|try|use|make\s+sure|please)\s+[\w\s]{4,80})",
    # "you should have / you should've ..."
    r"(?i)(?:you\s+should(?:\'?ve|have)\s+[\w\s]{4,80})",
]

# —— 英文：长记忆 ——
_EN_LONG_MEMORY_PATTERNS = [
    # "(remember|don't forget|note that|keep in mind) (that)? ..."
    r'(?i)(?:(?:remember|don\'?t\s+forget|note\s+that|keep\s+in\s+mind)\s+(?:that\s+)?[\w\s]{4,100})',
    # "I (live in|work at|work as|am a) ..."
    r'(?i)(?:I\s+(?:live\s+in|work\s+(?:at|as|in|for|with)|am\s+a)\s+[\w\s]{4,80})',
]


# ── CaptureEngine ────────────────────────────────────────────────────


class CaptureEngine:
    """条目截获引擎。

    用法:
        engine = CaptureEngine(entry_manager, topic_tracker)
        await engine.capture(user_msg, assistant_msg, session_id, turn)
    """

    JACCARD_DEDUP_THRESHOLD = 0.6

    def __init__(self, entries: EntryManager, tracker: TopicFrequencyTracker) -> None:
        self._entries = entries
        self._tracker = tracker

    # ── 关键词提取 ────────────────────────────────────────────────

    _MAX_KEYWORDS = 5
    _KEYWORD_RE = re.compile(r'[一-鿿]{2,4}|[a-zA-Z]{3,}')

    @staticmethod
    def _extract_candidate_keywords(text: str) -> list[str]:
        """从消息中提取候选关键词（≤5 个），用于频率追踪。纯正则，<1ms。"""
        if not text:
            return []
        matches = CaptureEngine._KEYWORD_RE.findall(text)
        # 去重 + 截断
        seen: set[str] = set()
        result: list[str] = []
        for m in matches:
            low = m.lower()
            if low not in seen:
                seen.add(low)
                result.append(low)
                if len(result) >= CaptureEngine._MAX_KEYWORDS:
                    break
        return result

    async def _batch_record(self, keywords: list[str], session_id: str) -> None:
        """后台批量记录关键词到 Tracker（fire-and-forget）。"""
        for kw in keywords:
            try:
                await self._tracker.record(kw, session_id)
            except Exception:
                pass  # tracker 失败不影响对话

    # ── 主截获流程 ──────────────────────────────────────────────────

    async def capture(
        self,
        user_msg: str,
        assistant_msg: str,
        session_id: str,
        turn: int,
    ) -> list[dict]:
        """从一轮对话中截获条目。

        Args:
            user_msg: 用户消息原文
            assistant_msg: AI 回复原文
            session_id: 当前会话 ID
            turn: 当前轮次编号

        Returns:
            本轮新截获/更新的条目列表
        """
        source = {"session_id": session_id, "turn": turn}
        captured: list[dict] = []
        lang = _detect_language(user_msg)

        # ── 频率追踪（fire-and-forget，不阻塞）──
        keywords = self._extract_candidate_keywords(user_msg)
        if keywords:
            asyncio.create_task(self._batch_record(keywords, session_id))

        # ── 根据语言选择 Pattern ──
        if lang == "en":
            pref_patterns = _EN_PREFERENCE_PATTERNS
            wf_patterns = _EN_WORKFLOW_PATTERNS
            mem_patterns = _EN_LONG_MEMORY_PATTERNS
            implicit_pref = []
            implicit_correct = []
            implicit_mem = []
            implicit_mem_strong = []
        else:
            pref_patterns = _PREFERENCE_PATTERNS
            wf_patterns = _WORKFLOW_PATTERNS
            mem_patterns = _LONG_MEMORY_PATTERNS
            implicit_pref = _IMPLICIT_PREFERENCE_PATTERNS
            implicit_correct = _IMPLICIT_CORRECTION_PATTERNS
            implicit_mem = _IMPLICIT_LONG_MEMORY_PATTERNS
            implicit_mem_strong = _IMPLICIT_LONG_MEMORY_STRONG_PATTERNS

        # ── 偏好截获（显式 + 隐式）──
        for pattern in pref_patterns + implicit_pref:
            for match in re.finditer(pattern, user_msg):
                content = match.group(0).strip()
                if len(content) < 4:
                    continue
                entry = await self._dedup_and_save("preferences", content, source)
                if entry:
                    captured.append(entry)

        # ── 工作流截获（显式 + 隐式）──
        for pattern in wf_patterns + implicit_correct:
            for match in re.finditer(pattern, user_msg):
                content = match.group(0).strip()
                if len(content) < 4:
                    continue
                entry = await self._dedup_and_save("workflows", content, source)
                if entry:
                    captured.append(entry)

        # ── 长记忆截获（显式指令 + 隐式强信号 + 隐式弱信号频率触发）──
        for pattern in mem_patterns + implicit_mem_strong + implicit_mem:
            for match in re.finditer(pattern, user_msg):
                content = match.group(0).strip()
                if len(content) < 4:
                    continue

                # 隐式弱信号走频率校验；显式指令 + 隐式强信号直接截获
                if pattern in implicit_mem:
                    kw = (self._extract_candidate_keywords(content) or [content[:4]])[0]
                    if not await self._tracker.should_capture(kw):
                        continue
                    source["trigger"] = "frequency"
                elif pattern in implicit_mem_strong:
                    source["trigger"] = "implicit_strong"
                else:
                    source["trigger"] = "explicit"

                entry = await self._dedup_and_save("long_term_memory", content, source)
                if entry:
                    captured.append(entry)

        if captured:
            logger.info(f"截获 {len(captured)} 条: {[e.get('content','')[:40] for e in captured]}")

        return captured

    async def _dedup_and_save(
        self, entry_type: str, content: str, source: dict
    ) -> dict | None:
        """去重后写入条目。返回写入/更新的条目，None 表示跳过。"""
        existing = await self._entries.load(entry_type)
        # 使用 word-level tokenizer（优先）+ char 2-gram fallback
        content_word_tokens, content_bigrams = _tokenize(content)

        # 检查是否与已有条目相似
        for i, old_entry in enumerate(existing):
            old_content = old_entry.get("content", "")
            old_word_tokens, old_bigrams = _tokenize(old_content)

            # 优先用 word-level Jaccard；fallback 到 char 2-gram
            if content_word_tokens and old_word_tokens:
                similarity = _jaccard(content_word_tokens, old_word_tokens)
            else:
                similarity = _jaccard(content_bigrams, old_bigrams)

            if similarity >= self.JACCARD_DEDUP_THRESHOLD:
                # 更新已有条目
                await self._entries.update(entry_type, i, content=content)
                logger.debug(f"去重更新 [{entry_type}] #{i}: {content[:40]}…")
                # 返回更新后的条目
                updated = await self._entries.load(entry_type)
                entry = updated[i] if i < len(updated) else None
                if entry:
                    entry["type"] = entry_type
                return entry

        # 新条目
        entry = await self._entries.add(entry_type, content, source)
        entry["type"] = entry_type
        return entry
