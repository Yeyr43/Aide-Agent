"""Tests for edit_file tool."""

import pytest
from pathlib import Path

from core.tools.builtin.edit_file import execute, schema


class TestEditFile:
    @pytest.mark.asyncio
    async def test_empty_file_path(self):
        result = await execute({"file_path": "", "old_string": "a", "new_string": "b"})
        assert "不能为空" in result

    @pytest.mark.asyncio
    async def test_empty_old_string(self):
        result = await execute({"file_path": "/tmp/test.txt", "old_string": "", "new_string": "b"})
        assert "不能为空" in result

    @pytest.mark.asyncio
    async def test_file_not_exists(self):
        result = await execute({
            "file_path": "/NONEXISTENT_FILE_XYZ.txt", "old_string": "a", "new_string": "b",
        })
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_path_is_dir_not_file(self, tmp_path):
        result = await execute({
            "file_path": str(tmp_path), "old_string": "a", "new_string": "b",
        })
        assert "不是文件" in result

    @pytest.mark.asyncio
    async def test_old_string_not_found(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world", encoding="utf-8")
        result = await execute({
            "file_path": str(f), "old_string": "notfound", "new_string": "replaced",
        })
        assert "未找到" in result

    @pytest.mark.asyncio
    async def test_old_string_not_unique(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world\nhello world\n", encoding="utf-8")
        result = await execute({
            "file_path": str(f), "old_string": "hello", "new_string": "hi",
        })
        assert "2 次" in result

    @pytest.mark.asyncio
    async def test_successful_replace(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("original content here", encoding="utf-8")
        result = await execute({
            "file_path": str(f), "old_string": "original", "new_string": "replaced",
        })
        assert "已编辑" in result
        new_content = f.read_text(encoding="utf-8")
        assert new_content == "replaced content here"

    @pytest.mark.asyncio
    async def test_multiline_replace(self, tmp_path):
        f = tmp_path / "test.py"
        original = "def old_function():\n    pass\n"
        f.write_text(original, encoding="utf-8")
        result = await execute({
            "file_path": str(f),
            "old_string": "def old_function():\n    pass",
            "new_string": "def new_function():\n    return 42",
        })
        assert "已编辑" in result
        new_content = f.read_text(encoding="utf-8")
        assert "new_function" in new_content
        assert "return 42" in new_content

    @pytest.mark.asyncio
    async def test_replace_with_empty(self, tmp_path):
        """Replace with empty string (deletion)."""
        f = tmp_path / "test.txt"
        f.write_text("keep this\ndelete this\nkeep this\n", encoding="utf-8")
        result = await execute({
            "file_path": str(f), "old_string": "delete this\n", "new_string": "",
        })
        assert "已编辑" in result
        new_content = f.read_text(encoding="utf-8")
        assert "delete" not in new_content


class TestEditFileSchema:
    def test_schema(self):
        assert schema["type"] == "object"
        assert "file_path" in schema["required"]
        assert "old_string" in schema["required"]
        assert "new_string" in schema["required"]
