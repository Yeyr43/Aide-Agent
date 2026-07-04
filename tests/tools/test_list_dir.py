"""Tests for list_dir tool."""

import pytest
from pathlib import Path

from core.tools.builtin.list_dir import execute, schema, _fmt_size, _fmt_time


class TestListDirSchema:
    def test_no_required_fields(self):
        assert schema["required"] == []

    def test_has_path_property(self):
        assert "path" in schema["properties"]

    def test_has_recursive_property(self):
        assert "recursive" in schema["properties"]


class TestFormatSize:
    def test_bytes(self):
        assert _fmt_size(500) == "500B"

    def test_kb(self):
        assert "KB" in _fmt_size(2048)

    def test_mb(self):
        assert "MB" in _fmt_size(5 * 1024 * 1024)

    def test_gb(self):
        assert "GB" in _fmt_size(2 * 1024 * 1024 * 1024)


class TestFormatTime:
    def test_returns_string(self):
        import time
        result = _fmt_time(time.time())
        assert ":" in result  # HH:MM format


class TestListDirExecute:
    @pytest.mark.asyncio
    async def test_defaults_to_cwd(self):
        result = await execute({})
        assert "错误" not in result
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_dir_not_found(self, tmp_path):
        missing = tmp_path / "missing_dir"
        result = await execute({"path": str(missing)})
        assert "错误" in result
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_path_is_file_not_dir(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")
        result = await execute({"path": str(f)})
        assert "错误" in result
        assert "不是目录" in result or "read_file" in result

    @pytest.mark.asyncio
    async def test_lists_directory_content(self, tmp_path):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.txt").write_text("y")
        result = await execute({"path": str(tmp_path)})
        assert "a.py" in result
        assert "b.txt" in result

    @pytest.mark.asyncio
    async def test_empty_directory(self, tmp_path):
        result = await execute({"path": str(tmp_path)})
        assert "目录为空" in result or len(result) > 0

    @pytest.mark.asyncio
    async def test_glob_pattern(self, tmp_path):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.txt").write_text("y")
        result = await execute({"path": str(tmp_path), "pattern": "*.py"})
        assert "a.py" in result
        assert "b.txt" not in result

    @pytest.mark.asyncio
    async def test_recursive_listing(self, tmp_path):
        (tmp_path / "a.py").write_text("x")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.py").write_text("y")
        result = await execute({"path": str(tmp_path), "recursive": True})
        assert "a.py" in result
        assert "b.py" in result
