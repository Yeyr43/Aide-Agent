"""run_shell — 执行 Shell 命令。

安全限制：超时上限 60s，输出上限 6000 字符（展示开头，模型自行 grep/head/tail），无命令白名单（Soul 软引导）。
Windows 用系统 shell（cmd.exe），macOS/Linux 用 sh。

输出策略：重复行压缩 → 保留开头给模型看结构 → 元信息告知总量让模型自行精准查询。
实现：subprocess.run + asyncio.to_thread（线程池），避免 Textual asyncio 事件循环兼容问题。
"""

import asyncio
import locale
import logging
import subprocess as _subprocess
import sys as _sys

from core.locale import t
from .definition import ToolDefinition
from core.platform import IS_WINDOWS

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30      # 默认超时（秒）
MAX_TIMEOUT = 60           # 超时硬上限（秒）
MAX_OUTPUT_CHARS = 6000    # 输出字符硬上限（展示开头，超出部分由模型自行 grep/head/tail）
_REPEAT_THRESHOLD = 5      # 连续重复行压缩阈值

# shell 输出编码：Windows cmd.exe 管道输出用 OEM 代码页（GetOEMCP），Unix 统一 UTF-8。
# 用 errors="replace" 兜底：即使编码猜错也不崩，非法字节变 �。
def _detect_shell_encoding() -> str:
    """探测当前平台 shell 输出的最可能编码。"""
    if _sys.platform != "win32":
        return "utf-8"
    # Windows：OEM 代码页才是 cmd.exe 管道输出的真实编码
    # （locale.getpreferredencoding() 返回的是 ANSI 代码页，英文系统上二者不同）
    try:
        import ctypes
        oem_cp = ctypes.windll.kernel32.GetOEMCP()
        if oem_cp > 0:
            return f"cp{oem_cp}"
    except Exception:
        pass
    return locale.getpreferredencoding()

_SHELL_ENCODING = _detect_shell_encoding()


def _compress_repeats(text: str, threshold: int = _REPEAT_THRESHOLD) -> str:
    """压缩连续重复行，节省上下文。

    Lines repeated >= threshold times in a row are collapsed:
    "error" × 30 → "error\\n…（以上重复 30 次）"
    """
    if not text or threshold <= 0:
        return text

    # 统一换行符：Windows cmd.exe 输出 \r\n，split 后每行末尾残留 \r
    text = text.replace("\r\n", "\n")

    lines = text.split("\n")
    if len(lines) < threshold:
        return text

    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        count = 1
        while i + count < len(lines) and lines[i + count] == line:
            count += 1
        if count >= threshold:
            result.append(line)
            result.append(f"…（以上重复 {count} 次）")
        else:
            result.extend(lines[i : i + count])
        i += count

    return "\n".join(result)


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

    # ── 编码探测 ──
    # PowerShell 管道输出默认 UTF-8，与 cmd.exe 的 OEM 编码不同
    encoding = _SHELL_ENCODING
    cmd_lower = command.lower().strip()
    if cmd_lower.startswith("powershell") or cmd_lower.startswith("pwsh"):
        encoding = "utf-8"

    try:
        result = await asyncio.to_thread(
            _subprocess.run,
            command,
            shell=True,
            stdin=_subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding=encoding,
            errors="replace",
            timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()

    except _subprocess.TimeoutExpired:
        return t("tool.run_shell.timeout", timeout=timeout, command=command)
    except Exception as e:
        logger.warning("run_shell exception: %s", e, exc_info=True)
        return t("tool.run_shell.failed", e=e)

    exit_code = result.returncode

    # ── 重复行压缩 ──
    output = _compress_repeats(output)

    # ── 输出截断：保留开头，让模型看清结构并自行 grep/head/tail ──
    if len(output) > MAX_OUTPUT_CHARS:
        line_count = output.count("\n") + 1
        output = (
            output[:MAX_OUTPUT_CHARS]
            + f"\n\n[…输出过长（{line_count} 行，{len(output)} 字符），"
            + "用 head/tail/grep 缩小范围。…]"
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
            "type": "number",
            "description": f"命令超时秒数（默认 {DEFAULT_TIMEOUT}s，1~30s 生效，>30 被外层硬超时掐断）",
        },
    },
    "required": ["command"],
}


definition = ToolDefinition(
    name="run_shell",
    description=t("tool_desc.run_shell"),
    parameters=schema,
    execute=execute,
)
