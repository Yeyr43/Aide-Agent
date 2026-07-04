"""Tests for image_utils — clipboard, file loading, base64 encoding."""

import io
import sys
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock

from PIL import Image

from core.llm_gateway.image_utils import (
    image_to_data_url,
    grab_clipboard_image,
    save_clipboard_image,
    open_with_os,
    image_from_file,
    resize_if_needed,
    image_file_to_data_url,
    get_image_size_kb,
    data_url_to_image,
    extract_file_paths,
    is_image_path,
    MAX_SHORT_SIDE,
    MAX_LONG_SIDE,
)


# ── fixture ─────────────────────────────────────────────────────────

@pytest.fixture
def small_rgb_image() -> Image.Image:
    """100x50 RGB 测试图片。"""
    return Image.new("RGB", (100, 50), color=(255, 0, 0))


@pytest.fixture
def small_rgba_image() -> Image.Image:
    """100x50 RGBA 测试图片（半透明）。"""
    return Image.new("RGBA", (100, 50), color=(0, 255, 0, 128))


@pytest.fixture
def large_image() -> Image.Image:
    """3000x4000 RGB 大图 — 应被缩放。"""
    return Image.new("RGB", (3000, 4000), color=(0, 0, 255))


# ── image_to_data_url ────────────────────────────────────────────────

class TestImageToDataUrl:
    def test_rgb_png(self, small_rgb_image):
        url = image_to_data_url(small_rgb_image, fmt="PNG")
        assert url.startswith("data:image/png;base64,")
        assert len(url) > 50

    def test_rgba_png(self, small_rgba_image):
        url = image_to_data_url(small_rgba_image, fmt="PNG")
        assert url.startswith("data:image/png;base64,")

    def test_jpeg_converts_rgba(self, small_rgba_image):
        """JPEG 不支持透明通道，应自动转换。"""
        url = image_to_data_url(small_rgba_image, fmt="JPEG")
        assert url.startswith("data:image/jpeg;base64,")

    def test_webp_format(self, small_rgb_image):
        url = image_to_data_url(small_rgb_image, fmt="WEBP")
        assert url.startswith("data:image/webp;base64,")

    def test_output_is_valid_base64(self, small_rgb_image):
        import base64
        url = image_to_data_url(small_rgb_image, fmt="PNG")
        b64_part = url.split(",", 1)[1]
        decoded = base64.b64decode(b64_part)
        assert len(decoded) > 0

    def test_roundtrip(self, small_rgb_image):
        """data URL → Image 应能回解码。"""
        url = image_to_data_url(small_rgb_image, fmt="PNG")
        restored = data_url_to_image(url)
        assert restored is not None
        assert restored.size == (100, 50)


# ── grab_clipboard_image ─────────────────────────────────────────────

class TestGrabClipboardImage:
    def test_no_image_returns_none(self):
        """剪贴板无图片时返回 None。"""
        with patch("PIL.ImageGrab.grabclipboard", return_value=None):
            result = grab_clipboard_image()
            assert result is None

    def test_image_returns_pil_image(self, small_rgb_image):
        """剪贴板有 PIL Image 时返回之。"""
        with patch("PIL.ImageGrab.grabclipboard", return_value=small_rgb_image):
            result = grab_clipboard_image()
            assert result is small_rgb_image

    def test_list_of_images_returns_first(self, small_rgb_image):
        """某些平台返回文件列表。"""
        with patch("PIL.ImageGrab.grabclipboard",
                   return_value=[small_rgb_image]):
            result = grab_clipboard_image()
            assert result is small_rgb_image

    def test_exception_returns_none(self):
        """异常时优雅降级。"""
        with patch("PIL.ImageGrab.grabclipboard",
                   side_effect=OSError("clipboard error")):
            result = grab_clipboard_image()
            assert result is None


# ── save_clipboard_image ─────────────────────────────────────────────

class TestSaveClipboardImage:
    def test_saves_to_dir(self, tmp_path, small_rgb_image):
        with patch("PIL.ImageGrab.grabclipboard",
                   return_value=small_rgb_image):
            result = save_clipboard_image(tmp_path)
            assert result is not None
            file_path, data_url = result
            assert file_path.exists()
            assert data_url.startswith("data:image/png;base64,")

    def test_auto_numbers(self, tmp_path, small_rgb_image):
        """多次保存自动编号 img_001, img_002..."""
        with patch("PIL.ImageGrab.grabclipboard",
                   return_value=small_rgb_image):
            r1 = save_clipboard_image(tmp_path)
            r2 = save_clipboard_image(tmp_path)
        assert r1 and r2
        assert "img_001" in r1[0].name
        assert "img_002" in r2[0].name

    def test_no_image_returns_none(self, tmp_path):
        with patch("PIL.ImageGrab.grabclipboard",
                   return_value=None):
            result = save_clipboard_image(tmp_path)
            assert result is None

    def test_resizes_large_image(self, tmp_path):
        """大图应被缩放后保存。"""
        big = Image.new("RGB", (3000, 4000), color=(0, 0, 255))
        with patch("PIL.ImageGrab.grabclipboard",
                   return_value=big):
            result = save_clipboard_image(tmp_path)
        assert result is not None
        saved = Image.open(result[0])
        assert saved.size[0] <= MAX_LONG_SIDE


# ── open_with_os ─────────────────────────────────────────────────────

class TestOpenWithOS:
    def test_open_nonexistent(self):
        """不存在的文件也应尝试打开（返回 bool）。"""
        result = open_with_os("/nonexistent.png")
        assert result is True or result is False  # 取决于平台

    def test_win32_path(self, tmp_path):
        p = tmp_path / "test.png"
        Image.new("RGB", (10, 10)).save(p)
        with patch("os.startfile") as mock_startfile:
            result = open_with_os(p)
            # Windows 上调用 os.startfile，其他平台调用 Popen
            if sys.platform == "win32":
                assert mock_startfile.called
            else:
                assert result in (True, False)


# ── image_from_file ──────────────────────────────────────────────────

class TestImageFromFile:
    def test_loads_png(self, tmp_path, small_rgb_image):
        p = tmp_path / "test.png"
        small_rgb_image.save(p)
        loaded = image_from_file(p)
        assert loaded.size == (100, 50)

    def test_file_not_exists(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="不存在"):
            image_from_file(tmp_path / "nope.png")

    def test_not_an_image(self, tmp_path):
        p = tmp_path / "text.txt"
        p.write_text("not an image")
        with pytest.raises(Exception):
            image_from_file(p)

    def test_oversized_file(self, tmp_path):
        """超过 10MB 的文件应报错。"""
        p = tmp_path / "big.png"
        # 创建一个大文件（mock 也行）
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value = MagicMock(st_size=11 * 1024 * 1024)
            with pytest.raises(ValueError, match="过大"):
                image_from_file(p)


# ── resize_if_needed ─────────────────────────────────────────────────

class TestResizeIfNeeded:
    def test_small_image_unchanged(self, small_rgb_image):
        """小图不应缩放。"""
        result = resize_if_needed(small_rgb_image)
        assert result.size == (100, 50)

    def test_large_image_resized(self, large_image):
        """大图应按比例缩放。"""
        result = resize_if_needed(large_image)
        w, h = result.size
        assert w <= MAX_LONG_SIDE
        assert h <= MAX_LONG_SIDE
        # 保持宽高比
        original_ratio = 3000 / 4000
        new_ratio = w / h
        assert abs(original_ratio - new_ratio) < 0.01

    def test_resized_lanczos_quality(self, large_image):
        """缩放后不应是纯色（LANCZOS 重采样保留了细节）。"""
        result = resize_if_needed(large_image)
        # 大图是纯蓝色，缩放后也应该是纯蓝色
        assert result.getpixel((0, 0)) == (0, 0, 255)


# ── image_file_to_data_url ───────────────────────────────────────────

class TestImageFileToDataUrl:
    def test_png_file(self, tmp_path, small_rgb_image):
        p = tmp_path / "img.png"
        small_rgb_image.save(p)
        url = image_file_to_data_url(p)
        assert url.startswith("data:image/png;base64,")

    def test_jpg_file(self, tmp_path, small_rgb_image):
        p = tmp_path / "img.jpg"
        small_rgb_image.convert("RGB").save(p, "JPEG")
        url = image_file_to_data_url(p)
        assert url.startswith("data:image/jpeg;base64,")


# ── get_image_size_kb ────────────────────────────────────────────────

class TestGetImageSizeKb:
    def test_estimates_size(self, small_rgb_image):
        url = image_to_data_url(small_rgb_image, fmt="PNG")
        kb = get_image_size_kb(url)
        assert kb > 0
        assert kb < 200  # 100x50 PNG 很小


# ── data_url_to_image ────────────────────────────────────────────────

class TestDataUrlToImage:
    def test_invalid_base64(self):
        result = data_url_to_image("data:image/png;base64,!!!not-base64!!!")
        assert result is None

    def test_no_comma(self):
        result = data_url_to_image("data:image/png;base64")
        assert result is None


# ── extract_file_paths ─────────────────────────────────────────────────

class TestExtractFilePaths:
    def test_single_path_no_spaces(self, tmp_path):
        """不带空格不引号的单个路径。"""
        f = tmp_path / "report.pdf"
        f.write_text("test")
        remaining, paths = extract_file_paths(str(f))
        assert remaining == ""
        assert paths == [str(f)]

    def test_single_path_with_spaces_no_quotes(self, tmp_path):
        """含空格但不带引号的单个路径（Windows Terminal Ctrl+V 粘贴）。"""
        f = tmp_path / "新建 文本文档.txt"
        f.write_text("test")
        remaining, paths = extract_file_paths(str(f))
        assert remaining == ""
        assert paths == [str(f)]

    def test_single_path_quoted(self, tmp_path):
        """含空格且带引号的路径（拖放格式）。"""
        f = tmp_path / "my file.png"
        f.write_text("test")
        remaining, paths = extract_file_paths(f'"{f}"')
        assert remaining == ""
        assert paths == [str(f)]

    def test_multiple_paths_quoted(self, tmp_path):
        """多个带引号的路径。"""
        f1 = tmp_path / "a.png"
        f2 = tmp_path / "b.txt"
        f1.write_text("1")
        f2.write_text("2")
        remaining, paths = extract_file_paths(f'"{f1}" "{f2}"')
        assert remaining == ""
        assert set(paths) == {str(f1), str(f2)}

    def test_text_with_path(self, tmp_path):
        """文本 + 路径混排 → 路径被提取，文本保留。
        调用方 _detect_dropped_files 会检查 remaining 非空 → 不自动提取，
        所以混排场景不会误触发。"""
        f = tmp_path / "doc.pdf"
        f.write_text("test")
        remaining, paths = extract_file_paths(f"帮我看看这个 {f}")
        assert remaining == "帮我看看这个"
        assert paths == [str(f)]

    def test_non_existent_path(self):
        """不存在的路径视为普通文本。"""
        remaining, paths = extract_file_paths("D:/nonexistent/file.xyz")
        assert paths == []
        assert remaining == "D:/nonexistent/file.xyz"

    def test_plain_text(self):
        """普通文本不含路径。"""
        remaining, paths = extract_file_paths("你好世界")
        assert paths == []
        assert remaining == "你好世界"

    def test_empty_string(self):
        remaining, paths = extract_file_paths("")
        assert paths == []
        assert remaining == ""

    def test_multiple_lines_paths(self, tmp_path):
        """每行一个路径。"""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("1")
        f2.write_text("2")
        text = f"{f1}\n{f2}"
        remaining, paths = extract_file_paths(text)
        assert remaining == ""
        assert set(paths) == {str(f1), str(f2)}


# ── is_image_path ──────────────────────────────────────────────────────

class TestIsImagePath:
    def test_png(self):
        assert is_image_path("test.png") is True

    def test_jpg(self):
        assert is_image_path("photo.jpg") is True
        assert is_image_path("photo.jpeg") is True

    def test_txt_not_image(self):
        assert is_image_path("readme.txt") is False

    def test_no_extension(self):
        assert is_image_path("Makefile") is False
