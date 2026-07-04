"""纯函数工具：word-level tokenizer、TF-IDF 评分、话题提取、决策检测、对话切分、历史总览。

P5 Batch B1: 从 char 2-gram 升级为 word-level tokenizer（最大正向匹配 + ASCII分词）+ TF-IDF 加权。
"""

import logging
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from core.setup import aide_dir

logger = logging.getLogger(__name__)

WINDOW_TURNS = 8

DECISION_KEYWORDS = re.compile(
    r'(确定|决定|选择|采用|最终|结论是|方案是|就用|还是用|'
    r'建议|推荐|修改了|创建了|删除了|更新了)'
)

# ── 停用词（扩展版：~100 中文 + 30 英文）─────────────────────────────

_ZH_STOP_WORDS = frozenset({
    '这个', '那个', '什么', '怎么', '为什么', '可以', '能不能',
    '帮我', '一个', '一下', '一些', '这些', '那些',
    '有没有', '是不是', '能不能', '可不可以', '我需要', '我想要',
    '请问', '麻烦', '然后', '所以', '但是', '因为', '如果', '虽然',
    '我们', '你们', '他们', '哪里',
    '编写', '现在', '知道',
    # 扩展高频词
    '的', '了', '是', '在', '和', '也', '就', '都', '而', '及', '与',
    '着', '或', '一个', '没有', '已经', '还是', '只是', '不是',
    '通过', '使用', '需要', '进行', '可能', '应该', '问题',
    '自己', '非常', '比较', '之后', '之前', '以后', '时候',
    '大家', '所有', '很多', '各种', '觉得', '知道',
    # 扩展停用词补充
    '那种', '这样', '那样', '一样', '其中', '作为', '对于',
    '关于', '以及', '并且', '不过', '不仅', '而已', '什么',
    '而且', '虽然', '然而', '用来', '不能', '不会', '不要',
    '也是', '还会', '还要', '都会', '的话', '就是',
})

_EN_STOP_WORDS = frozenset({
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'can', 'shall', 'to', 'of', 'in', 'for',
    'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through', 'during',
    'before', 'after', 'above', 'below', 'between', 'under', 'again',
    'further', 'then', 'once', 'here', 'there', 'when', 'where', 'why',
    'how', 'all', 'both', 'each', 'few', 'more', 'most', 'other', 'some',
    'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than',
    'too', 'very', 'just', 'because', 'but', 'and', 'or', 'if', 'while',
    'that', 'this', 'it', 'its', 'my', 'your', 'i', 'you', 'he', 'she',
    'they', 'we', 'me', 'him', 'her', 'us', 'them',
})

# ── 种子词汇表（冷启动：新用户尚无条目时使用）──────────────────────

_SEED_VOCABULARY: frozenset[str] = frozenset({
    # 技术术语
    "编程", "测试", "部署", "数据库", "前端", "后端", "代码", "文件",
    "配置", "性能", "安全", "日志", "缓存", "队列", "容器", "服务",
    "接口", "框架", "模块", "函数", "异常", "调试", "构建", "发布",
    "版本", "依赖", "环境", "网络", "存储", "消息", "任务", "权限",
    "认证", "加密", "备份", "恢复", "监控", "扩展", "优化", "重构",
    "文档", "注释", "规范", "架构", "设计", "模式", "脚本", "插件",
    "模板", "路由", "状态", "事件", "命令", "终端", "编译器", "解释器",
    "进程", "线程", "并发", "异步", "同步", "内存", "请求", "响应",
    # 日常表达
    "简洁", "详细", "快速", "风格", "格式", "颜色", "主题", "布局",
    "搜索", "过滤", "排序", "导入", "导出", "上传", "下载", "安装",
    "卸载", "更新", "升级", "迁移", "删除", "创建", "修改", "查看",
    "运行", "停止", "重启", "连接", "断开", "发送", "接收", "处理",
    # 英文高频词（ASCII，由 _tokenize 的 _ASCII_WORD_RE 处理）
    # 这里只放中文种子词
})

# "伪 DF 值"：种子词在冷启动时 df 统一为 1（避免 idf 为 0）
_SEED_DF: dict[str, int] = {w: 1 for w in _SEED_VOCABULARY}

# ── Word-Level Tokenizer ──────────────────────────────────────────────

# 中文片段正则（CJK 统一汉字）
_CJK_RE = re.compile(r'[一-鿿]{2,}')

# ASCII 单词正则
_ASCII_WORD_RE = re.compile(r'[a-zA-Z0-9]+')


@dataclass
class VocabularyIndex:
    """词汇索引 — 惰性构建，用于中文分词和 TF-IDF 评分。"""
    vocab: frozenset[str] = field(default_factory=frozenset)
    df: dict[str, int] = field(default_factory=dict)   # document frequency
    N: int = 0                                           # total documents
    built: bool = False


# 模块级词汇缓存（惰性初始化）
_vocab_index: VocabularyIndex = VocabularyIndex()


def _build_vocabulary(agent_root: Path | None = None) -> VocabularyIndex:
    """从 entry JSON 文件中构建词汇表和 DF 表。

    扫描 ~/.aide/agent/data/*.json 中的所有条目 content，
    提取 2-4 字中文片段（出现 ≥2 次）作为词汇表。
    同时计算每个 token 在多少条目中出现（document frequency）。

    调用此函数刷新模块级缓存。
    """
    global _vocab_index
    if _vocab_index.built:
        return _vocab_index

    if agent_root is None:
        agent_root = aide_dir() / "agent"

    data_dir = agent_root / "data"
    if not data_dir.exists():
        # 冷启动：data 目录不存在，使用种子词汇
        _vocab_index = VocabularyIndex(
            vocab=_SEED_VOCABULARY,
            df=_SEED_DF,
            N=len(_SEED_VOCABULARY),
            built=True,
        )
        logger.debug(f"词汇索引冷启动（无 data 目录）: {len(_SEED_VOCABULARY)} 种子词")
        return _vocab_index

    # 收集所有条目内容
    all_entries: list[str] = []
    for fname in ["preferences.json", "workflows.json", "long_term_memory.json"]:
        path = data_dir / fname
        if not path.exists():
            continue
        try:
            import json
            entries = json.loads(path.read_text(encoding="utf-8"))
            for e in entries:
                content = e.get("content", "")
                if content:
                    all_entries.append(content)
        except Exception:
            continue

    if not all_entries:
        # 冷启动：无条目时使用种子词汇
        _vocab_index = VocabularyIndex(
            vocab=_SEED_VOCABULARY,
            df=_SEED_DF,
            N=len(_SEED_VOCABULARY),
            built=True,
        )
        logger.debug(f"词汇索引冷启动: {len(_SEED_VOCABULARY)} 种子词")
        return _vocab_index

    # 统计 2-4 字中文片段频率
    fragment_counter: Counter = Counter()
    for text in all_entries:
        # 找所有中文片段
        for run in _CJK_RE.findall(text):
            for n in [2, 3, 4]:
                for i in range(len(run) - n + 1):
                    frag = run[i:i + n]
                    if frag not in _ZH_STOP_WORDS:
                        fragment_counter[frag] += 1

    # 只保留出现 ≥2 次的片段
    vocab = frozenset({frag for frag, cnt in fragment_counter.items() if cnt >= 2})

    # 词汇不足 20 个时，注入种子词汇补充冷启动
    if len(vocab) < 20:
        vocab = vocab | _SEED_VOCABULARY

    # 计算 DF（每个 token 在多少个条目中出现）
    df: dict[str, int] = {}
    for token in vocab:
        if token in _SEED_VOCABULARY and token not in {frag for frag, cnt in fragment_counter.items() if cnt >= 2}:
            df[token] = 1  # 纯种子词
        else:
            for text in all_entries:
                if token in text:
                    df[token] = df.get(token, 0) + 1

    _vocab_index = VocabularyIndex(
        vocab=vocab,
        df=df,
        N=len(all_entries),
        built=True,
    )

    logger.debug(f"词汇索引构建完成: {len(vocab)} 词, {len(all_entries)} 条目")
    return _vocab_index


def _chinese_tokenize(text: str, vocab: frozenset[str]) -> list[str]:
    """中文最大正向匹配分词（纯 stdlib）。

    对每个 CJK 连续片段，从位置 0 开始贪心取最长匹配词（2-4 字），
    未匹配的字符按单字切分。

    Args:
        text: 待分词的中文文本
        vocab: 已知词汇集合（来自 VocabularyIndex）

    Returns:
        分词后的 token 列表
    """
    if not vocab:
        return list(text)  # fallback: 逐字切分

    tokens: list[str] = []
    i = 0
    while i < len(text):
        # 非 CJK 字符直接跳过，由 _tokenize() 外部处理
        if not ('一' <= text[i] <= '鿿' or '㐀' <= text[i] <= '䶿'):
            tokens.append(text[i])
            i += 1
            continue

        # 最大正向匹配（4 → 3 → 2 → 单字）
        longest = text[i]  # fallback: single char
        max_len = min(4, len(text) - i)
        for j in range(max_len, 1, -1):
            candidate = text[i:i + j]
            if candidate in vocab:
                longest = candidate
                break

        # 过滤停用词
        if longest not in _ZH_STOP_WORDS:
            tokens.append(longest)
        i += len(longest)

    return tokens


def _tokenize(text: str, vocab: frozenset[str] | None = None) -> tuple[set[str], set[str]]:
    """将文本分词为 (word_tokens, char_bigrams)。

    - 中文：最大正向匹配 → word tokens
    - 英文/ASCII：word tokens
    - char_bigrams：保留作为 fallback（2-gram on original text）

    Args:
        text: 输入文本
        vocab: 中文词汇表（None 时回退到纯 char 2-gram）

    Returns:
        (word_tokens, char_bigrams) — 两个 token set
    """
    if not text:
        return set(), set()

    # Char 2-gram fallback（始终计算）
    char_bigrams = {text[i:i + 2] for i in range(len(text) - 1)}

    if vocab is None:
        vocab = _vocab_index.vocab

    word_tokens: set[str] = set()

    # 提取 ASCII 单词（跳过停用词）
    for word in _ASCII_WORD_RE.findall(text.lower()):
        if word not in _EN_STOP_WORDS and len(word) >= 2:
            word_tokens.add(word)

    # 中文分词
    for cjk_run in _CJK_RE.findall(text):
        for token in _chinese_tokenize(cjk_run, vocab):
            if len(token) >= 2:
                word_tokens.add(token)

    return word_tokens, char_bigrams


def _tfidf_score(query_tokens: set[str], doc_tokens: set[str],
                 df: dict[str, int] | None = None, N: int = 1) -> float:
    """TF-IDF 加权评分。

    score = sum(tf(t) * idf(t) for t in overlap)
    其中 tf = 在文档中的频次，idf = log(N / df(t))

    Args:
        query_tokens: 用户消息的 token set
        doc_tokens: prompt 段落的 token set
        df: document frequency 表
        N: 总文档数

    Returns:
        归一化分数（0.0 ~ 1.0）
    """
    if not query_tokens or not doc_tokens:
        return 0.0

    overlap = query_tokens & doc_tokens
    if not overlap:
        return 0.0

    score = 0.0
    if df and N > 1:
        for token in overlap:
            tf = 1.0  # query tokens are typically unique
            doc_freq = df.get(token, 1)
            idf = math.log(N / doc_freq) + 1.0  # smooth
            score += tf * idf
    else:
        score = float(len(overlap))

    # Jaccard-like normalization
    union = len(query_tokens | doc_tokens)
    if union > 0:
        score = score / union

    return score


def _decay_factor(file_path: Path | None, half_life_days: int = 30) -> float:
    """指数时间衰减：weight = 0.5 ^ (age_days / half_life_days)。

    用于降低旧 prompt 文件的相关性权重。
    返回 1.0（无衰减）如果文件不存在或无法读取 mtime。

    Args:
        file_path: prompt 文件路径
        half_life_days: 半衰期（天），默认 30 天

    Returns:
        衰减因子（0.0 ~ 1.0）
    """
    if file_path is None:
        return 1.0
    try:
        mtime = file_path.stat().st_mtime
        age_days = (time.time() - mtime) / 86400.0
        if age_days <= 0:
            return 1.0
        return 0.5 ** (age_days / half_life_days)
    except (OSError, ValueError):
        return 1.0


def flush_vocab_cache() -> None:
    """刷新词汇缓存（/profile update 后调用）。"""
    global _vocab_index
    _vocab_index = VocabularyIndex()


# ── bigram 工具 ─────────────────────────────────────────────────────


def _bigrams(text: str) -> set[str]:
    """Character 2-gram 分词，零依赖。"""
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _jaccard(a: set, b: set) -> float:
    """Jaccard 相似度。"""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ── 总览生成 ────────────────────────────────────────────────────────


def _extract_topics(text: str, max_topics: int = 3) -> list[str]:
    """从文本中提取关键词作为话题。

    策略：
    1. 取所有 2-4 字片段
    2. 过滤停用词
    3. 按频率排序取 top N
    """
    if not text.strip():
        return []

    # 提取 2-4 字片段（仅纯中文）
    fragments: list[str] = []
    for n in [2, 3, 4]:
        for i in range(len(text) - n + 1):
            frag = text[i:i + n]
            # 只保留纯中文片段（不含 ASCII、数字、标点）
            if not re.fullmatch(r'[一-鿿]+', frag):
                continue
            if frag in _ZH_STOP_WORDS:
                continue
            fragments.append(frag)

    if not fragments:
        return []

    counter = Counter(fragments)
    # 优先长片段：3-4 字 >=1 次，2 字 >=2 次
    candidates: list[tuple[str, int]] = []
    for frag, count in counter.most_common(30):
        if len(frag) >= 3 and count >= 1:
            candidates.append((frag, count))
        elif len(frag) == 2 and count >= 2:
            candidates.append((frag, count))

    # 去重：移除被更长片段包含的短片段
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
    # 分句（按 。！？\n）
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

    Args:
        session_dir: session 目录（用于读取 cache.json）
        older_conversation: 早期的对话消息
    """
    def _extract_text(content: str | list) -> str:
        """提取消息文本（兼容多模态 content list）。"""
        if isinstance(content, list):
            return " ".join(p.get("text", "") for p in content if p.get("type") == "text")
        return content or ""

    # 收集用户消息
    user_texts = [
        _extract_text(m.get("content", ""))
        for m in older_conversation if m.get("role") == "user"
    ]
    # 收集助手消息
    assistant_texts = [
        _extract_text(m.get("content", ""))
        for m in older_conversation if m.get("role") == "assistant"
    ]

    all_user = " ".join(user_texts)
    all_assistant = " ".join(assistant_texts)

    # 提取话题
    topics = _extract_topics(all_user)
    # 提取决策
    decisions = _extract_decisions(all_assistant)

    if not topics and not decisions:
        # fallback: 使用用户首条消息的前 30 字符
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


# ── 对话切分 ────────────────────────────────────────────────────────


def _split_conversation(
    conversation: list[dict],
    window: int = WINDOW_TURNS,
) -> tuple[list[dict], list[dict]]:
    """按轮次切分对话历史。

    "一轮" = 一条 user 消息 + 后续 assistant 消息（含 tool_calls）。

    Returns:
        (older_messages, recent_messages)
    """
    # 找到所有 user 消息的索引
    user_indices = [
        i for i, m in enumerate(conversation)
        if m.get("role") == "user"
    ]

    if len(user_indices) <= window:
        return [], conversation

    cutoff = user_indices[-window]  # 最近 window 轮的第一条 user 消息
    return conversation[:cutoff], conversation[cutoff:]
