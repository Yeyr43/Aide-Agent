"""search_in_files — 文件内容搜索 + 目录浏览，二合一。

当 pattern 非空时：正则搜索文件内容（类似 grep），支持 glob 过滤和递归。
当 pattern 为空时：列出目录内容（类似 ls），支持 glob 过滤和递归。

安全限制：搜索最多 5000 个文件，跳过 >1MB 的文件；列表最多 200 条目。
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
from pathlib import Path

from core.locale import t
from .definition import ToolDefinition

MAX_FILES = 5000
MAX_FILE_SIZE = 1 * 1024 * 1024
MAX_LIST_ITEMS = 200
MAX_LIST_SIZE = 20 * 1024
MAX_LIST_DEPTH = 5

_IGNORED_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".tox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".idea", ".vscode", ".DS_Store", ".next", ".nuxt",
})


async def execute(arguments: dict) -> str:
    """搜索文件内容或列出目录。

    Args:
        arguments: {
            "pattern": str         — 正则搜索模式。空字符串 = 目录列表模式
            "directory": str       — 搜索/列表目录（默认当前目录）
            "glob": str            — 文件名过滤 glob
            "max_results": int     — 最大结果数（搜索默认 50 最大 200，列表默认 200）
            "case_sensitive": bool — 搜索是否区分大小写（默认 False）
            "recursive": bool      — 列表模式是否递归（默认 False）
        }
    """
    pattern = arguments.get("pattern", "").strip()
    directory = arguments.get("directory", "") or "."
    dir_path = Path(directory).expanduser().resolve()

    if not dir_path.exists():
        return t("tool.search_in_files.dir_not_found", path=dir_path)
    if not dir_path.is_dir():
        return t("tool.search_in_files.not_dir", path=dir_path)

    # ── pattern 为空 → 目录列表模式 ──
    if not pattern:
        return await _list_mode(dir_path, arguments)

    # ── pattern 非空 → 内容搜索模式 ──
    return await _search_mode(dir_path, pattern, arguments)


# ── 内容搜索模式 ──────────────────────────────────────────────────────────

async def _search_mode(dir_path: Path, pattern: str, arguments: dict) -> str:
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

    return await asyncio.to_thread(
        _search_mode_sync, dir_path, file_glob, pattern, regex, max_results,
    )


def _search_mode_sync(
    dir_path: Path, file_glob: str, pattern: str, regex: re.Pattern,
    max_results: int,
) -> str:
    """Synchronous content search — runs via asyncio.to_thread to avoid blocking."""
    results: list[str] = []
    file_count = 0
    oversized = 0

    try:
        file_iter = _iter_files(dir_path, file_glob)
    except PermissionError:
        return t("tool.search_in_files.no_permission", path=dir_path)

    for file_path in file_iter:
        if file_count >= MAX_FILES:
            results.append("\n" + t("tool.search_in_files.too_many_files", max=MAX_FILES))
            break
        file_count += 1

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


# ── 目录列表模式 ──────────────────────────────────────────────────────────

async def _list_mode(dir_path: Path, arguments: dict) -> str:
    file_glob = arguments.get("glob", "") or "*"
    recursive = arguments.get("recursive", False)
    max_results = arguments.get("max_results", MAX_LIST_ITEMS)
    if not isinstance(max_results, int) or max_results < 1:
        max_results = MAX_LIST_ITEMS
    max_results = min(max_results, MAX_LIST_ITEMS)

    try:
        limit = max_results + 1  # +1 to detect truncation
        if recursive:
            entries = _rglob_depth(dir_path, file_glob, MAX_LIST_DEPTH, limit)
        else:
            entries = _scandir_entries(dir_path, file_glob, limit)
    except PermissionError:
        return t("tool.search_in_files.no_permission", path=dir_path)
    except Exception as e:
        return t("tool.search_in_files.list_failed", e=e)

    if not entries:
        result = t("tool.search_in_files.empty_dir", path=dir_path)
        if file_glob != "*":
            result += t("tool.search_in_files.empty_pattern", pattern=file_glob)
        return result

    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))

    lines = [f"## {dir_path.resolve()}", t("tool.search_in_files.total", n=len(entries)) + "\n"]
    count = 0

    for entry in entries:
        if count >= max_results:
            lines.append("\n" + t("tool.search_in_files.max_items", max=max_results))
            break
        icon = "📁" if entry["is_dir"] else "📄"
        display = entry["display"]
        lines.append(f"  {icon} {display:<40} {entry['size']:>8}  {entry['mtime']}")
        count += 1

    result = "\n".join(lines)
    if len(result.encode("utf-8")) > MAX_LIST_SIZE:
        result = result.encode("utf-8")[:MAX_LIST_SIZE].decode("utf-8", errors="replace")
        result += "\n" + t("tool.search_in_files.too_large")
    return result


# ── 文件遍历 ──────────────────────────────────────────────────────────────

def _iter_files(dir_path: Path, file_glob: str):
    """流式生成器：逐个产出要搜索的文件，不预收集到内存。"""
    pattern = file_glob or "*"
    for f in dir_path.rglob(pattern, recurse_symlinks=False):
        if f.is_file() and not any(p in _IGNORED_DIRS for p in f.parts):
            yield f


def _search_file(file_path: Path, regex: re.Pattern) -> list[tuple[int, str]]:
    """在单个文件中搜索，返回 (行号, 行内容) 列表。"""
    matches = []
    with open(file_path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            if regex.search(line):
                matches.append((i, line.rstrip("\n\r")[:300]))
    return matches


# ── 目录遍历（os.scandir，stat 由 OS 缓存） ───────────────────────────────

def _fmt_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f}GB"


def _fmt_time(timestamp: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(timestamp).strftime("%m-%d %H:%M")


def _scandir_entries(dir_path: Path, pattern: str, max_entries: int | None = None) -> list[dict]:
    """使用 os.scandir 列出目录（非递归）。"""
    entries: list[dict] = []
    try:
        with os.scandir(dir_path) as it:
            for de in it:
                if not fnmatch.fnmatch(de.name, pattern):
                    continue
                try:
                    st = de.stat()
                    size = _fmt_size(st.st_size)
                    mtime = _fmt_time(st.st_mtime)
                except OSError:
                    size = "?"
                    mtime = "?"
                entries.append({
                    "name": de.name, "display": de.name,
                    "is_dir": de.is_dir(follow_symlinks=False), "size": size, "mtime": mtime,
                })
                if max_entries is not None and len(entries) >= max_entries:
                    break
    except PermissionError:
        raise
    return entries


def _rglob_depth(root: Path, pattern: str, max_depth: int, max_entries: int | None = None) -> list[dict]:
    entries: list[dict] = []
    _walk_depth(root, root, pattern, 0, max_depth, max_entries, entries)
    return entries


def _walk_depth(
    root: Path, current: Path, pattern: str, depth: int, max_depth: int,
    max_entries: int | None, entries: list[dict],
) -> None:
    if depth > max_depth:
        return
    if max_entries is not None and len(entries) >= max_entries:
        return
    try:
        with os.scandir(current) as it:
            for de in sorted(it, key=lambda d: d.name):
                if max_entries is not None and len(entries) >= max_entries:
                    return
                rel = str(Path(de.path).relative_to(root))
                if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(de.name, pattern):
                    try:
                        st = de.stat()
                        size = _fmt_size(st.st_size)
                        mtime = _fmt_time(st.st_mtime)
                    except OSError:
                        size = "?"
                        mtime = "?"
                    entries.append({
                        "name": de.name, "display": rel,
                        "is_dir": de.is_dir(follow_symlinks=False), "size": size, "mtime": mtime,
                    })
                if de.is_dir(follow_symlinks=False) and de.name not in _IGNORED_DIRS:
                    _walk_depth(root, Path(de.path), pattern, depth + 1, max_depth, max_entries, entries)
    except PermissionError:
        pass


# ── JSON Schema ───────────────────────────────────────────────────────────

schema = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "正则搜索模式。留空则进入目录列表模式（显示文件和子目录）。",
        },
        "directory": {
            "type": "string",
            "description": "搜索/列表目录路径（默认当前目录）",
        },
        "glob": {
            "type": "string",
            "description": "文件名过滤 glob（fnmatch 风格，不支持 {..} 花括号），如 '*.py'、'**/*.ts'",
        },
        "max_results": {
            "type": "integer",
            "description": "最大结果数（搜索默认 50 最大 200，列表默认 200）",
        },
        "case_sensitive": {
            "type": "boolean",
            "description": "是否区分大小写（搜索模式，默认 false）",
        },
        "recursive": {
            "type": "boolean",
            "description": "是否递归（列表模式，默认 false）",
        },
    },
    "required": [],
}


definition = ToolDefinition(
    name="search_in_files",
    description=t("tool_desc.search_in_files"),
    parameters=schema,
    execute=execute,
)
