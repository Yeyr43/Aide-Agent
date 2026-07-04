"""FileWatcher — 通用目录轮询监听器。

P4 Batch 2: 从 MCPAdapter 提取，可复用于任何需要热加载的目录。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

OnChangeCallback = Callable[[], Awaitable[tuple[int, int, int]]]


class FileWatcher:
    """目录轮询监听器 — 检测 .json 文件增删改时触发回调。

    用法:
        watcher = FileWatcher("/path/to/dir", on_change=reload_fn)
        await watcher.start()
        ...
        watcher.stop()
    """

    def __init__(
        self,
        watch_dir: str,
        on_change: OnChangeCallback,
        interval: float = 5.0,
    ) -> None:
        self._watch_dir = watch_dir
        self._on_change = on_change
        self._interval = interval
        self._mtimes: dict[str, float] = {}
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动后台轮询。"""
        # 初始化 mtime 快照
        dir_path = Path(self._watch_dir)
        if dir_path.is_dir():
            for f in dir_path.glob("*.json"):
                try:
                    self._mtimes[f.name] = f.stat().st_mtime
                except OSError:
                    pass

        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll_loop())
        logger.info(f"[FileWatcher] 已启动: {self._watch_dir}")

    def stop(self) -> None:
        """停止轮询。"""
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self._mtimes.clear()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _poll_loop(self) -> None:
        """后台轮询循环。"""
        while True:
            await asyncio.sleep(self._interval)

            dir_path = Path(self._watch_dir)
            if not dir_path.is_dir():
                continue

            current: dict[str, float] = {}
            for f in dir_path.glob("*.json"):
                try:
                    current[f.name] = f.stat().st_mtime
                except OSError:
                    pass

            if current != self._mtimes:
                added = set(current) - set(self._mtimes)
                removed = set(self._mtimes) - set(current)
                changed = {
                    k for k in current
                    if k in self._mtimes and current[k] != self._mtimes[k]
                }

                if added or removed or changed:
                    logger.info(
                        f"[FileWatcher] 检测到变更 — "
                        f"新增: {added}, 删除: {removed}, 修改: {changed}"
                    )
                    await self._on_change()

                self._mtimes = current
