"""EmbeddingEngine — 轻量语义嵌入层。

基于 ONNX Runtime + 极小 WordPiece 分词器。
模型首次使用时自动从 HuggingFace 下载。
嵌入缓存于内存，避免重复推理。

用法:
    engine = EmbeddingEngine()
    if engine.available:
        v = engine.embed("帮我写个脚本")
        sim = engine.similarity("部署", "发布到线上")
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from urllib import request
from urllib.error import URLError

import numpy as np

from core.setup import aide_dir
from core.resources import get_resource_path

logger = logging.getLogger(__name__)

# ── 默认模型配置 ─────────────────────────────────────────────────────────

# all-MiniLM-L6-v2: 23 MB, 384-dim, 中英文兼顾
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_URL = f"https://huggingface.co/{MODEL_ID}/resolve/main/onnx/model.onnx"
VOCAB_URL = f"https://huggingface.co/{MODEL_ID}/resolve/main/vocab.txt"

# 优先从 bundle 加载，否则使用 ~/.aide/models/
_BUNDLE_MODEL_DIR = get_resource_path("models") / "all-MiniLM-L6-v2"
_USER_MODEL_DIR = aide_dir() / "models" / "all-MiniLM-L6-v2"

if (_BUNDLE_MODEL_DIR / "model.onnx").exists():
    MODEL_DIR = _BUNDLE_MODEL_DIR
else:
    MODEL_DIR = _USER_MODEL_DIR

MODEL_PATH = MODEL_DIR / "model.onnx"
VOCAB_PATH = MODEL_DIR / "vocab.txt"
EMBEDDING_DIM = 384
MAX_LENGTH = 128

# ── 极简 WordPiece 分词器 ────────────────────────────────────────────────


class _WordPieceTokenizer:
    """BERT/MiniLM WordPiece 分词器（纯 Python，无外部依赖）。

    加载 vocab.txt 文件（一行一个 token），
    实现最长匹配优先的 WordPiece 算法，支持中英混合。
    """

    def __init__(self, vocab_path: Path) -> None:
        self.vocab: dict[str, int] = {}
        self.ids_to_tokens: dict[int, str] = {}

        with open(vocab_path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                token = line.strip()
                self.vocab[token] = i
                self.ids_to_tokens[i] = token

        self.unk_id = self.vocab.get("[UNK]", 100)
        self.cls_id = self.vocab.get("[CLS]", 101)
        self.sep_id = self.vocab.get("[SEP]", 102)
        self.pad_id = self.vocab.get("[PAD]", 0)

    # ── 预分词：按 CJK 字符 / 标点 / 空白切分 ──────────────────────

    @staticmethod
    def _basic_tokenize(text: str) -> list[str]:
        """预分词：CJK 单字切开，ASCII 按空白和标点切分。"""
        tokens: list[str] = []
        buf = ""
        for ch in text:
            if ch.isspace():
                if buf:
                    tokens.append(buf)
                    buf = ""
            elif "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿":
                # CJK 字符：每个字独立
                if buf:
                    tokens.append(buf)
                    buf = ""
                tokens.append(ch)
            elif ch in "，。！？；：、（）【】《》「」\"'…—–,.;:!?()[]{}":
                if buf:
                    tokens.append(buf)
                    buf = ""
                tokens.append(ch)
            else:
                buf += ch
        if buf:
            tokens.append(buf)
        return [t for t in tokens if t]

    # ── WordPiece 子词切分 ──────────────────────────────────────────

    def _wordpiece(self, token: str) -> list[str]:
        """对单个 token 执行 WordPiece 最长匹配。

        如果不是首 token（即加了 ## 前缀的），先尝试整体匹配，
        失败则从右向左逐步缩短。
        """
        if len(token) > 100:
            return ["[UNK]"]

        # 已存在于词表
        if token in self.vocab:
            return [token]

        sub_tokens: list[str] = []
        start = 0
        while start < len(token):
            end = len(token)
            found = False
            while start < end:
                sub = token[start:end]
                # 非首片段加 ## 前缀
                lookup = f"##{sub}" if start > 0 else sub
                if lookup in self.vocab:
                    sub_tokens.append(lookup)
                    found = True
                    break
                end -= 1
            if not found:
                sub_tokens.append("[UNK]")
                break
            start = end

        return sub_tokens

    # ── 编码 ─────────────────────────────────────────────────────────

    def encode(
        self, text: str, max_length: int = MAX_LENGTH,
    ) -> tuple[list[int], list[int], list[int]]:
        """编码文本为 (input_ids, attention_mask, token_type_ids)。

        Returns:
            (input_ids, attention_mask, token_type_ids)
        """
        # 预分词
        basic_tokens: list[str] = []
        for t in self._basic_tokenize(text.lower()):
            # 跳过纯标点
            if len(t) == 1 and t in "，。！？；：、…—–,.;:!?":
                continue
            basic_tokens.append(t)

        # WordPiece
        wp_tokens: list[str] = []
        for i, token in enumerate(basic_tokens):
            if i == 0:
                wp_tokens.extend(self._wordpiece(token))
            else:
                for sub in self._wordpiece(token):
                    wp_tokens.append(sub if sub.startswith("##") or sub == "[UNK]" else f"##{sub}")

        # 截断
        wp_tokens = wp_tokens[:max_length - 2]

        # 构建 ids
        ids = [self.cls_id]
        for t in wp_tokens:
            ids.append(self.vocab.get(t, self.unk_id))
        ids.append(self.sep_id)

        # Padding
        seq_len = len(ids)
        pad_len = max_length - seq_len
        attention_mask = [1] * seq_len + [0] * pad_len
        token_type_ids = [0] * max_length
        ids += [self.pad_id] * pad_len

        return ids, attention_mask, token_type_ids


# ── 模型下载器 ────────────────────────────────────────────────────────────


def _download_file(url: str, dest: Path, desc: str = "") -> None:
    """下载文件到指定路径，支持断点。"""
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        logger.debug(f"{desc} 已存在: {dest}")
        return

    logger.info(f"下载 {desc}: {url}")
    try:
        req = request.Request(url, headers={"User-Agent": "Aide/0.1"})
        with request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        dest.write_bytes(data)
        logger.info(f"{desc} 下载完成: {len(data) / 1024:.0f} KB")
    except URLError as e:
        # 清理不完整文件
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"下载 {desc} 失败: {e}") from e


def ensure_model() -> tuple[Path, Path]:
    """确保模型文件和词表存在，不存在则下载。

    Returns:
        (model_path, vocab_path)
    """
    if not MODEL_PATH.exists():
        _download_file(MODEL_URL, MODEL_PATH, "ONNX 模型")
    if not VOCAB_PATH.exists():
        _download_file(VOCAB_URL, VOCAB_PATH, "词表")
    return MODEL_PATH, VOCAB_PATH


def is_model_available() -> bool:
    """检查模型是否可用（已下载）。"""
    return MODEL_PATH.exists() and VOCAB_PATH.exists()


# ── EmbeddingEngine ───────────────────────────────────────────────────────


class EmbeddingEngine:
    """语义嵌入引擎。

    用法:
        engine = EmbeddingEngine()
        if engine.available:
            v = engine.embed("文本")
            sim = engine.similarity("文本A", "文本B")
    """

    def __init__(self) -> None:
        self._session = None
        self._tokenizer: _WordPieceTokenizer | None = None
        self._cache: dict[str, np.ndarray] = {}  # text_hash → embedding
        self._available: bool | None = None
        self._init()

    def _init(self) -> None:
        """尝试初始化 ONNX session 和分词器。"""
        try:
            import onnxruntime as ort  # noqa: F811
        except ImportError:
            logger.info("onnxruntime 未安装，嵌入引擎不可用")
            self._available = False
            return

        if not is_model_available():
            logger.info("嵌入模型未下载，嵌入引擎不可用")
            self._available = False
            return

        try:
            self._session = ort.InferenceSession(str(MODEL_PATH))
            self._tokenizer = _WordPieceTokenizer(VOCAB_PATH)
            self._available = True
            logger.info("EmbeddingEngine 初始化完成")
        except Exception as e:
            logger.warning(f"EmbeddingEngine 初始化失败: {e}")
            self._available = False

    @property
    def available(self) -> bool:
        """嵌入引擎是否可用。"""
        if self._available is None:
            return False
        return self._available

    @property
    def dim(self) -> int:
        return EMBEDDING_DIM

    # ── 嵌入 ─────────────────────────────────────────────────────────

    @staticmethod
    def _text_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def embed(self, text: str) -> np.ndarray | None:
        """编码文本为嵌入向量。

        Returns:
            numpy array shape=(384,)，失败返回 None
        """
        if not self.available or self._session is None or self._tokenizer is None:
            return None

        key = self._text_hash(text)
        if key in self._cache:
            return self._cache[key]

        try:
            input_ids, attention_mask, token_type_ids = self._tokenizer.encode(text)

            ort_inputs = {
                "input_ids": np.array([input_ids], dtype=np.int64),
                "attention_mask": np.array([attention_mask], dtype=np.int64),
                "token_type_ids": np.array([token_type_ids], dtype=np.int64),
            }

            # ONNX 推理
            outputs = self._session.run(None, ort_inputs)
            # 取 [CLS] 位置的输出或 mean pooling
            embedding = outputs[0][0]  # shape (384,) or (128, 384)

            # Mean pooling: average over all non-padding tokens
            if embedding.ndim == 2:
                mask = np.array(attention_mask, dtype=np.float32)
                embedding = (embedding * mask[:, None]).sum(axis=0) / mask.sum()

            # L2 normalize
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm

            self._cache[key] = embedding
            return embedding
        except Exception as e:
            logger.warning(f"嵌入编码失败: {e}")
            return None

    def batch_embed(self, texts: list[str]) -> list[np.ndarray | None]:
        """批量编码，逐个处理（ONNX 不支持动态 batch 时 fallback）。"""
        return [self.embed(t) for t in texts]

    # ── 相似度 ───────────────────────────────────────────────────────

    def similarity(self, a: str, b: str) -> float:
        """计算两个文本的语义相似度。

        Returns:
            cosine similarity (0.0 ~ 1.0)，失败返回 0.0
        """
        va = self.embed(a)
        vb = self.embed(b)
        if va is None or vb is None:
            return 0.0
        return float(np.dot(va, vb))

    # ── 批量排序 ─────────────────────────────────────────────────────

    def rank(
        self,
        query: str,
        documents: list[str],
        top_k: int | None = None,
    ) -> list[tuple[int, float]]:
        """对文档列表按与 query 的语义相似度排序。

        Returns:
            [(doc_index, similarity_score), ...] 降序排列
        """
        q_emb = self.embed(query)
        if q_emb is None:
            return []

        scored: list[tuple[int, float]] = []
        for i, doc in enumerate(documents):
            d_emb = self.embed(doc)
            if d_emb is not None:
                sim = float(np.dot(q_emb, d_emb))
                scored.append((i, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        if top_k is not None:
            scored = scored[:top_k]
        return scored

    # ── 缓存管理 ─────────────────────────────────────────────────────

    def clear_cache(self) -> None:
        self._cache.clear()

    @property
    def cache_size(self) -> int:
        return len(self._cache)


# ── 模块级单例 ────────────────────────────────────────────────────────────

_engine: EmbeddingEngine | None = None


def get_embedding_engine() -> EmbeddingEngine:
    """获取模块级 EmbeddingEngine 单例。"""
    global _engine
    if _engine is None:
        _engine = EmbeddingEngine()
    return _engine
