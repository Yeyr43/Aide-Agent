"""run_shell — 执行 Shell 命令。

安全限制：超时上限 60s，输出上限 100KB（尾重截断），无命令白名单（Soul 软引导）。
Windows 用系统 shell（cmd.exe），macOS/Linux 用 sh。

实现：subprocess.run + asyncio.to_thread（线程池），避免 Textual asyncio 事件循环兼容问题。
"""

import asyncio
import logging
import subprocess as _subprocess

from core.locale import t
from core.platform import IS_WINDOWS
from core.tools.truncation import truncate_output

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30      # 默认超时（秒）
MAX_TIMEOUT = 60           # 超时硬上限（秒）
MAX_OUTPUT_BYTES = 100 * 1024  # 输出硬上限（100KB）

# shell 输出尾重截断比例：保留 20% 头部 + 80% 尾部（错误/结果通常在末尾）
_SHELL_TRUNC_HEAD_RATIO = 0.2


def _shell_hint() -> str:
    """告诉 LLM 该用什么 shell 语法（当前语言）。"""
    key = "tool.run_shell.win_hint" if IS_WINDOWS else "tool.run_shell.nix_hint"
    return t(key)


async def execute(arguments: dict) -> str:
    """异步执行 shell 命令（subprocess.run → asyncio.to_thread）。

    Args:
        arguments: {"command": str, "timeout": int (可选)}

    Returns:
        命令输出（stdout + stderr 合并），或超时/错误描述
    """
    command = arguments.get("command", "").strip()
    if not command:
        return t("tool.run_shell.empty_command")

    timeout = arguments.get("timeout", DEFAULT_TIMEOUT)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        timeout = DEFAULT_TIMEOUT
    timeout = min(timeout, MAX_TIMEOUT)

    try:
        result: _subprocess.CompletedProcess = await asyncio.to_thread(
            _subprocess.run,
            command,
            shell=True,
            stdin=_subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
        )

        # 解码：UTF-8 优先，回退系统编码（Windows cmd.exe 输出 GBK）
        raw = result.stdout + result.stderr
        try:
            output = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            import locale
            output = raw.decode(locale.getpreferredencoding(), errors="replace").strip()

    except _subprocess.TimeoutExpired:
        return t("tool.run_shell.timeout", timeout=timeout, command=command)
    except Exception as e:
        logger.warning("run_shell exception: %s", e, exc_info=True)
        return t("tool.run_shell.failed", e=e)

    exit_code = result.returncode

    # ── 输出截断：超过 100KB 尾重截断（错误/结果通常在末尾）──
    output = truncate_output(
        output, MAX_OUTPUT_BYTES, unit="bytes", head_ratio=_SHELL_TRUNC_HEAD_RATIO,
    )

    if output:
        header = t("tool.run_shell.exit_code", code=exit_code) + "\n"
    else:
        header = t("tool.run_shell.exit_code_no_output", code=exit_code)
    return header + output if output else header


# ── JSON Schema ───────────────────────────────────────────────────

schema = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": f"要执行的 shell 命令。{_shell_hint()}",
        },
        "timeout": {
            "type": "integer",
            "description": f"命令超时秒数（默认 {DEFAULT_TIMEOUT}s）",
        },
    },
    "required": ["command"],
}
