"""list_dir — 列出目录内容。

支持 glob 过滤和递归深度控制。
"""

from pathlib import Path

from core.locale import t


MAX_ITEMS = 200         # 单次最多返回条目数
MAX_SIZE = 20 * 1024    # 20KB 输出上限
MAX_DEPTH = 5            # 递归深度上限


async def execute(arguments: dict) -> str:
    """列出目录中的文件和子目录。

    Args:
        arguments: {"path": str, "pattern": str (optional), "recursive": bool (optional)}

    Returns:
        文件/目录列表字符串，或错误描述
    """
    dir_path = arguments.get("path", "").strip()
    if not dir_path:
        dir_path = "."

    path = Path(dir_path)
    if not path.exists():
        return t("tool.list_dir.not_found", path=dir_path)
    if not path.is_dir():
        return t("tool.list_dir.not_dir", path=dir_path)

    pattern = arguments.get("pattern", "").strip() or "*"
    recursive = arguments.get("recursive", False)

    try:
        if recursive:
            entries = _rglob_depth(path, pattern, MAX_DEPTH)
        else:
            entries = list(path.glob(pattern))
    except PermissionError:
        return t("tool.list_dir.no_permission", path=dir_path)
    except Exception as e:
        return t("tool.list_dir.failed", e=e)

    if not entries:
        result = t("tool.list_dir.empty", path=dir_path)
        if pattern != "*":
            result += t("tool.list_dir.empty_pattern", pattern=pattern)
        return result

    # 排序：目录优先，然后字母序
    entries.sort(key=lambda p: (not p.is_dir(), p.name.lower()))

    lines = [f"## {path.resolve()}", t("tool.list_dir.total", n=len(entries)) + "\n"]
    count = 0

    for entry in entries:
        if count >= MAX_ITEMS:
            lines.append("\n" + t("tool.list_dir.max_items", max=MAX_ITEMS))
            break

        try:
            stat = entry.stat()
            size = _fmt_size(stat.st_size)
            mtime = _fmt_time(stat.st_mtime)
        except OSError:
            size = "?"
            mtime = "?"

        icon = "📁" if entry.is_dir() else "📄"
        display = str(entry.relative_to(path) if entry != path else entry)
        lines.append(f"  {icon} {display:<40} {size:>8}  {mtime}")

        count += 1

    result = "\n".join(lines)
    if len(result.encode("utf-8")) > MAX_SIZE:
        result = result[:MAX_SIZE] + "\n" + t("tool.list_dir.too_large")
    return result


def _fmt_size(size_bytes: int) -> str:
    """格式化文件大小。"""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f}GB"


def _fmt_time(timestamp: float) -> str:
    """格式化 mtime 为简短日期。"""
    from datetime import datetime
    return datetime.fromtimestamp(timestamp).strftime("%m-%d %H:%M")


def _rglob_depth(root: Path, pattern: str, max_depth: int) -> list[Path]:
    """深度受限的递归 glob，防止无限递归拖垮性能。"""
    import fnmatch
    entries: list[Path] = []
    _walk_depth(root, root, pattern, 0, max_depth, fnmatch, entries)
    return entries


def _walk_depth(
    root: Path, current: Path, pattern: str, depth: int, max_depth: int,
    fnmatch, entries: list[Path],
) -> None:
    if depth > max_depth:
        return
    try:
        for child in sorted(current.iterdir()):
            rel = str(child.relative_to(root))
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(child.name, pattern):
                entries.append(child)
            if child.is_dir():
                _walk_depth(root, child, pattern, depth + 1, max_depth, fnmatch, entries)
    except PermissionError:
        pass


# ── JSON Schema ───────────────────────────────────────────────────

schema = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "要列出内容的目录路径（绝对路径或相对路径）。默认为当前工作目录。",
        },
        "pattern": {
            "type": "string",
            "description": "文件名匹配模式（glob），例如 '*.py'、'test_*'。默认为 '*'（全部）。",
        },
        "recursive": {
            "type": "boolean",
            "description": "是否递归列出子目录。默认 false。",
        },
    },
    "required": [],
}
