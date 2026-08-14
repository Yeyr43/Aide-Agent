"""测试内置工具 — search_in_files 列表模式。"""

import pytest
from pathlib import Path
from unittest.mock import patch

from core.tools import search_in_files


class TestSearchInFilesListMode:
    """测试 search_in_files 的目录列表模式（pattern 为空）。"""

    @pytest.mark.asyncio
    async def test_list_current_dir(self):
        """pattern 为空 → 列出当前目录。"""
        result = await search_in_files.execute({"pattern": ""})
        assert "错误" not in result

    @pytest.mark.asyncio
    async def test_list_dir_not_exists(self):
        result = await search_in_files.execute({"pattern": "", "directory": "/NONEXISTENT_DIR_XYZ"})
        assert "错误" in result

    @pytest.mark.asyncio
    async def test_list_file_not_dir(self, tmp_path):
        """传入文件路径应报错。"""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = await search_in_files.execute({"pattern": "", "directory": str(f)})
        assert "不是目录" in result.lower() or "错误" in result.lower()

    @pytest.mark.asyncio
    async def test_list_with_glob(self, tmp_path):
        """测试 glob 过滤。"""
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        (tmp_path / "c.txt").write_text("")
        result = await search_in_files.execute({
            "pattern": "", "directory": str(tmp_path), "glob": "*.py",
        })
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result

    @pytest.mark.asyncio
    async def test_list_recursive(self, tmp_path):
        """测试递归列出。"""
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("")
        (tmp_path / "root.py").write_text("")
        result = await search_in_files.execute({
            "pattern": "", "directory": str(tmp_path), "recursive": True,
        })
        assert "root.py" in result
        assert "deep.py" in result

    @pytest.mark.asyncio
    async def test_list_empty_dir(self, tmp_path):
        """空目录。"""
        empty = tmp_path / "empty"
        empty.mkdir()
        result = await search_in_files.execute({"pattern": "", "directory": str(empty)})
        assert "空" in result.lower() or "为空" in result.lower()


class TestSearchInFilesSchema:
    """验证 schema 合法性。"""

    def test_schema(self):
        schema = search_in_files.schema
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "pattern" in schema["properties"]
        assert "directory" in schema["properties"]
        assert "glob" in schema["properties"]
        assert "recursive" in schema["properties"]
        assert "case_sensitive" in schema["properties"]
        assert "max_results" in schema["properties"]
        # pattern 不再是 required — 空 pattern = 列表模式
        assert "pattern" not in schema.get("required", [])
