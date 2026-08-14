"""测试 safety.py — 高危命令拦截 + 文件覆盖保护。"""

import pytest
import tempfile
from pathlib import Path

from core.kernel.safety import check_tool_safety, strip_quoted, DESTRUCTIVE_PATTERNS


class TestStripQuoted:
    """测试引号内容剥离。"""

    def test_strip_double_quotes(self):
        assert strip_quoted('echo "use rm -rf"') == 'echo ""'

    def test_strip_single_quotes(self):
        assert strip_quoted("echo 'delete with rm -rf /'") == "echo ''"

    def test_strip_both_quote_types(self):
        text = 'echo "hello" && ls \'world\''
        result = strip_quoted(text)
        assert 'hello' not in result
        assert 'world' not in result

    def test_no_quotes_unchanged(self):
        assert strip_quoted("ls -la /tmp") == "ls -la /tmp"

    def test_empty_string(self):
        assert strip_quoted("") == ""


class TestCheckToolSafetyRunShell:
    """测试 run_shell 工具安全校验。"""

    def test_safe_command_passes(self):
        assert check_tool_safety("run_shell", {"command": "ls -la"}) is None
        assert check_tool_safety("run_shell", {"command": "echo hello"}) is None
        assert check_tool_safety("run_shell", {"command": "cat /etc/hosts"}) is None

    def test_rm_rf_blocked(self):
        result = check_tool_safety("run_shell", {"command": "rm -rf /"})
        assert result is not None
        assert "破坏性" in result

    def test_rm_r_blocked(self):
        result = check_tool_safety("run_shell", {"command": "rm -r /tmp/data"})
        assert result is not None

    def test_dd_blocked(self):
        result = check_tool_safety("run_shell", {"command": "dd if=/dev/zero of=/dev/sda"})
        assert result is not None

    def test_mkfs_blocked(self):
        result = check_tool_safety("run_shell", {"command": "mkfs.ext4 /dev/sda1"})
        assert result is not None

    def test_fork_bomb_blocked(self):
        result = check_tool_safety("run_shell", {"command": ":(){ :|:& };:"})
        assert result is not None

    def test_chmod_777_blocked(self):
        result = check_tool_safety("run_shell", {"command": "chmod 777 /etc/passwd"})
        assert result is not None

    def test_write_to_dev_blocked(self):
        result = check_tool_safety("run_shell", {"command": "cat file > /dev/sda"})
        assert result is not None

    def test_write_to_nvme_blocked(self):
        result = check_tool_safety("run_shell", {"command": "dd if=file > /dev/nvme0n1"})
        assert result is not None

    def test_quoted_rm_is_safe(self):
        """引号内的 rm -rf 不应被拦截（无害的 echo 等命令）。"""
        result = check_tool_safety("run_shell", {"command": 'echo "use rm -rf to delete"'})
        assert result is None

    def test_cmd_alias_for_command(self):
        """cmd 参数也应被检查。"""
        result = check_tool_safety("run_shell", {"cmd": "rm -rf /"})
        assert result is not None

    def test_command_not_string_skipped(self):
        assert check_tool_safety("run_shell", {"command": 123}) is None

    def test_empty_command(self):
        assert check_tool_safety("run_shell", {}) is None


class TestCheckToolSafetyWriteFile:
    """测试 write_file 工具安全校验。"""

    def test_new_file_passes(self, tmp_path):
        new_file = tmp_path / "new_file.txt"
        result = check_tool_safety("write_file", {"file_path": str(new_file)})
        assert result is None

    def test_existing_file_blocked(self, tmp_path):
        existing = tmp_path / "existing.txt"
        existing.write_text("important data")
        result = check_tool_safety("write_file", {"file_path": str(existing)})
        assert result is not None
        assert "已存在" in result

    def test_filepath_alias(self, tmp_path):
        existing = tmp_path / "data.txt"
        existing.write_text("data")
        result = check_tool_safety("write_file", {"filepath": str(existing)})
        assert result is not None

    def test_no_path_skipped(self):
        assert check_tool_safety("write_file", {}) is None

    def test_other_tool_skipped(self):
        assert check_tool_safety("read_file", {"file_path": "/etc/passwd"}) is None


class TestDestructivePatterns:
    """验证 DESTRUCTIVE_PATTERNS 常量包含关键模式。"""

    def test_has_core_patterns(self):
        patterns_str = "|".join(DESTRUCTIVE_PATTERNS)
        assert "rm" in patterns_str
        assert "dd" in patterns_str
        assert "mkfs" in patterns_str
        # fork bomb
        assert ":" in patterns_str
