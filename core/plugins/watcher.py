"""PluginWatcher — 插件热重载。

watchfiles 优先 + polling fallback。
检测 plugins/ 目录变更，按变更范围精确重载：
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

logger = logging.getLogger(__name__)


class PluginWatcher:
    """插件目录热重载。

    对标 MCP watcher.py 的 FileWatcher 模式。

    用法:
        watcher = PluginWatcher(plugins_dir, plugin_host)
        await watcher.start()
        # 在后台运行，检测到变更自动重载
    """

    DEBOUNCE_SECONDS = 0.5  # 去抖窗口

    def __init__(self, plugins_dir: Path, host) -> None:
        self._dir = plugins_dir
        self._host = host
        self._task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Task] = {}
        self._mtimes: dict[str, float] = {}

    async def start(self) -> None:
        """启动热重载监听。watchfiles 优先，polling fallback。"""
        try:
            from watchfiles import awatch
            self._task = asyncio.create_task(self._watchfiles_loop(awatch))
            logger.info("PluginWatcher: watchfiles 模式已启动")
        except ImportError:
            logger.info("PluginWatcher: watchfiles 未安装，使用 polling 模式")
            self._task = asyncio.create_task(self._polling_loop())

    async def stop(self) -> None:
        """停止监听。"""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ── watchfiles 模式 ────────────────────────────────────────────────

    async def _watchfiles_loop(self, awatch) -> None:
        """使用 watchfiles.awatch 监听文件变更。"""
        try:
            async for changes in awatch(str(self._dir)):
                changed_dirs: set[Path] = set()
                for change_type, path_str in changes:
                    path = Path(path_str)
                    # 找变更所属的插件目录
                    try:
                        rel = path.relative_to(self._dir)
                        plugin_dir = self._dir / rel.parts[0]
                        changed_dirs.add(plugin_dir)
                    except ValueError:
                        continue

                for plugin_dir in changed_dirs:
                    await self._schedule_reload(plugin_dir)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("watchfiles 循环异常，切换到 polling", exc_info=True)
            self._task = asyncio.create_task(self._polling_loop())

    # ── Polling 模式 ───────────────────────────────────────────────────

    async def _polling_loop(self) -> None:
        """每 2 秒检查 mtime 变更。"""
        while True:
            try:
                await asyncio.sleep(2)
                if not self._dir.exists():
                    continue

                for entry in self._dir.iterdir():
                    if not entry.is_dir():
                        continue
                    current_mtime = self._dir_mtime(entry)
                    prev = self._mtimes.get(entry.name, 0)
                    if current_mtime > prev:
                        self._mtimes[entry.name] = current_mtime
                        await self._schedule_reload(entry)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("PluginWatcher polling 异常", exc_info=True)

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
        """等待去抖窗口后执行重载。"""
        await asyncio.sleep(self.DEBOUNCE_SECONDS)
        await self._reload_plugin(plugin_dir)

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
