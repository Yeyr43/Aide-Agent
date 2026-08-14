"""Tests for core.tools.read_file — local file reading tool."""

import pytest
from pathlib import Path

from core.tools.read_file import execute, schema, MAX_BYTES


class TestReadFileSchema:
    def test_schema_type(self):
        assert schema["type"] == "object"

    def test_file_path_is_required(self):
        assert "file_path" in schema["required"]


class TestReadFileExecute:
    @pytest.mark.asyncio
    async def test_empty_path(self):
        result = await execute({"file_path": ""})
        assert result != ""
        # Should return error message, not file content
        assert "file_path" in result.lower() or "路径" in result

    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path):
        result = await execute({"file_path": str(tmp_path / "nonexistent.txt")})
        assert "not_found" in result.lower() or "不存在" in result or "找不到" in result

    @pytest.mark.asyncio
    async def test_path_is_directory(self, tmp_path):
        result = await execute({"file_path": str(tmp_path)})
        assert result != ""
        # Should return error that it's a directory

    @pytest.mark.asyncio
    async def test_reads_text_file(self, tmp_path):
        file = tmp_path / "test.txt"
        file.write_text("Hello, world!", encoding="utf-8")
        result = await execute({"file_path": str(file)})
        assert result == "Hello, world!"

    @pytest.mark.asyncio
    async def test_reads_empty_file(self, tmp_path):
        file = tmp_path / "empty.txt"
        file.write_text("", encoding="utf-8")
        result = await execute({"file_path": str(file)})
        assert result == ""

    @pytest.mark.asyncio
    async def test_reads_multiline_file(self, tmp_path):
        file = tmp_path / "multi.txt"
        content = "line1\nline2\nline3\n"
        file.write_text(content, encoding="utf-8")
        result = await execute({"file_path": str(file)})
        # On Windows, file read returns \r\n → normalize for assertion
        assert result.replace("\r\n", "\n") == content

    @pytest.mark.asyncio
    async def test_truncates_large_file(self, tmp_path):
        file = tmp_path / "large.txt"
        # Create file larger than MAX_BYTES
        chunk = "x" * 1024  # 1KB chunks
        with open(file, "wb") as f:
            for _ in range(MAX_BYTES // 1024 + 2):  # +2 to ensure over limit
                f.write(chunk.encode("utf-8"))
        result = await execute({"file_path": str(file)})
        assert "截断" in result or "truncated" in result.lower()

    @pytest.mark.asyncio
    async def test_reads_utf8_with_chinese(self, tmp_path):
        file = tmp_path / "chinese.txt"
        file.write_text("你好世界！Hello World!", encoding="utf-8")
        result = await execute({"file_path": str(file)})
        assert "你好世界" in result

    @pytest.mark.asyncio
    async def test_handles_non_utf8_file(self, tmp_path):
        file = tmp_path / "latin1.txt"
        # Write raw bytes that are valid Latin-1 but invalid UTF-8
        data = b'\xff\xfeH\x00e\x00l\x00l\x00o\x00'  # UTF-16 BOM
        file.write_bytes(data)
        result = await execute({"file_path": str(file)})
        # Should not crash — returns either content with replacement chars or error
        assert isinstance(result, str)
