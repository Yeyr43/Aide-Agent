"""MCP Lifecycle — 健康检查、文件监听、配置热加载。

从 MCPAdapter 拆分，减少单文件体积。
HealthMonitor + ConfigWatcher 分别管理服务端健康和配置变更。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .watcher import FileWatcher

if TYPE_CHECKING:
    from .adapter import MCPAdapter, MCPServerConfig

logger = logging.getLogger(__name__)

# ── 常量 ───────────────────────────────────────────────────────────────

HEALTH_CHECK_INTERVAL = 30.0   # 定期健康检查间隔（秒）
RECONNECT_DELAY = 2.0          # 重连前等待时间
WATCH_INTERVAL = 5.0           # mcp/ 目录轮询间隔（秒）


# ── 共享工具函数 ──────────────────────────────────────────────────────


def scan_mcp_directory(mcp_dir: str) -> dict[str, dict]:
    """扫描 mcp/ 目录下所有 .json 文件，返回 {name: config_dict}。

    load_builtin_servers 和 reload_config 共用此函数。
    """
    all_configs: dict[str, dict] = {}
    dir_path = Path(mcp_dir)

    if not dir_path.is_dir():
        return all_configs

    for json_file in sorted(dir_path.glob("*.json")):
        try:
            raw = json_file.read_text(encoding="utf-8")
            configs = json.loads(raw)
            if not isinstance(configs, list):
                logger.warning(f"[MCP] {json_file.name} 应为 JSON 数组，跳过")
                continue
            for cfg in configs:
                name = cfg.get("name", "")
                if name:
                    all_configs[name] = cfg
            logger.info(f"[MCP] 加载 {json_file.name}（{len(configs)} 个服务端）")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[MCP] {json_file.name} 读取失败: {e}")

    return all_configs


# ── 健康监控 ──────────────────────────────────────────────────────────


class HealthMonitor:
    """MCP 服务端健康监控 — 定期检查 + 自动重连。

    用法:
        monitor = HealthMonitor(adapter)
        monitor.start()
        ...
        monitor.stop()
    """

    def __init__(self, adapter: MCPAdapter, interval: float = HEALTH_CHECK_INTERVAL) -> None:
        self._adapter = adapter
        self._interval = interval
        self._task: asyncio.Task | None = None

    async def _loop(self) -> None:
        """后台健康检查循环 — 检测不健康服务端并自动重连。"""
        while True:
            await asyncio.sleep(self._interval)
            for name, transport in list(self._adapter._transports.items()):
                if not transport.is_connected:
                    logger.warning(f"[MCP] 服务端不健康，尝试重连: {name}")
                    await self._adapter.reconnect(name)

    def start(self) -> None:
        """启动后台健康检查。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info("[MCP] 健康检查已启动")

    def stop(self) -> None:
        """停止健康检查。"""
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()


# ── 配置监听（文件热加载）────────────────────────────────────────────


class ConfigWatcher:
    """MCP 配置目录监听 — 文件变更时增量重载服务端。

    用法:
        watcher = ConfigWatcher(adapter, mcp_dir="/path/to/mcp")
        watcher.start()
        ...
        watcher.stop()
    """

    def __init__(
        self,
        adapter: MCPAdapter,
        mcp_dir: str,
        interval: float = WATCH_INTERVAL,
    ) -> None:
        self._adapter = adapter
        self._mcp_dir = mcp_dir
        self._interval = interval
        self._watcher: FileWatcher | None = None

    async def reload_config(self) -> tuple[int, int, int]:
        """增量重载 mcp/ 目录配置。

        比完全重启温和：新增的 connect，删除的 disconnect，
        已有的跳过（同名覆盖时重连）。

        Returns:
            (新增连接数, 断开数, 重连数)
        """
        from .adapter import MCPServerConfig

        new_configs = scan_mcp_directory(self._mcp_dir)

        old_names = set(self._adapter._servers.keys())
        new_names = set(new_configs.keys())

        added = 0
        reconnected = 0
        disconnected = 0

        # 移除已消失的
        for name in old_names - new_names:
            self._adapter.remove_server(name)
            disconnected += 1

        # 新增或更新
        for name in new_configs:
            cfg = new_configs[name]
            config = MCPServerConfig(
                name=name,
                command=cfg.get("command", ""),
                args=cfg.get("args", []),
                url=cfg.get("url", ""),
                enabled=cfg.get("enabled", True),
            )

            is_new = name not in old_names
            self._adapter.add_server(config)

            if config.enabled:
                try:
                    await self._adapter.connect(name)
                    if is_new:
                        added += 1
                    else:
                        reconnected += 1
                except Exception as e:
                    logger.warning(f"[MCP] 热加载连接 {name} 失败: {e}")

        logger.info(
            f"[MCP] 热加载完成 — 新增: {added}, 重连: {reconnected}, 断开: {disconnected}"
        )
        return (added, disconnected, reconnected)

    def start(self) -> None:
        """启动文件监听。"""
        if self._watcher is not None:
            self._watcher.stop()

        self._watcher = FileWatcher(
            watch_dir=self._mcp_dir,
            on_change=self.reload_config,
            interval=self._interval,
        )

        async def _start():
            await self._watcher.start()

        asyncio.ensure_future(_start())
        logger.info(f"[MCP] 文件监听已启动: {self._mcp_dir}")

    def stop(self) -> None:
        """停止文件监听。"""
        if self._watcher:
            self._watcher.stop()
            self._watcher = None

    @property
    def is_running(self) -> bool:
        return self._watcher is not None and self._watcher.is_running
