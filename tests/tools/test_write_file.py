"""Tests for write_file tool — overwrite + surgical edit modes."""

import pytest
from pathlib import Path

from core.tools.write_file import execute, schema


# ── Overwrite mode ─────────────────────────────────────────────────────────

class TestWriteFileOverwrite:
    @pytest.mark.asyncio
    async def test_empty_file_path(self):
        result = await execute({"file_path": "", "content": "data"})
        assert "不能为空" in result

    @pytest.mark.asyncio
    async def test_path_is_dir(self, tmp_path):
        result = await execute({"file_path": str(tmp_path), "content": "data"})
        assert "目录" in result

    @pytest.mark.asyncio
    async def test_write_creates_file(self, tmp_path):
        f = tmp_path / "new_file.txt"
        result = await execute({"file_path": str(f), "content": "hello world"})
        assert "已写入" in result
        assert f.read_text(encoding="utf-8") == "hello world"

    @pytest.mark.asyncio
    async def test_write_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "deep" / "nested" / "file.txt"
        result = await execute({"file_path": str(f), "content": "nested content"})
        assert "已写入" in result
        assert f.read_text(encoding="utf-8") == "nested content"


# ── Edit mode (old_string + new_string) ────────────────────────────────────

class TestWriteFileEdit:
    @pytest.mark.asyncio
    async def test_empty_old_string(self):
        result = await execute({
            "file_path": "/tmp/test.txt", "old_string": "", "new_string": "b",
        })
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


# ── Mode conflict ──────────────────────────────────────────────────────────

class TestWriteFileModeConflict:
    @pytest.mark.asyncio
    async def test_content_with_old_string(self):
        """不能同时传 content 和 old_string。"""
        result = await execute({
            "file_path": "/tmp/x.txt", "content": "a", "old_string": "b", "new_string": "c",
        })
        assert "互斥" in result

    @pytest.mark.asyncio
    async def test_old_without_new(self):
        """old_string 需要配对 new_string。"""
        result = await execute({
            "file_path": "/tmp/x.txt", "old_string": "a",
        })
        assert "同时提供" in result

    @pytest.mark.asyncio
    async def test_new_without_old(self):
        """new_string 需要配对 old_string。"""
        result = await execute({
            "file_path": "/tmp/x.txt", "new_string": "b",
        })
        assert "同时提供" in result


# ── Schema ─────────────────────────────────────────────────────────────────

class TestWriteFileSchema:
    def test_schema(self):
        assert schema["type"] == "object"
        assert "file_path" in schema["required"]
        assert "content" in schema["properties"]
        assert "old_string" in schema["properties"]
        assert "new_string" in schema["properties"]


# ── 原子写回归 ───────────────────────────────────────────────────────────────

class TestAtomicWrite:
    @pytest.mark.asyncio
    async def test_no_leftover_tmp_after_successful_write(self, tmp_path):
        """write_file 必须走原子写：成功后不留 .tmp_ 残留文件。"""
        f = tmp_path / "target.txt"
        result = await execute({"file_path": str(f), "content": "data"})
        assert "已写入" in result
        assert f.exists()
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".tmp_")]
        assert leftovers == [], f"原子写不应残留临时文件: {[p.name for p in leftovers]}"

    @pytest.mark.asyncio
    async def test_tilde_expanded_not_literal_dir(self, tmp_path, monkeypatch):
        """覆写模式 `~/` 前缀必须展开到用户主目录（曾写进字面 ~ 目录）。"""
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        f = tmp_path / "tilde_test.txt"

        result = await execute({"file_path": f"~/{f.name}", "content": "x"})
        assert "已写入" in result
        assert f.exists(), "应写入主目录而非字面 ~ 目录"
        assert not (tmp_path.parent / "~" / f.name).exists()


class TestEmptyWriteGuard:
    """回归：只传 file_path（无 content/old_string/new_string）必须拒绝，
    不能静默写空文件清空原文件。"""

    @pytest.mark.asyncio
    async def test_only_file_path_rejected(self, tmp_path):
        f = tmp_path / "existing.txt"
        f.write_text("original content", encoding="utf-8")

        result = await execute({"file_path": str(f)})
        assert "content" in result or "old_string" in result or "模式" in result
        # 原文件必须原封不动
        assert f.read_text(encoding="utf-8") == "original content"

    @pytest.mark.asyncio
    async def test_only_file_path_no_file_created(self, tmp_path):
        f = tmp_path / "brand_new.txt"
        result = await execute({"file_path": str(f)})
        assert not f.exists(), "不应创建空文件"
