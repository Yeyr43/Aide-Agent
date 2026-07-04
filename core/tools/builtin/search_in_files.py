"""search_in_files — 在文件中搜索内容（正则表达式）。

类似 ripgrep/grep，支持 glob 过滤和递归搜索。
安全限制：最多扫描 5000 个文件，跳过 >1MB 的文件。
"""

import re
from pathlib import Path

from core.locale import t

MAX_FILES = 5000                # 最多扫描文件数
MAX_FILE_SIZE = 1 * 1024 * 1024  # 跳过超过 1MB 的文件


async def execute(arguments: dict) -> str:
    """在文件中搜索匹配 pattern 的内容。

    Args:
        arguments: {
            "pattern": str       — 正则表达式搜索模式
            "directory": str     — 搜索目录（默认当前工作目录）
            "glob": str          — 文件名过滤 glob（如 "*.py"、"*.{ts,tsx}"）
            "max_results": int   — 最大结果数（默认 50，最大 200）
            "case_sensitive": bool — 是否区分大小写（默认 False）
        }

    Returns:
        搜索结果，格式为 "文件:行号:内容"
    """
    pattern = arguments.get("pattern", "").strip()
    if not pattern:
        return t("tool.search_in_files.empty_pattern")

    directory = arguments.get("directory", "") or "."
    dir_path = Path(directory).expanduser().resolve()
    if not dir_path.exists():
        return t("tool.search_in_files.dir_not_found", path=dir_path)
    if not dir_path.is_dir():
        return t("tool.search_in_files.not_dir", path=dir_path)

    file_glob = arguments.get("glob", "")
    max_results = arguments.get("max_results", 50)
    if not isinstance(max_results, int) or max_results < 1:
        max_results = 50
    max_results = min(max_results, 200)

    case_sensitive = arguments.get("case_sensitive", False)
    flags = 0 if case_sensitive else re.IGNORECASE

    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return t("tool.search_in_files.invalid_regex", e=e)

    results: list[str] = []
    file_count = 0
    oversized = 0

    try:
        files = _gather_files(dir_path, file_glob)
    except PermissionError as e:
        return t("tool.search_in_files.no_permission", e=e)

    for file_path in files:
        if file_count >= MAX_FILES:
            results.append("\n" + t("tool.search_in_files.too_many_files", max=MAX_FILES))
            break

        file_count += 1

        # ── 跳过过大文件 ──
        try:
            if file_path.stat().st_size > MAX_FILE_SIZE:
                oversized += 1
                continue
        except OSError:
            continue

        try:
            for line_no, line in _search_file(file_path, regex):
                results.append(f"{file_path}:{line_no}:{line}")
                if len(results) >= max_results:
                    break
        except (UnicodeDecodeError, PermissionError, OSError):
            continue

        if len(results) >= max_results:
            break

    if oversized > 0:
        results.append(t("tool.search_in_files.skipped_large", n=oversized))

    if not results:
        return t("tool.search_in_files.no_match", pattern=pattern)
    if len(results) >= max_results:
        results.append("\n" + t("tool.search_in_files.truncated", max=max_results))

    return "\n".join(results)


def _gather_files(dir_path: Path, file_glob: str) -> list[Path]:
    """收集要搜索的文件列表。"""
    if file_glob:
        files = sorted(dir_path.rglob(file_glob))
    else:
        files = sorted(dir_path.rglob("*"))

    # 过滤：只取文件，跳过常见忽略目录
    ignored = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox",
               ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
               ".idea", ".vscode", ".DS_Store", ".next", ".nuxt"}
    return [
        f for f in files
        if f.is_file()
        and not any(p in ignored for p in f.parts)
    ]


def _search_file(file_path: Path, regex: re.Pattern) -> list[tuple[int, str]]:
    """在单个文件中搜索，返回 (行号, 行内容) 列表。"""
    matches = []
    with open(file_path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            if regex.search(line):
                matches.append((i, line.rstrip("\n\r")[:300]))
    return matches


# ── JSON Schema ───────────────────────────────────────────────────

schema = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "正则表达式搜索模式",
        },
        "directory": {
            "type": "string",
            "description": "搜索目录路径（默认当前目录）",
        },
        "glob": {
            "type": "string",
            "description": "文件名过滤 glob，如 '*.py'、'*.{ts,tsx}'",
        },
        "max_results": {
            "type": "integer",
            "description": "最大结果数（默认 50，最大 200）",
        },
        "case_sensitive": {
            "type": "boolean",
            "description": "是否区分大小写（默认 false）",
        },
    },
    "required": ["pattern"],
}
