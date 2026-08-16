"""write_file — 写入/创建/精确编辑本地文件。

支持两种模式：
- 覆写：传入 content，创建或覆盖整个文件
- 手术替换：传入 old_string + new_string，精确替换首次出现（old_string 必须唯一）

两种模式不能同时使用。原子写入（临时文件 + os.replace），崩溃安全。
"""

from __future__ import annotations

from pathlib import Path

from core.locale import t
from .definition import ToolDefinition
from core.storage import atomic_write_text

MAX_CONTENT_BYTES = 500 * 1024  # 500KB 写入上限
MAX_FILE_SIZE = 500 * 1024       # 500KB 编辑文件大小上限


async def execute(arguments: dict) -> str:
    """写入或编辑文件内容。

    Args:
        arguments: {
            "file_path": str    — 目标文件路径
            "content": str      — 覆写模式：写入的完整内容
            "old_string": str   — 编辑模式：要替换的原字符串
            "new_string": str   — 编辑模式：替换后的新字符串
        }
    """
    file_path = arguments.get("file_path", "").strip()
    if not file_path:
        return t("tool.write_file.empty_path")

    has_content = "content" in arguments
    has_old = "old_string" in arguments
    has_new = "new_string" in arguments

    # ── 模式冲突检测 ──
    if has_content and (has_old or has_new):
        return t("tool.write_file.mode_conflict")
    if has_old != has_new:
        return t("tool.write_file.edit_pair")

    if has_old and has_new:
        return await _edit_mode(file_path, arguments["old_string"], arguments["new_string"])

    if not has_content:
        # 只传 file_path 无任何模式：拒绝而非静默写空文件（会清空原文件）
        return t("tool.write_file.no_mode")

    return await _write_mode(file_path, arguments["content"])


# ── 覆写模式 ──────────────────────────────────────────────────────────────

async def _write_mode(file_path: str, content: str) -> str:
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_CONTENT_BYTES:
        return t("tool.write_file.too_large", max_kb=MAX_CONTENT_BYTES // 1024)

    path = Path(file_path).expanduser()  # 与 _edit_mode 一致：~ 前缀展开（曾写进字面 ~ 目录）
    if path.is_dir():
        return t("tool.write_file.is_dir", path=file_path)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, content)
    except PermissionError:
        return t("tool.write_file.no_permission", path=file_path)
    except OSError as e:
        return t("tool.write_file.write_failed", e=e)

    return t("tool.write_file.done", path=file_path, size=len(content_bytes))


# ── 手术替换模式 ──────────────────────────────────────────────────────────

async def _edit_mode(file_path: str, old_string: str, new_string: str) -> str:
    if not old_string:
        return t("tool.write_file.empty_old")

    path = Path(file_path).expanduser().resolve()

    if not path.exists():
        return t("tool.write_file.not_found", path=path)
    if not path.is_file():
        return t("tool.write_file.not_file", path=path)

    try:
        if path.stat().st_size > MAX_FILE_SIZE:
            return t("tool.write_file.too_large", max_kb=MAX_FILE_SIZE // 1024)
    except OSError:
        pass

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return t("tool.write_file.decode_error", path=path)
    except PermissionError:
        return t("tool.write_file.no_read_permission", path=path)

    count = content.count(old_string)
    if count == 0:
        return t("tool.write_file.not_found_in_file")
    if count > 1:
        return t("tool.write_file.not_unique", count=count)

    new_content = content.replace(old_string, new_string, 1)

    try:
        atomic_write_text(path, new_content)
    except PermissionError:
        return t("tool.write_file.no_write_permission", path=path)
    except OSError as e:
        return t("tool.write_file.write_failed", e=e)

    old_lines = old_string.count("\n")
    new_lines = new_string.count("\n")
    return t(
        "tool.write_file.edited",
        name=path.name,
        old_lines=old_lines + 1,
        new_lines=new_lines + 1,
        old_char=len(old_string),
        new_char=len(new_string),
    )


# ── JSON Schema ───────────────────────────────────────────────────────────

schema = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "要写入/编辑的文件路径（支持 ~ 展开，自动创建缺失的父目录）",
        },
        "content": {
            "type": "string",
            "description": "覆写/新建模式：写入的完整内容（≤500KB，全量替换旧内容）。与 old_string/new_string 互斥，二选一必填。",
        },
        "old_string": {
            "type": "string",
            "description": "编辑模式：要替换的原字符串（必须在文件中唯一出现且精确匹配）。与 new_string 配对使用。",
        },
        "new_string": {
            "type": "string",
            "description": "编辑模式：替换后的新字符串。与 old_string 配对使用。",
        },
    },
    "required": ["file_path"],
}


definition = ToolDefinition(
    name="write_file",
    description=t("tool_desc.write_file"),
    parameters=schema,
    execute=execute,
)
