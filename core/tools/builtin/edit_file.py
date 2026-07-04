"""edit_file — 精确字符串替换编辑文件。

只替换 old_string 在文件中首次出现的位置。如果 old_string
在文件中不唯一或不存在，返回错误而非修改文件。
安全限制：拒绝编辑超过 500KB 的文件，原子写入（崩溃安全）。
"""

import os
import tempfile
from pathlib import Path

from core.locale import t

MAX_FILE_SIZE = 500 * 1024  # 500KB 文件大小上限


async def execute(arguments: dict) -> str:
    """在文件中执行精确字符串替换。

    Args:
        arguments: {
            "file_path": str   — 要编辑的文件绝对路径
            "old_string": str  — 要替换的原字符串（必须精确匹配）
            "new_string": str  — 替换后的新字符串
        }

    Returns:
        操作结果描述
    """
    file_path = arguments.get("file_path", "").strip()
    if not file_path:
        return t("tool.edit_file.empty_path")

    old_string = arguments.get("old_string", "")
    new_string = arguments.get("new_string", "")

    if not old_string:
        return t("tool.edit_file.empty_old")

    path = Path(file_path).expanduser().resolve()

    if not path.exists():
        return t("tool.edit_file.not_found", path=path)

    if not path.is_file():
        return t("tool.edit_file.not_file", path=path)

    # ── 文件大小检查 ──
    try:
        if path.stat().st_size > MAX_FILE_SIZE:
            return t("tool.edit_file.too_large", max_kb=MAX_FILE_SIZE // 1024)
    except OSError:
        pass

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return t("tool.edit_file.decode_error", path=path)
    except PermissionError:
        return t("tool.edit_file.no_read_permission", path=path)

    count = content.count(old_string)
    if count == 0:
        return t("tool.edit_file.not_found_in_file")

    if count > 1:
        return t("tool.edit_file.not_unique", count=count)

    new_content = content.replace(old_string, new_string, 1)

    # ── 原子写入：临时文件 + os.replace ──
    try:
        data = new_content.encode("utf-8")

        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=".tmp_",
            suffix=".edit",
        )
        try:
            os.write(tmp_fd, data)
            os.fsync(tmp_fd)
        finally:
            os.close(tmp_fd)

        os.replace(tmp_path, str(path))  # 原子替换
    except PermissionError:
        return t("tool.edit_file.no_write_permission", path=path)
    except OSError as e:
        try:
            os.unlink(tmp_path)
        except (OSError, NameError):
            pass
        return t("tool.edit_file.write_failed", e=e)

    # 计算变更摘要
    old_lines = old_string.count("\n")
    new_lines = new_string.count("\n")
    old_char = len(old_string)
    new_char = len(new_string)

    return t(
        "tool.edit_file.done",
        name=path.name,
        old_lines=old_lines + 1,
        new_lines=new_lines + 1,
        old_char=old_char,
        new_char=new_char,
    )


# ── JSON Schema ───────────────────────────────────────────────────

schema = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "要编辑的文件绝对路径",
        },
        "old_string": {
            "type": "string",
            "description": "要替换的原字符串（必须在文件中唯一出现且精确匹配）",
        },
        "new_string": {
            "type": "string",
            "description": "替换后的新字符串",
        },
    },
    "required": ["file_path", "old_string", "new_string"],
}
