"""MCP 配置目录监听 — 复用 core.watcher 通用后端。

通用后端（WatcherBackend / PollingBackend / WatchfilesBackend / FileWatcher）
统一收口到 core/watcher.py，本模块仅做兼容性 re-export，避免破坏既有导入。
"""

from core.watcher import (
    FileWatcher,
    OnChangeCallback,
    PollingBackend,
    WatchfilesBackend,
    WatcherBackend,
)

__all__ = [
    "FileWatcher",
    "OnChangeCallback",
    "PollingBackend",
    "WatchfilesBackend",
    "WatcherBackend",
]
