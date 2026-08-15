"""Token 估算 — 基于字符类型的粗略 token 计数。

用于 UI 状态栏显示上下文窗口使用率，不要求精确。
CJK 字符约占 1.5 个 token，非 CJK 约占 4 个字符/token。
图片按分辨率估算（低分辨率 85 token，高分辨率按 512² 瓦片计算）。

计数对象是实际发送给 LLM 的 messages 列表（含 system prompt + 裁剪后对话），
而非全量 conversation。tool schema JSON 也纳入计数。
"""

from __future__ import annotations

import base64
import json
from io import BytesIO

# 默认上下文窗口大小（token），可通过 compute_context_usage 的 context_window 参数覆盖
DEFAULT_CONTEXT_WINDOW = 128000

# 图片 token 估算常量（对标 OpenAI Vision 计费规则）
_IMAGE_TOKEN_AUTO = 85        # auto / low-res 模式
_IMAGE_TOKEN_PER_TILE = 170   # 每 512×512 瓦片
_TILE_SIZE = 512


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数量。

    CJK 字符（含全角标点）按 ~1.5 字符/token，
    非 CJK 字符按 ~4 字符/token。
    """
    cjk = 0
    non_cjk = 0
    for ch in text:
        if '一' <= ch <= '鿿' or '　' <= ch <= 'ヿ' or '＀' <= ch <= '￯':
            cjk += 1
        else:
            non_cjk += 1
    return int(cjk / 1.5 + non_cjk / 4)


def _estimate_image_tokens(data_url: str) -> int:
    """估算单张图片的 token 消耗。

    尝试从 base64 数据解码尺寸，按 512×512 瓦片计算。
    失败时回退到 auto 模式（85 token）。

    OpenAI Vision 规则：
      - auto / low-res → 85 token
      - high-res → 85 + 170 × tiles（每个 512×512 瓦片）
    """
    try:
        if "," not in data_url:
            return _IMAGE_TOKEN_AUTO
        _header, b64 = data_url.split(",", 1)
        img_data = base64.b64decode(b64)
        from PIL import Image
        img = Image.open(BytesIO(img_data))
        w, h = img.size
        tiles_x = (w + _TILE_SIZE - 1) // _TILE_SIZE
        tiles_y = (h + _TILE_SIZE - 1) // _TILE_SIZE
        tiles = tiles_x * tiles_y
        if tiles <= 1:
            return _IMAGE_TOKEN_AUTO
        return _IMAGE_TOKEN_AUTO + tiles * _IMAGE_TOKEN_PER_TILE
    except Exception:
        return _IMAGE_TOKEN_AUTO


def _extract_content_text_and_images(content) -> tuple[str, int]:
    """从消息 content（str 或 list[dict]）提取纯文本 + 图片 token 数。

    base64 图片数据不计入文本（否则字符估算法会严重虚高），
    改为按分辨率估算图片 token。

    Returns:
        (text, image_tokens)
    """
    if isinstance(content, str):
        return content, 0
    if not isinstance(content, list):
        return str(content), 0

    text_parts: list[str] = []
    image_tokens = 0
    for block in content:
        block_type = block.get("type", "")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "image_url":
            url = block.get("image_url", {}).get("url", "")
            image_tokens += _estimate_image_tokens(url)
    return " ".join(text_parts), image_tokens


def compute_context_usage(
    messages: list[dict],
    tools_schema: list[dict] | None = None,
    context_window: int = DEFAULT_CONTEXT_WINDOW,
) -> tuple[int, float]:
    """计算实际发送给 LLM 的上下文 token 用量。

    计数内容 = system prompt（已组装）+ 裁剪后的对话 + tool schema JSON。
    图片按分辨率估算，不计 base64 字符。

    Args:
        messages: 实际发送给 LLM 的 messages 列表（已含 system + trimmed_conv）
        tools_schema: 工具 schema 列表（OpenAI 格式），None 表示无工具
        context_window: 上下文窗口大小（token），0 表示不限制（返回 pct=0）

    Returns:
        (estimated_tokens, usage_pct) — pct 为 0.0~1.0，无限制时 pct=0
    """
    total_text = ""
    total_image_tokens = 0

    # 实际发送的 messages（system + 裁剪后对话）
    for msg in messages:
        text, img_tokens = _extract_content_text_and_images(msg.get("content", ""))
        total_text += text
        total_image_tokens += img_tokens

    # tool schema JSON
    if tools_schema:
        try:
            total_text += json.dumps(tools_schema, ensure_ascii=False)
        except (TypeError, ValueError):
            # mock 对象或非标准 schema（测试环境常见）
            pass

    estimated = estimate_tokens(total_text) + total_image_tokens
    if context_window <= 0:
        pct = 0.0  # 不限制
    else:
        pct = min(estimated / context_window, 1.0)
    return estimated, pct


# ── 上下文爆满兜底（纯机械降级，不调 LLM）────────────────────────────

def _split_turns(messages: list[dict]) -> list[list[dict]]:
    """把扁平消息列表切成轮次（每个 user 消息开启一轮）。

    与 pipeline._split_turns 同构，但保持本模块独立（避免跨模块循环依赖）。
    """
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


def trim_conversation_to_window(
    system_msgs: list[dict],
    conv: list[dict],
    context_window: int,
    tools_schema: list[dict] | None = None,
    ratio: float = 0.9,
) -> list[dict]:
    """上下文超预算兜底：估算 system+conv（+schema）超 window×ratio 时，
    从头部逐轮丢弃最老轮次，直到不超预算。纯机械降级，不调 LLM。

    不用于常规压缩——Aide 上下文靠轮数限制（older/recent split），
    此函数只在"recent 全文 + system 仍超窗口"的极端情况下兜底，
    避免 400 prompt-too-long。

    Args:
        system_msgs: system 消息（仅参与估算，不参与丢弃）
        conv: 裁剪后的对话（system_msgs + conv = 实际发送内容）
        context_window: 上下文窗口（token），0 表示不限制（原样返回）
        tools_schema: tool schema JSON（估算用，None 表示无）
        ratio: 预算比例（留出输出余量），默认 0.9

    Returns:
        修剪后的 conv。预算充足时原样返回（幂等）。
    """
    if context_window <= 0:
        return conv
    budget = int(context_window * ratio)

    def _est(msgs: list[dict]) -> int:
        estimated, _ = compute_context_usage(
            system_msgs + msgs, tools_schema, context_window=0,
        )
        return estimated

    if _est(conv) <= budget:
        return conv  # 未超预算，不动

    # 从最老轮次整组丢弃，直到不超预算
    kept = list(_split_turns(conv))
    while len(kept) > 1:
        kept = kept[1:]
        if _est([m for t in kept for m in t]) <= budget:
            break

    result = [m for t in kept for m in t]

    # 只剩 1 轮仍超 → 截断该轮 assistant 正文（user 消息保留，语义不丢）
    if _est(result) > budget:
        result = _truncate_last_turn_to_budget(result, budget, _est)
    return result


def _truncate_last_turn_to_budget(
    messages: list[dict],
    budget: int,
    est_fn,
) -> list[dict]:
    """截断 assistant 正文直到估算 ≤ budget（从最后一条 assistant 向前）。

    每条 assistant 内容持续减半（下限 100 字符），到下限后处理更早的。
    仅剩 user 消息仍超预算的极端情况放弃截断（语义优先）。
    """
    result = [dict(m) for m in messages]
    assistant_idx = [
        i for i, m in enumerate(result)
        if m.get("role") == "assistant" and isinstance(m.get("content"), str)
    ]

    while est_fn(result) > budget and assistant_idx:
        i = assistant_idx[-1]
        content = result[i]["content"]
        if len(content) <= 100:
            assistant_idx.pop()  # 已到下限，处理更早的 assistant
            continue
        keep = max(len(content) // 2, 100)
        result[i]["content"] = content[:keep]
    return result
