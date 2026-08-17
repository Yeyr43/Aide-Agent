"""词级分词 + 词汇索引 + 相似度评分子系统。

从 relevance.py 拆分：tokenizer、vocab、bigram/Jaccard、TF-IDF、时间衰减。
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from core.setup import aide_dir

logger = logging.getLogger(__name__)

# ── 正则常量 ──────────────────────────────────────────────────────────

_CJK_RE = re.compile(r'[一-鿿]{2,}')
_ASCII_WORD_RE = re.compile(r'[a-zA-Z0-9]+')

# ── 停用词（~100 中文 + 60 英文）─────────────────────────────────────

_ZH_STOP_WORDS = frozenset({
    '这个', '那个', '什么', '怎么', '为什么', '可以', '能不能',
    '帮我', '一个', '一下', '一些', '这些', '那些',
    '有没有', '是不是', '能不能', '可不可以', '我需要', '我想要',
    '请问', '麻烦', '然后', '所以', '但是', '因为', '如果', '虽然',
    '我们', '你们', '他们', '哪里',
    '编写', '现在', '知道',
    '的', '了', '是', '在', '和', '也', '就', '都', '而', '及', '与',
    '着', '或', '一个', '没有', '已经', '还是', '只是', '不是',
    '通过', '使用', '需要', '进行', '可能', '应该', '问题',
    '自己', '非常', '比较', '之后', '之前', '以后', '时候',
    '大家', '所有', '很多', '各种', '觉得', '知道',
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

# ── 种子词汇表（冷启动）─────────────────────────────────────────────

_SEED_VOCABULARY: frozenset[str] = frozenset({
    # 技术栈 — 中文
    "编程", "测试", "部署", "数据库", "前端", "后端", "代码", "文件",
    "配置", "性能", "安全", "日志", "缓存", "容器", "服务",
    "接口", "框架", "模块", "函数", "异常", "调试", "构建", "发布",
    "版本", "依赖", "环境", "网络", "存储", "消息", "任务", "权限",
    "认证", "加密", "备份", "恢复", "监控", "扩展", "优化", "重构",
    "文档", "注释", "规范", "架构", "设计", "模式", "脚本", "插件",
    "模板", "路由", "状态", "命令", "终端",
    "进程", "线程", "并发", "异步", "同步", "内存", "请求", "响应",
    # Tech stack — English
    "code", "test", "deploy", "database", "frontend", "backend", "file",
    "config", "performance", "security", "log", "cache", "container", "service",
    "api", "framework", "module", "function", "error", "debug", "build", "release",
    "version", "dependency", "environment", "network", "storage", "message", "task",
    "auth", "encrypt", "backup", "restore", "monitor", "scale", "optimize", "refactor",
    "document", "comment", "standard", "architecture", "design", "pattern", "script", "plugin",
    "template", "route", "state", "command", "terminal",
    "process", "thread", "async", "memory", "request", "response",
    # 偏好/工作流 — 中文
    "回复", "提醒", "命名", "规则", "习惯", "自动", "手动",
    "稳定", "可靠", "安静", "背景",
    "简洁", "详细", "快速", "风格", "格式", "颜色", "主题", "布局",
    # Preferences/workflows — English
    "reply", "remind", "naming", "rule", "habit", "auto", "manual",
    "stable", "reliable", "quiet", "background",
    "concise", "detailed", "quick", "style", "format", "color", "theme", "layout",
    # 操作 — 中文
    "搜索", "过滤", "排序", "导入", "导出", "上传", "下载", "安装",
    "卸载", "更新", "升级", "迁移", "删除", "创建", "修改", "查看",
    "运行", "停止", "重启", "连接", "发送", "处理",
    # Operations — English
    "search", "filter", "sort", "import", "export", "upload", "download", "install",
    "update", "upgrade", "migrate", "delete", "create", "modify", "view",
    "run", "stop", "restart", "connect", "send", "process",
})

_SEED_DF: dict[str, int] = {w: 1 for w in _SEED_VOCABULARY}

# ── VocabularyIndex ──────────────────────────────────────────────────


@dataclass
class VocabularyIndex:
    """词汇索引 — 惰性构建，用于中文分词和 TF-IDF 评分。"""
    vocab: frozenset[str] = field(default_factory=frozenset)
    df: dict[str, int] = field(default_factory=dict)
    N: int = 0
    built: bool = False


_vocab_index: VocabularyIndex = VocabularyIndex()


# ── 语言检测 ──────────────────────────────────────────────────────────


def _detect_language(text: str) -> str:
    """检测文本主要语言（"zh" 或 "en"）。

    基于 CJK 字符占比判断：CJK 字符 > 30% → "zh"，否则 "en"。
    专为简短约束文本优化（如"用中文回复"、"prefer English answers"）。

    P5: 反馈闭环新增。供 FeedbackVerifier 使用。
    """
    if not text:
        return "zh"
    # 去除非字母/CJK 字符后计数
    cleaned = ''.join(c for c in text if c.isalpha() or '一' <= c <= '鿿')
    if not cleaned:
        return "zh"
    cjk_count = sum(1 for c in cleaned if '一' <= c <= '鿿')
    return "zh" if cjk_count / len(cleaned) >= 0.3 else "en"


def _collect_timeline_lines(sessions_root: Path, sink: list[str],
                            max_lines: int = 300) -> None:
    """从会话 timeline.json 摘要收集文本行（供词汇构建），控制扫描成本。

    缓解冷启动：记忆文件少时分词退化（用户查询/记忆里的领域词
    不在词汇表 → 词级 TF-IDF 失效）。会话摘要补充领域词汇。
    """
    if not sessions_root.exists():
        return
    count = 0
    for session_dir in sorted(sessions_root.iterdir()):
        if not session_dir.is_dir() or session_dir.name.startswith("_"):
            continue
        timeline = session_dir / "timeline.json"
        if not timeline.exists():
            continue
        try:
            from core.storage import read_jsonl
            for e in read_jsonl(timeline):
                s = (e.get("summary", "") or "").strip()
                if s:
                    sink.append(s)
                    count += 1
                    if count >= max_lines:
                        return
        except (json.JSONDecodeError, OSError):
            continue


def _build_vocabulary(agent_root: Path | None = None,
                      sessions_root: Path | None = None) -> VocabularyIndex:
    """从 agent/*.md 记忆文件 + 会话 timeline 摘要构建词汇表和 DF 表。

    扫描 ~/.aide/agent/*.md 中的内容行，以及（若提供 sessions_root）
    各会话 timeline.json 摘要，提取 2-4 字中/英文片段（出现 ≥2 次）作为词汇表。

    P5: 切换到 .md 文件（不再有 JSON 条目）。
    """
    global _vocab_index
    if _vocab_index.built:
        return _vocab_index

    if agent_root is None:
        agent_root = aide_dir() / "agent"

    # 收集所有记忆文件的非标题行
    all_lines: list[str] = []
    for fname in ["preferences.md", "workflows.md", "long_term_memory.md"]:
        path = agent_root / fname
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                clean = line[2:].strip() if line.startswith("- ") else line
                if clean:
                    all_lines.append(clean)
        except Exception:
            continue

    # 会话摘要补充领域词汇（记忆不足时仍能分词）
    if sessions_root is not None:
        _collect_timeline_lines(sessions_root, all_lines)

    if not all_lines:
        _vocab_index = VocabularyIndex(
            vocab=_SEED_VOCABULARY, df=_SEED_DF,
            N=len(_SEED_VOCABULARY), built=True,
        )
        return _vocab_index

    # 提取中/英文片段
    fragment_counter: Counter = Counter()
    for text in all_lines:
        # CJK 片段
        for run in _CJK_RE.findall(text):
            for n in [2, 3, 4]:
                for i in range(len(run) - n + 1):
                    frag = run[i:i + n]
                    if frag not in _ZH_STOP_WORDS:
                        fragment_counter[frag] += 1
        # ASCII 词
        for word in _ASCII_WORD_RE.findall(text.lower()):
            if len(word) >= 2 and word not in _EN_STOP_WORDS:
                fragment_counter[word] += 1

    vocab = frozenset({frag for frag, cnt in fragment_counter.items() if cnt >= 2})
    if len(vocab) < 20:
        vocab = vocab | _SEED_VOCABULARY

    df: dict[str, int] = {}
    for token in vocab:
        # Seed vocab tokens get df=1 unless they actually appear
        seed_only = (token in _SEED_VOCABULARY and
                     token not in {frag for frag, cnt in fragment_counter.items() if cnt >= 2})
        if seed_only:
            df[token] = 1
        else:
            for text in all_lines:
                if token in text:
                    df[token] = df.get(token, 0) + 1

    _vocab_index = VocabularyIndex(vocab=vocab, df=df, N=len(all_lines), built=True)
    logger.debug(f"词汇索引构建完成: {len(vocab)} 词, {len(all_lines)} 行")
    return _vocab_index


def flush_vocab_cache() -> None:
    """刷新词汇缓存（/profile update 后调用）。

    Pipeline 的 flush_cache() 不直接重置实例引用，
    而是调用此函数 + _build_vocabulary() 重建模块级全局索引。
    这样所有子系统（pipeline / recall / capture）通过
    _build_vocabulary() 获取的都是同一份数据，不会出现
    分词词汇表不一致的问题。
    """
    global _vocab_index
    _vocab_index = VocabularyIndex()


def get_vocab_index() -> VocabularyIndex:
    """返回当前模块级词汇索引（惰性构建）。

    供 recall.py / capture.py 等非 pipeline 子系统使用，
    确保它们与 pipeline 共享同一份词汇表。
    """
    if not _vocab_index.built:
        _build_vocabulary()
    return _vocab_index


# ── 中文分词 ─────────────────────────────────────────────────────────


def _chinese_tokenize(text: str, vocab: frozenset[str]) -> list[str]:
    """中文最大正向匹配分词。"""
    if not vocab:
        return list(text)

    tokens: list[str] = []
    i = 0
    while i < len(text):
        if not ('一' <= text[i] <= '鿿' or '㐀' <= text[i] <= '䶿'):
            tokens.append(text[i])
            i += 1
            continue

        longest = text[i]
        max_len = min(4, len(text) - i)
        for j in range(max_len, 1, -1):
            candidate = text[i:i + j]
            if candidate in vocab:
                longest = candidate
                break

        if longest not in _ZH_STOP_WORDS:
            tokens.append(longest)
        i += len(longest)

    return tokens


def _tokenize(text: str, vocab: frozenset[str] | None = None) -> tuple[set[str], set[str]]:
    """将文本分词为 (word_tokens, char_bigrams)。

    - 中文：最大正向匹配 → word tokens
    - 英文/ASCII：word tokens
    - char_bigrams：保留作为 fallback
    """
    if not text:
        return set(), set()

    char_bigrams = {text[i:i + 2] for i in range(len(text) - 1)}

    if vocab is None:
        vocab = _vocab_index.vocab

    word_tokens: set[str] = set()

    for word in _ASCII_WORD_RE.findall(text.lower()):
        if word not in _EN_STOP_WORDS and len(word) >= 2:
            word_tokens.add(word)

    for cjk_run in _CJK_RE.findall(text):
        for token in _chinese_tokenize(cjk_run, vocab):
            if len(token) >= 2:
                word_tokens.add(token)

    return word_tokens, char_bigrams


# ── 相似度 ───────────────────────────────────────────────────────────


def _bigrams(text: str) -> set[str]:
    """Character 2-gram 分词。"""
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _jaccard(a: set, b: set) -> float:
    """Jaccard 相似度。"""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _tfidf_score(query_tokens: set[str], doc_tokens: set[str],
                 df: dict[str, int] | None = None, N: int = 1) -> float:
    """TF-IDF 加权评分。"""
    if not query_tokens or not doc_tokens:
        return 0.0

    overlap = query_tokens & doc_tokens
    if not overlap:
        return 0.0

    score = 0.0
    if df and N > 1:
        for token in overlap:
            tf = 1.0
            doc_freq = df.get(token, 1)
            idf = math.log(N / doc_freq) + 1.0
            score += tf * idf
    else:
        score = float(len(overlap))

    union = len(query_tokens | doc_tokens)
    if union > 0:
        score = score / union

    return score


def time_decay(age_days: float, half_life_days: float = 30) -> float:
    """指数时间衰减：weight = 0.5 ^ (age_days / half_life_days)。

    统一衰减公式：context 评分（pipeline/_decay_factor）与 memory 召回
    （recall._session_time_weight）共用，避免两处各自实现。
    """
    if age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


def _decay_factor(file_path: Path | None, half_life_days: int = 30) -> float:
    """指数时间衰减：weight = 0.5 ^ (age_days / half_life_days)。"""
    if file_path is None:
        return 1.0
    try:
        mtime = file_path.stat().st_mtime
        age_days = (time.time() - mtime) / 86400.0
        return time_decay(age_days, half_life_days)
    except (OSError, ValueError):
        return 1.0


# ── 同义词映射（跨模块共享：pipeline / recall）───────────────────────

SYNONYM_MAP: dict[str, list[str]] = {
    # 技术
    "代码": ["编程", "程序", "脚本", "code", "program", "script"],
    "文件": ["文档", "档案", "file", "document", "读写"],
    "设置": ["配置", "config", "settings", "偏好", "选项", "option"],
    "错误": ["bug", "异常", "error", "问题", "故障", "报错", "exception"],
    "部署": ["deploy", "发布", "上线", "release", "launch"],
    "测试": ["test", "单元测试", "集成测试", "验证", "verify", "check"],
    "数据库": ["database", "db", "查询", "存储", "query", "storage"],
    "前端": ["frontend", "UI", "界面", "网页", "web"],
    "后端": ["backend", "API", "服务端", "服务器", "server"],
    "性能": ["performance", "速度", "优化", "慢", "speed", "fast"],
    "安全": ["security", "权限", "加密", "认证", "auth", "permission"],
    "日志": ["log", "logging", "记录", "追踪", "trace", "track"],
    "缓存": ["cache", "redis", "memcache", "caching"],
    "容器": ["docker", "container", "k8s", "kubernetes"],
    "版本": ["version", "git", "升级", "更新", "upgrade", "update"],
    "安装": ["install", "setup", "配置环境", "environment"],
    "网络": ["network", "HTTP", "请求", "连接", "request", "connect"],
    "搜索": ["search", "查找", "检索", "grep", "find", "lookup"],
    # 偏好 / 风格
    "简洁": ["简短", "简明", "concise", "直接", "short", "brief"],
    "详细": ["详尽", "verbose", "具体", "detail", "specific"],
    "风格": ["偏好", "习惯", "style", "方式", "way", "approach"],
    "回复": ["回答", "响应", "response", "reply", "answer"],
    "注释": ["comment", "文档", "说明", "docstring", "注解"],
    "自动": ["auto", "自动化", "automate", "自动完成"],
    "手动": ["manual", "人工", "手写"],
    "安静": ["quiet", "安静点", "别吵", "少说话", "沉默"],
    "可靠": ["reliable", "稳定", "stable", "不要出错"],
}


def _get_all_synonyms(keyword: str) -> set[str]:
    """获取关键词的所有同义词。"""
    kw_lower = keyword.lower()
    for key, synonyms in SYNONYM_MAP.items():
        if kw_lower == key.lower() or kw_lower in (s.lower() for s in synonyms):
            return set(s.lower() for s in synonyms) | {key.lower()}
    return set()


def _expand_query(query: str, vocab: frozenset[str] | None = None) -> set[str]:
    """Tokenize 查询并展开同义词。

    同时检查原始查询字符串中的多词短语（tokenizer 可能切散）。

    Args:
        query: 用户查询字符串
        vocab: 词汇表（None 时使用模块级全局索引）
    """
    word_tokens, char_bigrams = _tokenize(query, vocab=vocab)
    terms: set[str] = {t.lower() for t in word_tokens} | {b.lower() for b in char_bigrams}

    for token in list(terms):
        synonyms = _get_all_synonyms(token)
        terms.update(synonyms)

    query_lower = query.lower()
    for key, synonyms in SYNONYM_MAP.items():
        key_lower = key.lower()
        if key_lower in query_lower or any(s.lower() in query_lower for s in synonyms):
            terms.add(key_lower)
            terms.update(s.lower() for s in synonyms)

    return terms
