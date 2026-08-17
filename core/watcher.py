"""通用目录监听器 — 可插拔后端的 watchfiles/polling 统一实现。

单一事实来源：core/mcp/watcher.py 与 core/plugins/watcher.py 均复用本模块。
默认轮询后端（零依赖），可替换为事件驱动后端（watchfiles）。

用法:
    # 默认轮询后端
    watcher = FileWatcher("/path/to/dir", on_change=reload_fn)
    await watcher.start()

    # 事件驱动后端（需 pip install watchfiles）
    watcher = FileWatcher("/path/to/dir", on_change=reload_fn,
                          backend=WatchfilesBackend())
    await watcher.start()
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable, Awaitable, Protocol

logger = logging.getLogger(__name__)

OnChangeCallback = Callable[[], Awaitable[tuple[int, int, int]]]


# ── 后端接口 ───────────────────────────────────────────────────────────


class WatcherBackend(Protocol):
    """文件监听后端协议。实现此接口即可插拔不同的监听策略。"""

    async def start(self, watch_dir: str, on_change: OnChangeCallback) -> None:
        """启动监听。"""
        ...

    async def stop(self) -> None:
        """停止监听，释放资源。"""
        ...

    @property
    def is_running(self) -> bool:
        """是否正在运行。"""
        ...


# ── 轮询后端（默认，零依赖） ────────────────────────────────────────────


class PollingBackend:
    """mtime 轮询后端 — 定时递归扫描目录检测文件变更。

    递归（rglob）覆盖子目录：MCP 配置目录（扁平 *.json）与插件目录
    （子目录式布局）均适用。间隔可配置。
    """

    def __init__(self, interval: float = 5.0) -> None:
        self._interval = interval
        self._mtimes: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self._watch_dir: str = ""
        self._on_change: OnChangeCallback | None = None

    async def start(self, watch_dir: str, on_change: OnChangeCallback) -> None:
        self._watch_dir = watch_dir
        self._on_change = on_change

        # 初始化 mtime 快照
        self._mtimes = self._scan()

        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll_loop())
        logger.info(f"[PollingBackend] 已启动: {watch_dir} (间隔 {self._interval}s)")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._mtimes.clear()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _scan(self) -> dict[str, float]:
        """递归扫描 watch_dir 下所有文件 → {相对路径: mtime}。"""
        result: dict[str, float] = {}
        dir_path = Path(self._watch_dir)
        if not dir_path.is_dir():
            return result
        for f in dir_path.rglob("*"):
            if not f.is_file():
                continue
            try:
                result[f.relative_to(dir_path).as_posix()] = f.stat().st_mtime
            except OSError:
                pass
        return result

    async def _poll_loop(self) -> None:
        """后台轮询循环。"""
        while True:
            await asyncio.sleep(self._interval)

            current = self._scan()
            if current == self._mtimes:
                continue

            added = set(current) - set(self._mtimes)
            removed = set(self._mtimes) - set(current)
            changed = {
                k for k in current
                if k in self._mtimes and current[k] != self._mtimes[k]
            }

            if added or removed or changed:
                logger.info(
                    f"[PollingBackend] 检测到变更 — "
                    f"新增: {sorted(added)}, 删除: {sorted(removed)}, 修改: {sorted(changed)}"
                )
                if self._on_change:
                    await self._on_change()

            self._mtimes = current


# ── watchfiles 后端（可选，需 pip install watchfiles） ──────────────────


class WatchfilesBackend:
    """事件驱动后端 — 基于 watchfiles (Rust inotify/ReadDirectoryChanges)。

    零延迟检测文件变更，适合高频变更场景。
    需要: pip install watchfiles
    """

    def __init__(self) -> None:
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task | None = None

    async def start(self, watch_dir: str, on_change: OnChangeCallback) -> None:
        try:
            import watchfiles  # noqa: F401
        except ImportError:
            raise ImportError(
                "watchfiles 未安装。运行: pip install watchfiles\n"
                "或使用默认的 PollingBackend。"
            )

        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._watch_loop(watch_dir, on_change)
        )
        logger.info(f"[WatchfilesBackend] 已启动: {watch_dir}")

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._stop_event = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _watch_loop(self, watch_dir: str, on_change: OnChangeCallback) -> None:
        import watchfiles

        stop = self._stop_event

        async for _changes in watchfiles.awatch(watch_dir, stop_event=stop):
            logger.info("[WatchfilesBackend] 检测到文件变更")
            await on_change()


# ── FileWatcher 门面 ────────────────────────────────────────────────────


class FileWatcher:
    """目录监听门面 — 统一轮询和事件驱动后端。

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
        backend: WatcherBackend | None = None,
    ) -> None:
        self._watch_dir = watch_dir
        self._on_change = on_change
        self._backend = backend or PollingBackend(interval=interval)

    async def start(self) -> None:
        await self._backend.start(self._watch_dir, self._on_change)

    async def stop(self) -> None:
        await self._backend.stop()

    @property
    def is_running(self) -> bool:
        return self._backend.is_running
