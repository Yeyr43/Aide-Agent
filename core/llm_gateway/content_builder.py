"""多模态用户消息 content 构建。

将文本 + 文件路径列表转换为 OpenAI content 格式（str 或 list[dict]）。
"""

from __future__ import annotations

import logging

from .image_utils import is_image_path, image_file_to_data_url

logger = logging.getLogger(__name__)

MAX_IMAGES_PER_MESSAGE = 20


def build_user_content(text: str, file_paths: list[str]) -> str | list[dict]:
    """构建多模态用户消息 content。

    纯文本 → str（向后兼容）。
    有图片 → OpenAI content 数组（文本 + data URL）。
    非图片文件路径已在 text 中（由 InputBox._post_submit 替换），不再追加。

    单张图片加载失败仅跳过该图片并附警告，不会导致整条消息崩溃。
    单次最多 {MAX_IMAGES_PER_MESSAGE} 张图片，超出部分截断并提示。
    """
    if not file_paths:
        return text

    # 过滤出图片路径
    img_paths: list[str] = [p for p in file_paths if is_image_path(p)]
    if not img_paths:
        return text

    # 数量上限
    truncated: list[str] = []
    if len(img_paths) > MAX_IMAGES_PER_MESSAGE:
        truncated = img_paths[MAX_IMAGES_PER_MESSAGE:]
        img_paths = img_paths[:MAX_IMAGES_PER_MESSAGE]

    # 逐张加载，失败跳过
    parts: list[dict] = []
    failed: list[str] = []
    for path in img_paths:
        try:
            data_url = image_file_to_data_url(path)
            parts.append({"type": "image_url", "image_url": {"url": data_url}})
        except Exception as e:
            logger.warning("Failed to load image %s: %s", path, e)
            failed.append(path)

    # 构建文本前缀
    notes: list[str] = []
    if text:
        notes.append(text)
    if failed:
        names = [p.split("/")[-1].split("\\")[-1] for p in failed]
        notes.append(f"[{len(failed)} 张图片加载失败: {', '.join(names)}]")
    if truncated:
        names = [p.split("/")[-1].split("\\")[-1] for p in truncated]
        notes.append(f"[超出单次 {MAX_IMAGES_PER_MESSAGE} 张上限，已跳过: {', '.join(names)}]")

    if not parts:
        # 全部加载失败 → 纯文本
        return " ".join(notes) if notes else text

    # 有成功加载的图片 → content 数组
    if notes:
        parts.insert(0, {"type": "text", "text": " ".join(notes)})
    return parts
