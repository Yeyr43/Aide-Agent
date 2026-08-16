"""read_file — 读取本地文件内容。

限制单次读取 100KB，超出部分截断并提示。
"""

from pathlib import Path

from core.locale import t
from .definition import ToolDefinition

MAX_BYTES = 100 * 1024  # 100KB


async def execute(arguments: dict) -> str:
    """读取文件内容（分块读取，内存安全）。

    Args:
        arguments: {"file_path": str}

    Returns:
        文件内容字符串，或错误描述
    """
    file_path = arguments.get("file_path", "").strip()
    if not file_path:
        return t("tool.read_file.empty_path")

    path = Path(file_path).expanduser()
    if not path.exists():
        return t("tool.read_file.not_found", path=file_path)

    if path.is_dir():
        return t("tool.read_file.is_dir", path=file_path)

    # ── 分块读取：最多只读 MAX_BYTES + 1 字节到内存 ──
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_BYTES + 1)  # +1 检测是否还有更多内容
            has_more = len(raw) > MAX_BYTES
            if has_more:
                raw = raw[:MAX_BYTES]
    except PermissionError:
        return t("tool.read_file.no_permission", path=file_path)
    except Exception as e:
        return t("tool.read_file.read_failed", e=e)

    try:
        content = raw.decode("utf-8", errors="replace")
    except Exception as e:
        return t("tool.read_file.read_failed", e=e)

    if has_more:
        # 确保截断后的 UTF-8 字节数不超过 MAX_BYTES（多字节字符边界处理）
        truncated = content
        while len(truncated.encode("utf-8")) > MAX_BYTES:
            truncated = truncated[:len(truncated) * 3 // 4]
        return (
            f"{t('tool.read_file.truncated')}\n\n{truncated}"
        )

    return content


# ── JSON Schema ───────────────────────────────────────────────────

schema = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "要读取的文本文件路径（支持 ~ 展开；仅文本文件，超 100KB 被截断）",
        },
    },
    "required": ["file_path"],
}


definition = ToolDefinition(
    name="read_file",
    description=t("tool_desc.read_file"),
    parameters=schema,
    execute=execute,
)
