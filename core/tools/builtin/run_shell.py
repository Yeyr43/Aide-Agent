"""run_shell — 执行 Shell 命令。

安全限制：超时上限 60s，输出上限 100KB，无命令白名单（Soul 软引导）。
"""

import asyncio
import logging

from core.locale import t

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30      # 默认超时（秒）
MAX_TIMEOUT = 60           # 超时硬上限（秒）
MAX_OUTPUT_BYTES = 100 * 1024  # 输出硬上限（100KB）


async def execute(arguments: dict) -> str:
    """异步执行 shell 命令。

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
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # stderr → stdout 合并
        )

        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace").strip()

    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            logger.debug("Failed to kill subprocess after timeout for command: %s", command)
        return t("tool.run_shell.timeout", timeout=timeout, command=command)
    except FileNotFoundError:
        return t("tool.run_shell.not_found", command=command)
    except Exception as e:
        return t("tool.run_shell.failed", e=e)

    exit_code = proc.returncode

    # ── 输出截断：超过 100KB 截断首尾 ──
    if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
        half = MAX_OUTPUT_BYTES // 2
        head = output[:half]
        tail = output[-half:]
        output = (
            f"{head}\n\n"
            f"…（输出过大，已截断）…\n\n"
            f"{tail}"
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
            "description": "要执行的 shell 命令",
        },
        "timeout": {
            "type": "integer",
            "description": f"命令超时秒数（默认 {DEFAULT_TIMEOUT}s）",
        },
    },
    "required": ["command"],
}
