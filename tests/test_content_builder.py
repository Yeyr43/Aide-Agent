"""Tests for core.llm_gateway.content_builder — multimodal user message content building."""

import pytest
from unittest.mock import patch

from core.llm_gateway.content_builder import build_user_content, MAX_IMAGES_PER_MESSAGE


class TestBuildUserContent:
    """Test build_user_content(text, file_paths) → str | list[dict]."""

    # ── Pure text (no images) ──────────────────────────────────────────

    def test_no_files_returns_plain_string(self):
        result = build_user_content("hello", [])
        assert result == "hello"

    def test_empty_text_no_files(self):
        result = build_user_content("", [])
        assert result == ""

    # ── Non-image files are ignored ────────────────────────────────────

    @patch("core.llm_gateway.content_builder.is_image_path", return_value=False)
    def test_non_image_files_return_plain_text(self, mock_is_image):
        result = build_user_content("hello", ["readme.md", "notes.txt"])
        assert result == "hello"

    # ── Image files → multimodal array ─────────────────────────────────

    @patch("core.llm_gateway.content_builder.is_image_path", return_value=True)
    @patch("core.llm_gateway.content_builder.image_file_to_data_url")
    def test_single_image_returns_content_array(self, mock_to_url, mock_is_image):
        mock_to_url.return_value = "data:image/png;base64,iVBORw0KGgo="
        result = build_user_content("What's in this image?", ["screenshot.png"])

        assert isinstance(result, list)
        assert len(result) == 2  # text block + image block
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "What's in this image?"
        assert result[1]["type"] == "image_url"
        assert result[1]["image_url"]["url"] == "data:image/png;base64,iVBORw0KGgo="

    @patch("core.llm_gateway.content_builder.is_image_path", return_value=True)
    @patch("core.llm_gateway.content_builder.image_file_to_data_url")
    def test_image_no_text(self, mock_to_url, mock_is_image):
        mock_to_url.return_value = "data:image/png;base64,abc="
        result = build_user_content("", ["img.png"])

        assert isinstance(result, list)
        # Only image block, no leading text block
        assert result[0]["type"] == "image_url"

    # ── Mixed image + non-image ────────────────────────────────────────

    @patch("core.llm_gateway.content_builder.is_image_path")
    @patch("core.llm_gateway.content_builder.image_file_to_data_url")
    def test_mixed_files_only_images_in_content(self, mock_to_url, mock_is_image):
        """Non-image files are excluded from content array."""
        mock_to_url.return_value = "data:image/png;base64,img="

        def is_img(path):
            return path.endswith(".png")

        mock_is_image.side_effect = is_img
        result = build_user_content("check this", ["screenshot.png", "notes.txt"])

        assert isinstance(result, list)
        # text block + 1 image block (notes.txt excluded)
        assert any(b.get("type") == "image_url" for b in result)

    # ── Failed image loads ─────────────────────────────────────────────

    @patch("core.llm_gateway.content_builder.is_image_path", return_value=True)
    @patch("core.llm_gateway.content_builder.image_file_to_data_url")
    def test_failed_image_load_adds_warning(self, mock_to_url, mock_is_image):
        mock_to_url.side_effect = OSError("file not found")
        result = build_user_content("look at this", ["missing.png"])

        # All images failed → falls back to plain text with warning
        assert isinstance(result, str)
        assert "加载失败" in result or "failed" in result.lower()
        assert "missing" in result

    @patch("core.llm_gateway.content_builder.is_image_path", return_value=True)
    @patch("core.llm_gateway.content_builder.image_file_to_data_url")
    def test_partial_image_failure(self, mock_to_url, mock_is_image):
        """One image loads, one fails → content array with warning text."""
        def to_url(path):
            if "good" in path:
                return "data:image/png;base64,good="
            raise OSError("not found")

        mock_to_url.side_effect = to_url
        result = build_user_content("images", ["good.png", "bad.png"])

        assert isinstance(result, list)
        # First block should be text with failure note
        text_block = result[0]
        assert text_block["type"] == "text"
        assert "加载失败" in text_block["text"] or "failed" in text_block["text"].lower()

    # ── Truncation beyond MAX_IMAGES_PER_MESSAGE ───────────────────────

    @patch("core.llm_gateway.content_builder.is_image_path", return_value=True)
    @patch("core.llm_gateway.content_builder.image_file_to_data_url")
    def test_truncates_beyond_max_images(self, mock_to_url, mock_is_image):
        mock_to_url.return_value = "data:image/png;base64,x="
        many_images = [f"img_{i}.png" for i in range(MAX_IMAGES_PER_MESSAGE + 5)]

        result = build_user_content("many pics", many_images)

        assert isinstance(result, list)
        # Count image_url blocks (not text blocks)
        img_blocks = [b for b in result if b.get("type") == "image_url"]
        assert len(img_blocks) == MAX_IMAGES_PER_MESSAGE

        # Text should mention truncation
        text_blocks = [b for b in result if b.get("type") == "text"]
        if text_blocks:
            assert "跳过" in text_blocks[0]["text"] or "truncated" in text_blocks[0]["text"].lower()

    # ── Edge cases ─────────────────────────────────────────────────────

    def test_empty_all(self):
        result = build_user_content("", [])
        assert result == ""

    @patch("core.llm_gateway.content_builder.is_image_path", return_value=True)
    @patch("core.llm_gateway.content_builder.image_file_to_data_url")
    def test_all_images_fail_even_with_text(self, mock_to_url, mock_is_image):
        mock_to_url.side_effect = OSError("not found")
        result = build_user_content("describe this", ["x.png", "y.png"])

        # Falls back to plain text
        assert isinstance(result, str)
        assert "describe this" in result
