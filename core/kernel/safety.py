"""工具调用安全检查 — 高危命令拦截 + 文件覆盖保护。

从 FunctionCallingLoop._should_block 提取的独立模块，
可复用于 Plugin SDK、命令处理器等场景。
"""

from __future__ import annotations

import re as _re
from pathlib import Path as _Path


# ── 破坏性 Shell 命令模式（高危，需要用户确认）─────────────────────────

DESTRUCTIVE_PATTERNS: tuple = (
    r'\brm\s+-rf\b', r'\brm\s+-r\b', r'\bdd\s+if=', r'\bmkfs\b',
    r'\bformat\b', r'\bfdisk\b', r'\bchmod\s+777\b',
    r':\(\)\s*\{',  # fork bomb
    r'>\s*/dev/sd[a-z]', r'>\s*/dev/nvme',
)


# ── 引号内容剥离 ──────────────────────────────────────────────────────

def strip_quoted(text: str) -> str:
    """移除 shell 命令中引号包裹的内容，避免子串误判。

    echo "use rm -rf" → echo
    echo 'delete with rm -rf /' → echo
    """
    # 移除双引号内容
    text = _re.sub(r'"[^"]*"', '""', text)
    # 移除单引号内容
    text = _re.sub(r"'[^']*'", "''", text)
    return text


# ── 安全检查 ──────────────────────────────────────────────────────────

def check_tool_safety(tool_name: str, arguments: dict) -> str | None:
    """检查工具调用是否高风险。返回阻止原因或 None。

    - run_shell: 含破坏性命令 → 阻止
    - write_file: 目标文件已存在 → 警告（可能覆盖用户数据）

    Args:
        tool_name: 工具名（如 "run_shell", "write_file"）
        arguments: LLM 返回的参数字典

    Returns:
        阻止原因字符串，或 None（安全）
    """
    if tool_name == "run_shell":
        command = arguments.get("command", arguments.get("cmd", ""))
        if isinstance(command, str):
            # 移除引号内内容后再检查，避免误判 echo "rm -rf" 这类无害命令
            unquoted = strip_quoted(command)
            for pattern in DESTRUCTIVE_PATTERNS:
                if _re.search(pattern, unquoted):
                    return "Shell 命令包含破坏性操作，已被阻止"

    if tool_name == "write_file":
        filepath = arguments.get("file_path", arguments.get("filepath", ""))
        if filepath:
            if _Path(filepath).exists():
                return f"目标文件已存在 ({filepath})，覆盖将丢失原有内容"

    return None
