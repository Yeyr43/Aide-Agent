"""read_file — 读取本地文件内容。

限制单次读取 100KB，超出部分截断并提示。
"""

from pathlib import Path

from core.locale import t

MAX_BYTES = 100 * 1024  # 100KB


async def execute(arguments: dict) -> str:
    """读取文件内容。

    Args:
        arguments: {"file_path": str}

    Returns:
        文件内容字符串，或错误描述
    """
    file_path = arguments.get("file_path", "").strip()
    if not file_path:
        return t("tool.read_file.empty_path")

    path = Path(file_path)
    if not path.exists():
        return t("tool.read_file.not_found", path=file_path)

    if path.is_dir():
        return t("tool.read_file.is_dir", path=file_path)

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        return t("tool.read_file.no_permission", path=file_path)
    except Exception as e:
        return t("tool.read_file.read_failed", e=e)

    if len(content.encode("utf-8")) > MAX_BYTES:
        # 精确字节截断：从字符级逐字缩减到 100KB 以内
        truncated = content
        while len(truncated.encode("utf-8")) > MAX_BYTES:
            truncated = truncated[:len(truncated) * 3 // 4]  # 二分逼近（UTF-8 最坏 4B/字）
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
            "description": "要读取的文件路径（绝对路径或相对于当前工作目录的路径）",
        },
    },
    "required": ["file_path"],
}
