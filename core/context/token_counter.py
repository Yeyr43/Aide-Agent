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
