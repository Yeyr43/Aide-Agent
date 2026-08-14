"""VersionManager — Prompt 文件备份、版本日志、回滚。

从 ReflectEngine 提取的独立模块，管理 ~/.aide/backups/ 下的备份文件
和 version_log.json 版本记录。
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from core.setup import aide_dir
from core.storage import atomic_write_json

logger = logging.getLogger(__name__)

# ── 路径常量 ──────────────────────────────────────────────────────────

AGENT_ROOT = aide_dir() / "agent"
BACKUPS_DIR = aide_dir() / "backups"


# ── 备份 ──────────────────────────────────────────────────────────────

def _backup_prompt(prompt_path: Path) -> str | None:
    """备份当前 prompt 文件到 backups/ 目录。"""
    if not prompt_path.exists():
        return None
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_name = f"{prompt_path.name}_{timestamp}.backup"
    backup_path = BACKUPS_DIR / backup_name
    shutil.copy2(prompt_path, backup_path)
    logger.info(f"Prompt 已备份: {backup_name}")
    return backup_name


# ── 版本日志 ──────────────────────────────────────────────────────────

def _append_version_log(filename: str, backup_name: str) -> None:
    """追加版本记录到 backups/version_log.json。"""
    log_path = BACKUPS_DIR / "version_log.json"
    version_log: dict = {}
    if log_path.exists():
        try:
            version_log = json.loads(log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            version_log = {}
    if filename not in version_log:
        version_log[filename] = []
    backup_file = BACKUPS_DIR / backup_name
    size = backup_file.stat().st_size if backup_file.exists() else 0
    version_log[filename].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "backup": backup_name,
        "size": size,
    })
    atomic_write_json(log_path, version_log)


# ── 回滚 ──────────────────────────────────────────────────────────────

def rollback_prompt(prompt_type: str, n: int = 0) -> tuple[bool, str]:
    """回滚 prompt 到第 N 个历史版本。

    Args:
        prompt_type: "preferences" | "workflows" | "long_term_memory"
        n: 0 = 最新备份, 1 = 上一个, ...
    """
    filename = f"{prompt_type}.md"
    log_path = BACKUPS_DIR / "version_log.json"
    if not log_path.exists():
        return False, f"无版本历史 — {log_path} 不存在"
    try:
        version_log = json.loads(log_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False, "版本日志损坏，无法回滚"
    history = version_log.get(filename, [])
    if not history:
        return False, f"{prompt_type} 无备份记录"
    if n < 0 or n >= len(history):
        return False, f"无效的备份编号 {n}（可用 0-{len(history)-1}）"
    entry = history[-(n + 1)]
    backup_name = entry["backup"]
    backup_path = BACKUPS_DIR / backup_name
    if not backup_path.exists():
        return False, f"备份文件丢失: {backup_name}"
    prompt_path = AGENT_ROOT / filename
    shutil.copy2(backup_path, prompt_path)
    logger.info(f"Prompt {filename} 已回滚到 {backup_name}")
    return True, f"{filename} 已回滚到备份 {backup_name}（{entry['timestamp']}）"
