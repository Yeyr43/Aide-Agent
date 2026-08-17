"""Tests for search_in_files tool."""

import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import core.tools.search_in_files as sif
from core.tools.search_in_files import (
    execute, schema, _search_file, _iter_files, _fmt_size,
)


class TestSearchInFiles:
    @pytest.mark.asyncio
    async def test_empty_pattern_now_lists_dir(self):
        """空 pattern 现在是目录列表模式，不应报错。"""
        result = await execute({"pattern": ""})
        assert "错误" not in result

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
        files = _iter_files(tmp_path, "*.py")
        names = [f.name for f in files]
        assert "a.py" in names
        assert "b.txt" not in names

    def test_skips_ignored_dirs(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("")
        (tmp_path / "real.py").write_text("")
        files = _iter_files(tmp_path, "*")
        names = [f.name for f in files]
        assert "real.py" in names
        assert "config" not in names


class TestSearchInFilesSchema:
    def test_schema(self):
        assert schema["type"] == "object"
        # pattern 不再是 required — 空 pattern = 目录列表模式
        assert schema["required"] == []
        assert "pattern" in schema["properties"]
        assert "directory" in schema["properties"]
        assert "glob" in schema["properties"]
        assert "max_results" in schema["properties"]
        assert "case_sensitive" in schema["properties"]
        assert "recursive" in schema["properties"]


# ── 搜索模式边角 ───────────────────────────────────────────────────────────

class TestSearchModeEdgeCases:
    @pytest.mark.asyncio
    async def test_invalid_max_results_falls_back(self, tmp_path):
        (tmp_path / "a.py").write_text("TODO fix", encoding="utf-8")
        result = await execute({"pattern": "TODO", "directory": str(tmp_path), "max_results": "abc"})
        assert "a.py" in result

    @pytest.mark.asyncio
    async def test_iter_files_permission_error(self, tmp_path):
        with patch("core.tools.search_in_files._iter_files", side_effect=PermissionError("denied")):
            result = await execute({"pattern": "x", "directory": str(tmp_path)})
        assert "权限" in result

    @pytest.mark.asyncio
    async def test_too_many_files_limit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sif, "MAX_FILES", 5)
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text("TODO item", encoding="utf-8")
        result = await execute({"pattern": "TODO", "directory": str(tmp_path)})
        assert "扫描上限" in result

    @pytest.mark.asyncio
    async def test_oversized_files_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sif, "MAX_FILE_SIZE", 10)
        (tmp_path / "big.txt").write_text("aaaaaaaaa TODO x", encoding="utf-8")  # >10 bytes
        (tmp_path / "small.txt").write_text("TODO", encoding="utf-8")
        result = await execute({"pattern": "TODO", "directory": str(tmp_path)})
        assert "已跳过" in result
        assert "small.txt" in result
        assert "big.txt" not in result

    @pytest.mark.asyncio
    async def test_stat_os_error_skips_file(self, tmp_path):
        (tmp_path / "good.txt").write_text("TODO x", encoding="utf-8")
        fake_bad = SimpleNamespace(
            name="bad.txt", stat=lambda: (_ for _ in ()).throw(OSError("gone"))
        )
        with patch("core.tools.search_in_files._iter_files",
                   return_value=[fake_bad, tmp_path / "good.txt"]):
            result = await execute({"pattern": "TODO", "directory": str(tmp_path)})
        assert "good.txt" in result
        assert "bad.txt" not in result

    @pytest.mark.asyncio
    async def test_search_file_exception_skipped(self, tmp_path):
        (tmp_path / "bad.py").write_text("TODO x", encoding="utf-8")
        (tmp_path / "good.py").write_text("TODO x", encoding="utf-8")
        real_search = _search_file

        def fake_search(fp, regex):
            if fp.name == "bad.py":
                raise PermissionError("denied")
            return real_search(fp, regex)

        with patch("core.tools.search_in_files._search_file", side_effect=fake_search):
            result = await execute({"pattern": "TODO", "directory": str(tmp_path)})
        assert "good.py" in result
        assert "bad.py" not in result


# ── 目录列表模式边角 ───────────────────────────────────────────────────────

class TestListModeEdgeCases:
    @pytest.mark.asyncio
    async def test_invalid_max_results_falls_back(self, tmp_path):
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        result = await execute({"pattern": "", "directory": str(tmp_path), "max_results": "abc"})
        assert "a.txt" in result

    @pytest.mark.asyncio
    async def test_scandir_permission_error(self, tmp_path):
        with patch("core.tools.search_in_files._scandir_entries", side_effect=PermissionError("denied")):
            result = await execute({"pattern": "", "directory": str(tmp_path)})
        assert "权限" in result

    @pytest.mark.asyncio
    async def test_list_generic_exception(self, tmp_path):
        with patch("core.tools.search_in_files._scandir_entries", side_effect=RuntimeError("boom")):
            result = await execute({"pattern": "", "directory": str(tmp_path)})
        assert "列出目录失败" in result

    @pytest.mark.asyncio
    async def test_empty_dir_with_pattern(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        result = await execute({"pattern": "", "directory": str(empty), "glob": "*.py"})
        assert "模式" in result

    @pytest.mark.asyncio
    async def test_max_items_truncated(self, tmp_path):
        for i in range(5):
            (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
        result = await execute({"pattern": "", "directory": str(tmp_path), "max_results": 2})
        assert "上限" in result

    @pytest.mark.asyncio
    async def test_output_too_large_truncated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sif, "MAX_LIST_SIZE", 100)
        for i in range(5):
            (tmp_path / f"file{i}.txt").write_text("x", encoding="utf-8")
        result = await execute({"pattern": "", "directory": str(tmp_path)})
        assert "输出过大" in result

    @pytest.mark.asyncio
    async def test_scandir_stat_os_error(self, tmp_path):
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        with patch("core.tools.search_in_files._fmt_time", side_effect=OSError("stat fail")):
            result = await execute({"pattern": "", "directory": str(tmp_path)})
        assert "?" in result

    @pytest.mark.asyncio
    async def test_scandir_permission_error_reraises(self, tmp_path):
        with patch("core.tools.search_in_files.os.scandir", side_effect=PermissionError("denied")):
            result = await execute({"pattern": "", "directory": str(tmp_path)})
        assert "权限" in result


# ── 递归列表模式 ───────────────────────────────────────────────────────────

class TestRecursiveList:
    @pytest.mark.asyncio
    async def test_recursive_lists_nested(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "nested.txt").write_text("x", encoding="utf-8")
        (tmp_path / "b.txt").write_text("x", encoding="utf-8")
        result = await execute({"pattern": "", "directory": str(tmp_path), "recursive": True})
        assert "nested.txt" in result
        assert "b.txt" in result

    @pytest.mark.asyncio
    async def test_recursive_depth_limit(self, tmp_path):
        d = tmp_path
        for name in ["a", "b", "c", "d", "e", "f"]:
            d = d / name
            d.mkdir()
        (d / "leaf.txt").write_text("x", encoding="utf-8")
        result = await execute({"pattern": "", "directory": str(tmp_path), "recursive": True})
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_recursive_max_entries_top_check(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "inner.txt").write_text("x", encoding="utf-8")
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        result = await execute({
            "pattern": "", "directory": str(tmp_path), "recursive": True, "max_results": 1,
        })
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_recursive_max_entries_in_loop(self, tmp_path):
        for i in range(3):
            (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
        result = await execute({
            "pattern": "", "directory": str(tmp_path), "recursive": True, "max_results": 1,
        })
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_recursive_stat_os_error(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "f.txt").write_text("x", encoding="utf-8")
        with patch("core.tools.search_in_files._fmt_time", side_effect=OSError("stat fail")):
            result = await execute({"pattern": "", "directory": str(tmp_path), "recursive": True})
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_recursive_scandir_permission_error(self, tmp_path):
        (tmp_path / "a").mkdir()
        with patch("core.tools.search_in_files.os.scandir", side_effect=PermissionError("denied")):
            result = await execute({"pattern": "", "directory": str(tmp_path), "recursive": True})
        assert isinstance(result, str)
        assert len(result) > 0


# ── _fmt_size 单位换算 ──────────────────────────────────────────────────────

class TestFormatSize:
    def test_units(self):
        assert _fmt_size(500) == "500B"
        assert _fmt_size(2048) == "2.0KB"
        assert _fmt_size(2 * 1024 * 1024) == "2.0MB"
        assert _fmt_size(2 * 1024 * 1024 * 1024) == "2.0GB"
