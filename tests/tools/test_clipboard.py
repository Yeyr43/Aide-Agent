"""Tests for clipboard tool."""

import pytest
import sys
from unittest.mock import MagicMock, patch

from core.tools.builtin.clipboard import execute, schema


class TestClipboardSchema:
    def test_schema_has_action_required(self):
        assert "action" in schema["required"]

    def test_schema_action_enum(self):
        assert schema["properties"]["action"]["enum"] == ["read", "write"]


class TestClipboardExecute:
    @pytest.fixture(autouse=True)
    def _patch_pyperclip(self):
        """Patch pyperclip before each test since it's imported locally inside execute()."""
        mock = MagicMock()
        mock.paste.return_value = "mocked content"
        with patch.dict(sys.modules, {"pyperclip": mock}):
            self._mock_pyperclip = mock
            yield mock
            # restore
            if mock is not None:
                mock.reset_mock()

    @pytest.mark.asyncio
    async def test_write_non_empty_text(self):
        mock_clip = sys.modules.get("pyperclip")
        result = await execute({"action": "write", "text": "hello"})
        mock_clip.copy.assert_called_once_with("hello")
        assert "已写入剪贴板" in result

    @pytest.mark.asyncio
    async def test_write_empty_text_error(self):
        result = await execute({"action": "write", "text": ""})
        assert "错误" in result

    @pytest.mark.asyncio
    async def test_write_missing_text_error(self):
        result = await execute({"action": "write"})
        assert "错误" in result

    @pytest.mark.asyncio
    async def test_read_clipboard_content(self):
        mock_clip = sys.modules.get("pyperclip")
        mock_clip.paste.return_value = "clipboard text"
        mock_clip.reset_mock()
        result = await execute({"action": "read"})
        assert "clipboard text" in result

    @pytest.mark.asyncio
    async def test_read_empty_clipboard(self):
        mock_clip = sys.modules.get("pyperclip")
        mock_clip.paste.return_value = ""
        mock_clip.reset_mock()
        result = await execute({"action": "read"})
        assert "剪贴板为空" in result

    @pytest.mark.asyncio
    async def test_read_truncates_long_content(self):
        mock_clip = sys.modules.get("pyperclip")
        mock_clip.paste.return_value = "x" * 10000
        mock_clip.reset_mock()
        result = await execute({"action": "read"})
        assert len(result) < 9000  # truncated
        assert "已截断" in result

    @pytest.mark.asyncio
    async def test_read_error_handling(self):
        mock_clip = sys.modules.get("pyperclip")
        mock_clip.paste.side_effect = RuntimeError("clipboard unavailable")
        mock_clip.reset_mock()
        result = await execute({"action": "read"})
        assert "错误" in result

    @pytest.mark.asyncio
    async def test_write_error_handling(self):
        mock_clip = sys.modules.get("pyperclip")
        mock_clip.copy.side_effect = RuntimeError("clipboard full")
        mock_clip.reset_mock()
        result = await execute({"action": "write", "text": "data"})
        assert "错误" in result

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        result = await execute({"action": "paste"})
        assert "错误" in result
        assert "未知" in result.lower()

    @pytest.mark.asyncio
    async def test_default_action_is_read(self):
        mock_clip = sys.modules.get("pyperclip")
        mock_clip.paste.return_value = "default read"
        mock_clip.reset_mock()
        result = await execute({})
        assert "default read" in result
