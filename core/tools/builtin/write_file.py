"""write_file — 写入/创建本地文件。

安全限制：内容上限 500KB，父目录自动创建，原子写入（崩溃安全）。
无硬校验（Soul 软引导：无明确指令时不主动写入）。
"""

import os
import tempfile
from pathlib import Path

from core.locale import t

MAX_CONTENT_BYTES = 500 * 1024  # 500KB 写入上限


async def execute(arguments: dict) -> str:
    """写入文件内容。

    Args:
        arguments: {"file_path": str, "content": str}

    Returns:
        成功确认或错误描述
    """
    file_path = arguments.get("file_path", "").strip()
    content = arguments.get("content", "")

    if not file_path:
        return t("tool.write_file.empty_path")

    # ── 内容大小检查 ──
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_CONTENT_BYTES:
        return t("tool.write_file.too_large", max_kb=MAX_CONTENT_BYTES // 1024)

    path = Path(file_path)

    # 安全检查：拒绝写入目录路径
    if path.is_dir():
        return t("tool.write_file.is_dir", path=file_path)

    # ── 原子写入：临时文件 + os.replace ──
    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=".tmp_",
            suffix=".write",
        )
        try:
            os.write(tmp_fd, content_bytes)
            os.fsync(tmp_fd)  # 确保数据落盘后再 rename
        finally:
            os.close(tmp_fd)

        os.replace(tmp_path, str(path))  # 原子替换
    except PermissionError:
        return t("tool.write_file.no_permission", path=file_path)
    except OSError as e:
        # 清理可能残留的临时文件
        try:
            os.unlink(tmp_path)
        except (OSError, NameError):
            pass
        return t("tool.write_file.write_failed", e=e)

    size = len(content_bytes)
    return t("tool.write_file.done", path=file_path, size=size)


# ── JSON Schema ───────────────────────────────────────────────────

schema = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "要写入的文件路径",
        },
        "content": {
            "type": "string",
            "description": "要写入的文件内容",
        },
    },
    "required": ["file_path", "content"],
}
