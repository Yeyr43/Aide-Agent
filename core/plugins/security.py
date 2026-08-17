"""PluginPreflightCheck — ClawScan 级别插件安全预检。

对标 OpenClaw ClawScan 的安全检查管线：
  1. install* 脚本白名单（仅允许 pip/npm/echo/mkdir/curl/wget）
  2. HTTPS-only 外部 URL
  3. POSIX 世界可写文件检测
  4. JVM/glibc/.NET 注入环境变量模式
  5. 敏感路径访问模式（/etc/passwd, ~/.ssh, /etc/shadow）

空列表 = 安全。非空 = 需要用户确认的警告。
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from pathlib import Path

from core.platform import IS_WINDOWS

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────────────


@dataclass
class PreflightWarning:
    """一条安全警告。"""
    level: str          # "error" | "warning" | "info"
    category: str       # "installer" | "url" | "permissions" | "env_injection" | "sensitive_path"
    message: str
    file: str = ""      # 触发警告的文件路径
    line: int = 0


@dataclass
class PreflightResult:
    """安全预检结果。"""
    passed: bool                          # True = 安全，可直接安装
    warnings: list[PreflightWarning] = field(default_factory=list)
    blocked: bool = False                 # True = 有致命问题，必须阻止安装


# ── 白名单 / 黑名单 ──────────────────────────────────────────────────────


# install* 脚本允许的命令（ClawScan 级别严格度）
INSTALLER_SAFE_PATTERNS: list[str] = [
    r'^pip\s+install',
    r'^pip3\s+install',
    r'^python\s+-m\s+pip\s+install',
    r'^python3\s+-m\s+pip\s+install',
    r'^uv\s+pip\s+install',
    r'^npm\s+install',
    r'^npm\s+i\s',
    r'^yarn\s+add',
    r'^pnpm\s+install',
    r'^echo\b',
    r'^mkdir\b',
    r'^curl\s+-[fSL]',
    r'^wget\s+-',
    r'^git\s+clone',
    r'^cargo\s+install',
    r'^go\s+install',
    r'^gem\s+install',
    r'^#',              # 注释
    r'^\s*$',           # 空行
]

# 阻止的环境变量注入模式
BLOCKED_ENV_PATTERNS: list[str] = [
    'MAVEN_OPTS',           # JVM 注入
    'JAVA_TOOL_OPTIONS',    # JVM 注入
    '_JAVA_OPTIONS',        # JVM 注入
    'GLIBC_TUNABLES',       # glibc 注入
    'GLIBC_TUNNELS',        # glibc 注入
    'DOTNET_ADDITIONAL_DEPS',  # .NET 依赖劫持
    'DOTNET_ROOT',          # .NET 根目录劫持
    'LD_PRELOAD',           # Linux 动态库劫持
    'LD_LIBRARY_PATH',      # Linux 库路径劫持
    'DYLD_INSERT_LIBRARIES',  # macOS 动态库注入
    'DYLD_LIBRARY_PATH',    # macOS 库路径劫持
    'PERL5LIB',             # Perl 库劫持
    'PERLLIB',              # Perl 库劫持
    'PYTHONPATH',           # Python 库劫持
    'RUBYLIB',              # Ruby 库劫持
    'NODE_PATH',            # Node.js 模块劫持
]

# 敏感路径模式
SENSITIVE_PATH_PATTERNS: list[str] = [
    r'/etc/(passwd|shadow|group|sudoers)',
    r'/etc/ssh/',
    r'~/.ssh/',
    r'/root/',
    r'C:\\Windows\\System32\\',
    r'~/\.aws/',
    r'~/\.gcloud/',
    r'~/\.azure/',
    r'~/\.config/gh/',
]

# 编译后的正则
_SAFE_INSTALLER_RE = [re.compile(p) for p in INSTALLER_SAFE_PATTERNS]
_SENSITIVE_PATH_RE = [re.compile(p, re.IGNORECASE) for p in SENSITIVE_PATH_PATTERNS]
_URL_RE = re.compile(r'https?://[^\s<>"\')\]]+')
_BLOCKED_ENV_RE = re.compile(
    r'\b(' + '|'.join(re.escape(p) for p in BLOCKED_ENV_PATTERNS) + r')\b',
)


# ── 检查器 ────────────────────────────────────────────────────────────────


class PluginPreflightCheck:
    """插件安装前安全预检 — ClawScan 级别严格度。

    用法:
        checker = PluginPreflightCheck()
        result = await checker.check(plugin_dir)
        if not result.passed:
            for w in result.warnings:
                print(f"[{w.level}] {w.category}: {w.message}")
    """

    async def check(self, plugin_dir: Path) -> PreflightResult:
        """对插件目录执行全套安全预检。

        Returns:
            PreflightResult — passed=True 表示可直接安装。
        """
        warnings: list[PreflightWarning] = []
        blocked = False

        # 1. install* 脚本白名单
        install_warnings, install_blocked = self._check_install_scripts(plugin_dir)
        warnings.extend(install_warnings)
        blocked = blocked or install_blocked

        # 2. HTTPS-only 外部 URL
        url_warnings = self._check_urls(plugin_dir)
        warnings.extend(url_warnings)

        # 3. POSIX 世界可写文件
        perm_warnings = self._check_permissions(plugin_dir)
        warnings.extend(perm_warnings)

        # 4. JVM/glibc/.NET 注入模式
        injection_warnings, injection_blocked = self._check_env_injection(plugin_dir)
        warnings.extend(injection_warnings)
        blocked = blocked or injection_blocked

        # 5. 敏感路径访问
        path_warnings = self._check_sensitive_paths(plugin_dir)
        warnings.extend(path_warnings)

        return PreflightResult(
            passed=not blocked and not any(w.level == "error" for w in warnings),
            warnings=warnings,
            blocked=blocked,
        )

    # ── 1. Install 脚本 ────────────────────────────────────────────────

    def _check_install_scripts(self, plugin_dir: Path) -> tuple[list[PreflightWarning], bool]:
        """检查 install* 脚本的每行命令是否在白名单内。"""
        warnings: list[PreflightWarning] = []
        blocked = False

        for script in plugin_dir.glob("install*"):
            if script.suffix in (".pyc", ".pyo", ".so", ".dll"):
                continue
            try:
                lines = script.read_text(encoding="utf-8", errors="replace").split("\n")
            except OSError:
                continue

            for lineno, line in enumerate(lines, 1):
                stripped = line.strip()
                if not stripped:
                    continue

                if not self._is_safe_command(stripped):
                    msg = f"install 脚本包含未授权命令: {stripped[:80]}"
                    warnings.append(PreflightWarning(
                        level="error", category="installer",
                        message=msg, file=str(script.relative_to(plugin_dir)), line=lineno,
                    ))
                    blocked = True

        return warnings, blocked

    @staticmethod
    def _is_safe_command(line: str) -> bool:
        """检查单行命令是否在白名单内。"""
        for pattern in _SAFE_INSTALLER_RE:
            if pattern.search(line):
                return True
        return False

    # ── 2. URL 检查 ────────────────────────────────────────────────────

    def _check_urls(self, plugin_dir: Path) -> list[PreflightWarning]:
        """检查所有文件中的 URL 是否 HTTPS-only。"""
        warnings: list[PreflightWarning] = []
        for f in plugin_dir.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix in (".pyc", ".pyo", ".so", ".dll", ".exe", ".png", ".jpg", ".ico"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            for match in _URL_RE.finditer(text):
                url = match.group(0)
                if url.startswith("http://"):
                    warnings.append(PreflightWarning(
                        level="warning", category="url",
                        message=f"非 HTTPS URL: {url[:100]}",
                        file=str(f.relative_to(plugin_dir)),
                    ))

        return warnings

    # ── 3. 权限检查 ────────────────────────────────────────────────────

    def _check_permissions(self, plugin_dir: Path) -> list[PreflightWarning]:
        """检查世界可写文件（仅 POSIX）。"""
        warnings: list[PreflightWarning] = []
        if IS_WINDOWS:  # Windows 权限模型不同
            return warnings

        for f in plugin_dir.rglob("*"):
            if not f.is_file():
                continue
            try:
                if f.stat().st_mode & 0o002:
                    warnings.append(PreflightWarning(
                        level="warning", category="permissions",
                        message=f"世界可写文件: {f.relative_to(plugin_dir)}",
                        file=str(f.relative_to(plugin_dir)),
                    ))
            except OSError:
                pass

        return warnings

    # ── 4. 环境变量注入 ────────────────────────────────────────────────

    def _check_env_injection(self, plugin_dir: Path) -> tuple[list[PreflightWarning], bool]:
        """检查是否有 JVM/glibc/.NET 环境变量注入模式。"""
        warnings: list[PreflightWarning] = []
        blocked = False

        for f in plugin_dir.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix in (".pyc", ".pyo", ".so", ".dll", ".exe"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            for match in _BLOCKED_ENV_RE.finditer(text):
                var_name = match.group(1)
                warnings.append(PreflightWarning(
                    level="error", category="env_injection",
                    message=f"检测到环境变量注入模式: {var_name}",
                    file=str(f.relative_to(plugin_dir)),
                ))
                blocked = True

        return warnings, blocked

    # ── 5. 敏感路径 ────────────────────────────────────────────────────

    def _check_sensitive_paths(self, plugin_dir: Path) -> list[PreflightWarning]:
        """检查是否有访问敏感系统路径的模式。"""
        warnings: list[PreflightWarning] = []

        for f in plugin_dir.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix in (".pyc", ".pyo", ".so", ".dll", ".exe"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            for pattern in _SENSITIVE_PATH_RE:
                if pattern.search(text):
                    warnings.append(PreflightWarning(
                        level="warning", category="sensitive_path",
                        message=f"访问敏感路径: {pattern.pattern[:60]}",
                        file=str(f.relative_to(plugin_dir)),
                    ))
                    break  # 每个文件每种模式只报告一次

        return warnings
