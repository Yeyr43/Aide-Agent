"""PluginWatcher — 插件热重载。

复用 core.watcher 的通用后端（watchfiles 优先 + polling fallback），
保留插件自身的变更语义：检测 plugins/ 目录变更，按变更范围精确重载：
  - SKILL.md 修改 → 重载 skill 上下文
  - hooks.json 修改 → 重新编译 matchers
  - plugin.json 修改 → 全量 reload
  - __init__.py 修改 → Python 模块重载（importlib.reload）
  - 目录新增 → discover + load
  - 目录删除 → unload
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from core.watcher import FileWatcher, PollingBackend, WatchfilesBackend

logger = logging.getLogger(__name__)


class PluginWatcher:
    """插件目录热重载 — 通用后端 + 插件精确重载语义。

    用法:
        watcher = PluginWatcher(plugins_dir, plugin_host)
        await watcher.start()
        # 在后台运行，检测到变更自动重载
    """

    DEBOUNCE_SECONDS = 0.5  # 去抖窗口

    def __init__(self, plugins_dir: Path, host) -> None:
        self._dir = plugins_dir
        self._host = host
        self._watcher: FileWatcher | None = None
        self._pending: dict[str, asyncio.Task] = {}
        self._mtimes: dict[str, float] = {}

    async def start(self) -> None:
        """启动热重载监听。watchfiles 优先，polling fallback。"""
        try:
            import watchfiles  # noqa: F401
            backend: PollingBackend | WatchfilesBackend = WatchfilesBackend()
            mode = "watchfiles"
        except ImportError:
            backend = PollingBackend(interval=2.0)
            mode = "polling"
        self._watcher = FileWatcher(str(self._dir), on_change=self._on_change, backend=backend)
        await self._watcher.start()
        logger.info(f"PluginWatcher: {mode} 模式已启动")

    async def stop(self) -> None:
        """停止监听。"""
        if self._watcher:
            await self._watcher.stop()
            self._watcher = None

    # ── 变更回调（通用后端触发 → 重扫子目录精确调度） ─────────────────

    async def _on_change(self) -> None:
        """共享后端报告"plugins/ 下有变更"→ 重扫各插件目录 mtime 调度重载。

        与后端解耦：不依赖后端报告的路径详情（mcp 轮询/事件后端都不带路径）。
        同时补齐删除检测：快照中消失的目录 → 调度 unload。
        """
        if not self._dir.exists():
            return

        existing: set[str] = set()
        for entry in self._dir.iterdir():
            if not entry.is_dir():
                continue
            existing.add(entry.name)
            current_mtime = self._dir_mtime(entry)
            prev = self._mtimes.get(entry.name, 0)
            if current_mtime > prev:
                await self._schedule_reload(entry)

        # 删除检测：快照里有、磁盘上已消失的目录 → 调度 unload
        for name in list(self._mtimes):
            if name not in existing:
                await self._schedule_reload(self._dir / name)

    @staticmethod
    def _dir_mtime(plugin_dir: Path) -> float:
        """获取插件目录下所有文件的最新 mtime。"""
        max_mtime = 0.0
        try:
            for f in plugin_dir.rglob("*"):
                if f.is_file():
                    try:
                        mtime = f.stat().st_mtime
                        if mtime > max_mtime:
                            max_mtime = mtime
                    except OSError:
                        pass
        except OSError:
            pass
        return max_mtime

    # ── 去抖重载调度 ───────────────────────────────────────────────────

    async def _schedule_reload(self, plugin_dir: Path) -> None:
        """去抖：DEBOUNCE_SECONDS 秒内同一目录仅重载一次。"""
        key = plugin_dir.name
        if key in self._pending:
            self._pending[key].cancel()

        task = asyncio.create_task(self._delayed_reload(plugin_dir))
        self._pending[key] = task

    async def _delayed_reload(self, plugin_dir: Path) -> None:
        """等待去抖窗口后执行重载，并推进 mtime 基线。

        基线在重载**之后**推进，使 _reload_plugin 内的 _detect_changed_files
        以变更前的基线做 mtime 比较，能准确检出"这次变了哪些文件"。
        """
        await asyncio.sleep(self.DEBOUNCE_SECONDS)
        await self._reload_plugin(plugin_dir)
        if plugin_dir.exists():
            self._mtimes[plugin_dir.name] = self._dir_mtime(plugin_dir)
        else:
            self._mtimes.pop(plugin_dir.name, None)

    async def _reload_plugin(self, plugin_dir: Path) -> None:
        """按变更文件范围执行精确重载。"""
        plugin_id = plugin_dir.name

        if not plugin_dir.exists():
            # 目录被删除 → unload
            logger.info(f"PluginWatcher: 检测到删除 {plugin_id}，卸载中")
            await self._host.unload(plugin_id)
            self._mtimes.pop(plugin_id, None)
            return

        loaded = self._host.is_loaded(plugin_id)
        if not loaded:
            # 新目录 → discover + load
            logger.info(f"PluginWatcher: 检测到新插件 {plugin_id}，加载中")
            await self._host.load(plugin_id)
            return

        # 已加载 → 根据变更文件类型决定重载范围
        changed = self._detect_changed_files(plugin_dir)
        if "plugin.json" in changed or ".claude-plugin" in changed:
            # manifest 变更 → 全量 reload
            logger.info(f"PluginWatcher: {plugin_id} manifest 变更，全量重载")
            await self._host.reload(plugin_id)
        elif "SKILL.md" in changed or "hooks.json" in changed:
            # skill/hook 变更 → reload
            logger.info(f"PluginWatcher: {plugin_id} skill/hook 变更，重载")
            await self._host.reload(plugin_id)
        elif "__init__.py" in changed or "main.py" in changed:
            # Python 入口变更 → reload（importlib.reload 有限制）
            logger.info(f"PluginWatcher: {plugin_id} Python 入口变更，重载")
            await self._host.reload(plugin_id)

    def _detect_changed_files(self, plugin_dir: Path) -> set[str]:
        """检测变更的文件类型标签。"""
        changed: set[str] = set()
        # 简化：检查关键文件的 mtime
        for check in [
            (".claude-plugin/plugin.json", ".claude-plugin"),
            ("plugin.json", "plugin.json"),
            ("SKILL.md", "SKILL.md"),
            ("hooks/hooks.json", "hooks.json"),
            ("__init__.py", "__init__.py"),
            ("main.py", "main.py"),
        ]:
            path = plugin_dir / check[0]
            if path.exists():
                try:
                    if path.stat().st_mtime > self._mtimes.get(plugin_dir.name, 0):
                        changed.add(check[1])
                except OSError:
                    pass
        return changed
