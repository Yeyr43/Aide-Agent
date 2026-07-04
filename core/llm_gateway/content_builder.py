"""多模态用户消息 content 构建。

将文本 + 文件路径列表转换为 OpenAI content 格式（str 或 list[dict]）。
"""

from __future__ import annotations

from .image_utils import is_image_path, image_file_to_data_url


def build_user_content(text: str, file_paths: list[str]) -> str | list[dict]:
    """构建多模态用户消息 content。

    纯文本 → str（向后兼容）。
    有图片 → OpenAI content 数组（文本 + data URL）。
    非图片文件路径已在 text 中（由 InputBox._post_submit 替换），不再追加。
    """
    if not file_paths:
        return text

    parts: list[dict] = []
    img_paths: list[str] = []

    for p in file_paths:
        if is_image_path(p):
            img_paths.append(p)

    if not img_paths:
        return text  # 无图片 → 纯文本

    # 有图片 → content 数组
    if text:
        parts.append({"type": "text", "text": text})
    for path in img_paths:
        data_url = image_file_to_data_url(path)
        parts.append({"type": "image_url", "image_url": {"url": data_url}})
    return parts
