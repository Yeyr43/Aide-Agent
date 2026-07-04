"""图片工具 — 剪贴板捕获、文件加载、base64 编码。

为多模态对话提供图片 → data URL 转换。PIL.ImageGrab 跨平台
(Windows/macOS 有原生支持，Linux 需要 xclip/wl-clipboard)。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

# 最大尺寸约束 — 避免 base64 字符串撑爆 LLM 上下文
MAX_SHORT_SIDE = 768   # 短边超过此值按比例缩放
MAX_LONG_SIDE = 2000   # 长边超过此值按比例缩放
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10MB — 与 OpenAI 单张图片限制一致

# 常见图片扩展名 → MIME type
_EXT_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

# 已知图片扩展名集合（小写）
_IMAGE_EXTENSIONS = frozenset(_EXT_TO_MIME.keys())


def is_image_path(path: str | Path) -> bool:
    """判断路径是否为图片文件（按扩展名）。"""
    return Path(path).suffix.lower() in _IMAGE_EXTENSIONS


def extract_file_paths(text: str) -> tuple[str, list[str]]:
    """从文本中提取文件路径。

    支持 Windows 拖放格式：
      "D:/a/b.png" "D:/c/d.txt"  →  ("", ["D:/a/b.png", "D:/c/d.txt"])
    也支持含空格的不带引号路径（Windows Terminal 粘贴常见）:
      D:\a\b\新建 文本文档.txt  →  ("", ["D:\a\b\新建 文本文档.txt"])
    非文件路径的文本原样返回。

    Returns:
        (剩余文本, 存在的文件路径列表)
    """
    import shlex
    import sys

    paths: list[str] = []
    remaining_parts: list[str] = []

    text = text.strip()

    # 先尝试整段文本作为一个路径（处理含空格但不带引号的 Windows 路径）
    if text:
        p = Path(text.strip('"'))
        if p.exists() and p.is_file():
            return "", [str(p)]

    # 再尝试按引号分割（Windows Terminal 拖放格式）
    tokens: list[str]
    if '"' in text:
        try:
            tokens = shlex.split(text, posix=(sys.platform != "win32"))
        except ValueError:
            tokens = text.split()
    else:
        # 按行或空格分割
        if "\n" in text:
            tokens = [t.strip() for t in text.split("\n") if t.strip()]
        else:
            tokens = text.split()

    for token in tokens:
        p = Path(token.strip('"'))
        if p.exists() and p.is_file():
            paths.append(str(p))
        else:
            remaining_parts.append(token)

    remaining = " ".join(remaining_parts) if remaining_parts else ""
    return remaining, paths


def image_to_data_url(image: Image.Image, fmt: str = "PNG") -> str:
    """PIL Image → data:image/...;base64,... URL。

    Args:
        image: PIL Image 对象
        fmt: 输出格式 ("PNG", "JPEG", "WEBP", "GIF")

    Returns:
        完整的 data URL 字符串
    """
    buf = io.BytesIO()
    save_fmt = fmt.upper()
    # JPEG 不支持 RGBA，需转换
    if save_fmt == "JPEG" and image.mode in ("RGBA", "P"):
        image = image.convert("RGB")
    # GIF 需要处理 palette 模式
    if save_fmt == "GIF" and image.mode == "RGBA":
        image = image.convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE)
    image.save(buf, format=save_fmt)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    mime = f"image/{save_fmt.lower()}"
    return f"data:{mime};base64,{b64}"


def grab_clipboard_image() -> Image.Image | None:
    """从系统剪贴板获取图片。headless Linux 安全。

    Returns:
        PIL Image 对象，无图片时返回 None
    """
    try:
        from PIL import ImageGrab
        img = ImageGrab.grabclipboard()
        if isinstance(img, Image.Image):
            return img
        # 某些平台可能返回 list（多文件）
        if isinstance(img, list) and len(img) > 0:
            first = img[0]
            if isinstance(first, Image.Image):
                return first
        return None
    except Exception:
        return None


def save_clipboard_image(save_dir: str | Path) -> tuple[Path, str] | None:
    """保存剪贴板图片到指定目录，返回 (文件路径, data_url)。

    Args:
        save_dir: 保存目录（会自动创建）

    Returns:
        (file_path, data_url) 或 None（无图片）
    """
    img = grab_clipboard_image()
    if img is None:
        return None

    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    # 自动编号文件名
    existing = list(save_dir.glob("img_*.png"))
    next_num = len(existing) + 1
    file_path = save_dir / f"img_{next_num:03d}.png"

    img = resize_if_needed(img)
    img.save(file_path, format="PNG")

    url = image_to_data_url(img, fmt="PNG")
    return file_path, url


def open_with_os(file_path: str | Path) -> bool:
    """用操作系统默认程序打开文件。

    Args:
        file_path: 文件路径

    Returns:
        True 如果成功启动
    """
    import os
    import subprocess
    import sys
    path = str(file_path)
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


def image_from_file(path: str | Path) -> Image.Image:
    """从文件路径加载图片。

    Args:
        path: 图片文件路径

    Returns:
        PIL Image 对象（RGB 或 RGBA）

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 文件不是有效图片
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"图片文件不存在: {path}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(
            f"图片文件过大: {path.stat().st_size / 1024 / 1024:.1f}MB "
            f"(上限 {MAX_FILE_BYTES / 1024 / 1024:.0f}MB)"
        )
    img = Image.open(path)
    # 确保图片已加载（Image.open 是 lazy 的）
    img.load()
    return img


def resize_if_needed(
    image: Image.Image,
    max_short_side: int = MAX_SHORT_SIDE,
    max_long_side: int = MAX_LONG_SIDE,
) -> Image.Image:
    """按比例缩放大图，避免 base64 字符串过大。

    Args:
        image: 原始图片
        max_short_side: 短边最大像素
        max_long_side: 长边最大像素

    Returns:
        缩放后的图片（可能为原图）
    """
    w, h = image.size
    short, long = min(w, h), max(w, h)

    if short <= max_short_side and long <= max_long_side:
        return image

    # 按较长边约束计算缩放比例
    scale = min(max_short_side / short, max_long_side / long)
    new_w, new_h = int(w * scale), int(h * scale)

    # 使用高质量重采样
    return image.resize((new_w, new_h), Image.LANCZOS)


def image_file_to_data_url(path: str | Path) -> str:
    """便捷函数：图片文件 → data URL。

    Args:
        path: 图片文件路径

    Returns:
        data:image/...;base64,... URL
    """
    img = image_from_file(path)
    img = resize_if_needed(img)
    ext = Path(path).suffix.lower()
    fmt = _EXT_TO_MIME.get(ext, "image/png")
    # 从 MIME "image/png" 提取格式名 "PNG"
    fmt_name = fmt.split("/")[-1].upper()
    return image_to_data_url(img, fmt=fmt_name)


def get_image_size_kb(data_url: str) -> float:
    """估算 data URL 中图片的大小（KB）。

    Args:
        data_url: data:image/...;base64,... 字符串

    Returns:
        近似大小（KB）
    """
    # base64 部分占 ~4/3 的原始字节
    try:
        b64_part = data_url.split(",", 1)[1]
        raw_bytes = len(b64_part) * 3 / 4
        return raw_bytes / 1024
    except (IndexError, ValueError):
        return 0.0


def data_url_to_image(data_url: str) -> Image.Image | None:
    """从 data URL 解码回 PIL Image（用于显示占位符信息）。

    Args:
        data_url: data:image/...;base64,... 字符串

    Returns:
        PIL Image 对象，解码失败返回 None
    """
    try:
        b64_part = data_url.split(",", 1)[1]
        raw = base64.b64decode(b64_part)
        return Image.open(io.BytesIO(raw))
    except Exception:
        return None


def save_images_to_session(images: list[Image.Image], session_dir: str | Path) -> list[str]:
    """将 PIL Image 列表保存到 session 的 images/ 目录。

    Args:
        images: PIL Image 对象列表
        session_dir: 会话目录路径

    Returns:
        保存后的文件路径列表
    """
    img_dir = Path(session_dir) / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    existing = list(img_dir.glob("img_*.png"))
    next_num = len(existing) + 1
    for img in images:
        file_path = img_dir / f"img_{next_num:03d}.png"
        img = resize_if_needed(img)
        img.save(file_path, format="PNG")
        saved.append(str(file_path))
        next_num += 1
    return saved
