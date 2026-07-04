"""测试内置工具 — list_dir, clipboard。"""

import os
import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from core.tools.builtin import list_dir, clipboard


class TestListDir:
    """测试 list_dir 工具。"""

    @pytest.mark.asyncio
    async def test_list_dir_empty_path(self):
        """空路径默认为当前目录。"""
        result = await list_dir.execute({"path": ""})
        assert "错误" not in result

    @pytest.mark.asyncio
    async def test_list_dir_current_dir(self):
        """列出当前目录。"""
        result = await list_dir.execute({"path": "."})
        assert "错误" not in result
        # 应该包含至少一个文件/目录
        assert len(result) > 10

    @pytest.mark.asyncio
    async def test_list_dir_not_exists(self):
        result = await list_dir.execute({"path": "/NONEXISTENT_DIR_XYZ"})
        assert "错误" in result
        assert "不存在" in result or "存在" in result

    @pytest.mark.asyncio
    async def test_list_dir_file_not_dir(self, tmp_path):
        """传入文件路径应报错。"""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = await list_dir.execute({"path": str(f)})
        assert "不是目录" in result.lower() or "错误" in result.lower()

    @pytest.mark.asyncio
    async def test_list_dir_with_pattern(self, tmp_path):
        """测试 glob 过滤。"""
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        (tmp_path / "c.txt").write_text("")
        result = await list_dir.execute({"path": str(tmp_path), "pattern": "*.py"})
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result

    @pytest.mark.asyncio
    async def test_list_dir_recursive(self, tmp_path):
        """测试递归列出。"""
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("")
        (tmp_path / "root.py").write_text("")
        result = await list_dir.execute({"path": str(tmp_path), "recursive": True})
        assert "root.py" in result
        assert "deep.py" in result

    @pytest.mark.asyncio
    async def test_list_dir_empty_dir(self, tmp_path):
        """空目录。"""
        empty = tmp_path / "empty"
        empty.mkdir()
        result = await list_dir.execute({"path": str(empty)})
        assert "空" in result.lower() or "为空" in result.lower()

    @pytest.mark.asyncio
    async def test_list_dir_permission_error(self):
        """无权限目录。"""
        # 使用系统保护目录测试，权限错误应被捕获
        with patch.object(Path, "glob", side_effect=PermissionError("denied")):
            result = await list_dir.execute({"path": "/root"})
            assert "错误" in result


class TestClipboard:
    """测试 clipboard 工具。"""

    @pytest.mark.asyncio
    async def test_clipboard_read(self):
        """读取剪贴板（可能为空）。"""
        result = await clipboard.execute({"action": "read"})
        # 成功或提示为空都算正常
        assert isinstance(result, str)
        assert "错误" not in result.lower() or "空" in result

    @pytest.mark.asyncio
    async def test_clipboard_write_read(self):
        """写入后读取。"""
        test_text = "Aide Clipboard Test 测试剪贴板"
        write_result = await clipboard.execute({"action": "write", "text": test_text})
        assert "已写入" in write_result or "错误" in write_result

        if "已写入" in write_result:
            read_result = await clipboard.execute({"action": "read"})
            assert test_text in read_result

    @pytest.mark.asyncio
    async def test_clipboard_write_empty_text(self):
        """写入空文本应报错。"""
        result = await clipboard.execute({"action": "write", "text": ""})
        assert "不能为空" in result

    @pytest.mark.asyncio
    async def test_clipboard_invalid_action(self):
        """无效操作。"""
        result = await clipboard.execute({"action": "invalid"})
        assert "错误" in result or "未知" in result

    @pytest.mark.asyncio
    async def test_clipboard_read_with_mock(self):
        """Mock pyperclip 测试读取。"""
        with patch("pyperclip.paste", return_value="mocked content"):
            result = await clipboard.execute({"action": "read"})
            assert "mocked content" in result

    @pytest.mark.asyncio
    async def test_clipboard_write_with_mock(self):
        """Mock pyperclip 测试写入。"""
        with patch("pyperclip.copy") as mock_copy:
            result = await clipboard.execute({"action": "write", "text": "hello world"})
            mock_copy.assert_called_once_with("hello world")
            assert "已写入剪贴板" in result


class TestToolSchemas:
    """验证新工具的 JSON Schema 合法性。"""

    def test_list_dir_schema(self):
        schema = list_dir.schema
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "path" in schema["properties"]
        assert "pattern" in schema["properties"]
        assert "recursive" in schema["properties"]

    def test_clipboard_schema(self):
        schema = clipboard.schema
        assert schema["type"] == "object"
        assert "action" in schema["required"]
        assert schema["properties"]["action"]["enum"] == ["read", "write"]
