"""Tests for search_in_files tool."""

import pytest
from pathlib import Path

from core.tools.builtin.search_in_files import execute, schema, _search_file, _gather_files


class TestSearchInFiles:
    @pytest.mark.asyncio
    async def test_empty_pattern(self):
        result = await execute({"pattern": ""})
        assert "不能为空" in result

    @pytest.mark.asyncio
    async def test_invalid_regex(self):
        result = await execute({"pattern": "[invalid"})
        assert "无效" in result

    @pytest.mark.asyncio
    async def test_directory_not_exists(self):
        result = await execute({"pattern": "test", "directory": "/NONEXISTENT_DIR"})
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_path_is_file_not_dir(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = await execute({"pattern": "hello", "directory": str(f)})
        assert "不是目录" in result

    @pytest.mark.asyncio
    async def test_find_in_files(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("def bar():\n    pass\n", encoding="utf-8")

        result = await execute({"pattern": "def foo", "directory": str(tmp_path)})
        assert "a.py" in result
        assert "def foo" in result

    @pytest.mark.asyncio
    async def test_glob_filter(self, tmp_path):
        (tmp_path / "a.py").write_text("TODO: fix this", encoding="utf-8")
        (tmp_path / "b.txt").write_text("TODO: fix this too", encoding="utf-8")

        result = await execute({"pattern": "TODO", "directory": str(tmp_path), "glob": "*.py"})
        assert "a.py" in result
        assert "b.txt" not in result

    @pytest.mark.asyncio
    async def test_case_insensitive_default(self, tmp_path):
        (tmp_path / "code.py").write_text("hello WORLD\n", encoding="utf-8")
        result = await execute({"pattern": "world", "directory": str(tmp_path)})
        assert "WORLD" in result or "world" in result

    @pytest.mark.asyncio
    async def test_case_sensitive(self, tmp_path):
        (tmp_path / "code.py").write_text("hello WORLD\n", encoding="utf-8")
        result = await execute({
            "pattern": "world", "directory": str(tmp_path), "case_sensitive": True,
        })
        assert "未找到" in result or "WORLD" not in result

    @pytest.mark.asyncio
    async def test_no_match(self, tmp_path):
        (tmp_path / "code.py").write_text("no matches here\n", encoding="utf-8")
        result = await execute({"pattern": "NOTFOUNDXYZ", "directory": str(tmp_path)})
        assert "未找到" in result

    @pytest.mark.asyncio
    async def test_max_results_limit(self, tmp_path):
        for i in range(10):
            (tmp_path / f"file_{i}.py").write_text(f"# TODO item {i}\n", encoding="utf-8")
        result = await execute({"pattern": "TODO", "directory": str(tmp_path), "max_results": 3})
        assert "上限" in result


class TestSearchFile:
    def test_finds_matches(self, tmp_path):
        import re
        f = tmp_path / "test.py"
        f.write_text("line 1: hello\nline 2: world\nline 3: hello again\n", encoding="utf-8")
        regex = re.compile("hello", re.IGNORECASE)
        matches = _search_file(f, regex)
        assert len(matches) == 2
        assert matches[0][0] == 1
        assert matches[1][0] == 3


class TestGatherFiles:
    def test_glob_filter(self, tmp_path):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.txt").write_text("")
        files = _gather_files(tmp_path, "*.py")
        names = [f.name for f in files]
        assert "a.py" in names
        assert "b.txt" not in names

    def test_skips_ignored_dirs(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("")
        (tmp_path / "real.py").write_text("")
        files = _gather_files(tmp_path, "*")
        names = [f.name for f in files]
        assert "real.py" in names
        assert "config" not in names


class TestSearchInFilesSchema:
    def test_schema(self):
        assert schema["type"] == "object"
        assert "pattern" in schema["required"]
        assert "directory" in schema["properties"]
        assert "glob" in schema["properties"]
        assert "max_results" in schema["properties"]
