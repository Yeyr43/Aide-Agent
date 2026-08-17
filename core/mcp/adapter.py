"""MCP Adapter — 将 MCP (Model Context Protocol) 工具映射为 Aide ToolDefinition。

P4 Batch 2: 完整实现 — stdio + HTTP transport、工具发现、工具执行、
健康检查、自动重连、mcp/ 目录热加载。

生命周期管理（健康检查/文件监听/配置热加载）已拆至 lifecycle.py。
服务端注册表（配置 CRUD + 状态查询 + 批量加载）已拆至 ServerRegistry。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from core.tools import ToolDefinition
from core.locale import t
from .fault import CircuitBreaker
from .lifecycle import (
    HealthMonitor,
    ConfigWatcher,
    scan_mcp_directory,
    RECONNECT_DELAY,
)
from .protocol import make_tools_list_request, make_tools_call_request
from .transport import (
    StdioTransport,
    HTTPTransport,
    create_transport,
    CALL_TIMEOUT,
)

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """MCP 服务端连接配置。

    stdio transport: 提供 command + args
    HTTP transport: 提供 url
    """
    name: str
    command: str = ""        # stdio: 可执行文件路径
    args: list[str] = field(default_factory=list)   # stdio: 命令行参数
    url: str = ""            # HTTP: 服务端 URL
    enabled: bool = True     # 是否启用


@dataclass
class MCPServerStatus:
    """MCP 服务端运行时状态。"""
    name: str
    transport: str           # "stdio" | "http" | "none"
    connected: bool
    enabled: bool
    tool_count: int
    healthy: bool
    circuit_tripped: bool = False   # 熔断器是否已触发


class ServerRegistry:
    """MCP 服务端注册表 — 配置 CRUD + 状态查询 + 批量加载。

    从 MCPAdapter 提取，减少门面体积。不持有 transport/breaker 引用，
    状态查询所需的外部数据通过方法参数传入。
    """

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerConfig] = {}

    # ── CRUD ──────────────────────────────────────────────────────

    def add(self, config: MCPServerConfig) -> None:
        """注册一个 MCP 服务端配置（不立即连接）。"""
        self._servers[config.name] = config

    def remove(self, name: str) -> bool:
        """移除一个 MCP 服务端配置。"""
        if name not in self._servers:
            return False
        self._servers.pop(name, None)
        return True

    def list_all(self) -> list[MCPServerConfig]:
        """列出所有已注册的服务端。"""
        return list(self._servers.values())

    def get(self, name: str) -> MCPServerConfig | None:
        """获取单个服务端配置。"""
        return self._servers.get(name)

    @property
    def names(self) -> set[str]:
        """返回所有已注册服务端名称。"""
        return set(self._servers.keys())

    # ── 状态查询 ─────────────────────────────────────────────────

    def get_status(
        self,
        name: str,
        *,
        transport: StdioTransport | HTTPTransport | None = None,
        tool_count: int = 0,
        circuit_tripped: bool = False,
    ) -> MCPServerStatus:
        """获取服务端运行时状态。

        Args:
            name: 服务端名称
            transport: 当前 transport 实例（None = 未连接）
            tool_count: 已缓存工具数
            circuit_tripped: 熔断器状态
        """
        config = self._servers.get(name)

        if transport is None:
            return MCPServerStatus(
                name=name,
                transport="none",
                connected=False,
                enabled=config.enabled if config else False,
                tool_count=0,
                healthy=False,
            )

        t_type = "stdio" if isinstance(transport, StdioTransport) else "http"
        connected = transport.is_connected
        healthy = connected and tool_count > 0

        return MCPServerStatus(
            name=name,
            transport=t_type,
            connected=connected,
            enabled=config.enabled if config else False,
            tool_count=tool_count,
            healthy=healthy,
            circuit_tripped=circuit_tripped,
        )

    def get_all_status(
        self,
        transports: dict[str, StdioTransport | HTTPTransport],
        tool_cache: dict[str, list[ToolDefinition]],
        breaker: CircuitBreaker,
    ) -> list[MCPServerStatus]:
        """获取所有已注册服务端的状态。"""
        return [
            self.get_status(
                name,
                transport=transports.get(name),
                tool_count=len(tool_cache.get(name, [])),
                circuit_tripped=breaker.is_tripped(name),
            )
            for name in self._servers
        ]

    # ── 批量加载 ─────────────────────────────────────────────────

    async def load_from_directory(
        self,
        mcp_dir: str,
        connect_fn,  # async (name: str) -> None
    ) -> tuple[int, list[str]]:
        """扫描目录下 .json 文件，注册并连接已启用的服务端。

        Args:
            mcp_dir: ~/.aide/mcp/ 目录路径
            connect_fn: 连接回调 async def connect(name) -> None

        Returns:
            (connected_count, failed_names)
        """
        all_configs = scan_mcp_directory(mcp_dir)
        if not all_configs:
            logger.debug(f"[MCP] 目录不存在或为空: {mcp_dir}")
            return (0, [])

        connected = 0
        failed: list[str] = []
        for name, cfg_dict in all_configs.items():
            config = MCPServerConfig(
                name=name,
                command=cfg_dict.get("command", ""),
                args=cfg_dict.get("args", []),
                url=cfg_dict.get("url", ""),
                enabled=cfg_dict.get("enabled", True),
            )
            self.add(config)

            if config.enabled:
                try:
                    await connect_fn(name)
                    connected += 1
                except Exception as e:
                    logger.warning(f"[MCP] 连接 {name} 失败: {e}")
                    failed.append(name)

        return (connected, failed)


class MCPAdapter:
    """MCP → Aide 工具适配器。

    管理多个 MCP 服务端连接，将远程工具映射为 Aide ToolDefinition。
    支持热插拔、健康检查、自动重连、文件监听。

    用法:
        adapter = MCPAdapter()
        await adapter.load_builtin_servers()
        tools = await adapter.discover_all_tools()
        adapter.start_watcher()
    """

    def __init__(self) -> None:
        self._registry = ServerRegistry()
        self._transports: dict[str, StdioTransport | HTTPTransport] = {}
        self._tool_cache: dict[str, list[ToolDefinition]] = {}
        # aide_tool_name → (server_name, original_tool_name) 映射
        # 供 discover_all_tools 绑定 execute 时可靠反查，避免 split("_", 2) 歧义
        self._tool_mapping: dict[str, tuple[str, str]] = {}
        # 熔断器
        self._breaker = CircuitBreaker(threshold=3)
        # 健康监控
        self._health = HealthMonitor(self)
        # 配置监听（延迟初始化，需要 mcp_dir）
        self._watcher: ConfigWatcher | None = None
        self._mcp_dir: str = ""
        # ToolRegistry 引用（由 AppBootstrap 注入，用于热加载同步工具）
        self._tool_registry: Any = None

    # ── 服务端管理（委托给 ServerRegistry） ────────────────────────────

    def add_server(self, config: MCPServerConfig) -> None:
        """注册一个 MCP 服务端配置（不立即连接）。"""
        self._registry.add(config)

    def remove_server(self, name: str) -> bool:
        """移除一个 MCP 服务端配置。若已连接则先断开。"""
        if not self._registry.remove(name):
            return False
        if name in self._transports:
            try:
                asyncio.ensure_future(self.disconnect(name))
            except RuntimeError:
                pass
        self._tool_cache.pop(name, None)
        # 清理 tool mapping
        for aide_name in list(self._tool_mapping):
            if self._tool_mapping[aide_name][0] == name:
                del self._tool_mapping[aide_name]
        return True

    def list_servers(self) -> list[MCPServerConfig]:
        """列出所有已注册的服务端。"""
        return self._registry.list_all()

    def get_server_status(self, name: str) -> MCPServerStatus:
        """获取服务端运行状态。"""
        return self._registry.get_status(
            name,
            transport=self._transports.get(name),
            tool_count=len(self._tool_cache.get(name, [])),
            circuit_tripped=self._breaker.is_tripped(name),
        )

    def get_all_status(self) -> list[MCPServerStatus]:
        """获取所有已注册服务端的状态。"""
        return self._registry.get_all_status(
            self._transports, self._tool_cache, self._breaker,
        )

    # ── 连接管理 ────────────────────────────────────────────────────

    async def connect(self, name: str) -> None:
        """连接到指定 MCP 服务端。"""
        config = self._registry.get(name)
        if config is None:
            raise KeyError(f"MCP 服务端未注册: {name}")
        if not config.enabled:
            raise RuntimeError(f"MCP 服务端已禁用: {name}")

        if name in self._transports:
            await self.disconnect(name)

        transport = await create_transport(
            command=config.command,
            args=config.args,
            url=config.url,
        )
        self._transports[name] = transport
        self._tool_cache.pop(name, None)
        self._breaker.reset(name)
        logger.info(f"[MCP] 已连接服务端: {name}")

    async def disconnect(self, name: str) -> None:
        """断开指定 MCP 服务端连接。"""
        transport = self._transports.pop(name, None)
        self._tool_cache.pop(name, None)
        if transport:
            try:
                await transport.disconnect()
            except Exception:
                logger.exception(f"[MCP] 断开 {name} 时出错")
        logger.info(f"[MCP] 已断开服务端: {name}")

    # ── 自动重连 ─────────────────────────────────────────────────

    async def reconnect(self, name: str) -> bool:
        """尝试重连服务端。成功返回 True。"""
        config = self._registry.get(name)
        if config is None or not config.enabled:
            return False

        try:
            await self.disconnect(name)
        except Exception:
            pass

        await asyncio.sleep(RECONNECT_DELAY)
        try:
            await self.connect(name)
            await self.refresh_tools(name)
            return True
        except Exception as e:
            logger.warning(f"[MCP] 重连 {name} 失败: {e}")
            return False

    def start_health_check(self) -> None:
        """启动后台健康检查。委托给 HealthMonitor。"""
        self._health.start()

    def stop_health_check(self) -> None:
        """停止健康检查。委托给 HealthMonitor。"""
        self._health.stop()

    # ── 文件监听（mcp/ 目录热加载） ────────────────────────────────

    async def reload_config(self) -> tuple[int, int, int]:
        """增量重载 mcp/ 目录配置。委托给 ConfigWatcher。"""
        if self._watcher is None:
            return (0, 0, 0)
        return await self._watcher.reload_config()

    def start_watcher(self, mcp_dir: str | None = None) -> None:
        """启动 mcp/ 目录文件监听。"""
        if mcp_dir is None:
            from core.setup import aide_dir
            mcp_dir = str(aide_dir() / "mcp")
        self._mcp_dir = mcp_dir
        self._watcher = ConfigWatcher(self, mcp_dir)
        self._watcher.start()

    async def stop_watcher(self) -> None:
        """停止文件监听。"""
        if self._watcher:
            await self._watcher.stop()
            self._watcher = None

    # ── ToolRegistry 同步 ─────────────────────────────────────────

    def set_tool_registry(self, registry: Any) -> None:
        """注入 ToolRegistry 引用，用于热加载时自动同步 MCP 工具。"""
        self._tool_registry = registry

    async def _sync_tools_to_registry(self) -> int:
        """将当前已连接服务端的工具同步到 ToolRegistry。

        先发现新工具，再原子替换旧的 mcp_* 工具（先删后注册），
        最小化工具缺失窗口。
        """
        if self._tool_registry is None:
            return 0

        # 先发现当前工具（网络调用在清除之前完成）
        all_tools = await self.discover_all_tools()

        # 原子替换：清除旧 → 注册新（窗口仅在内存操作之间，毫秒级）
        for name in list(self._tool_registry.list_names()):
            if name.startswith("mcp_"):
                self._tool_registry.unregister(name)

        for tool in all_tools:
            self._tool_registry.register(tool)

        if all_tools:
            logger.info(f"[MCP] 已同步 {len(all_tools)} 个工具到 ToolRegistry")
        return len(all_tools)

    # ── 工具发现 ────────────────────────────────────────────────────

    async def discover_tools(self, name: str) -> list[ToolDefinition]:
        """从 MCP 服务端发现工具，映射为 Aide ToolDefinition。"""
        if name in self._tool_cache:
            return self._tool_cache[name]

        transport = self._transports.get(name)
        if transport is None:
            logger.warning(f"[MCP] 服务端未连接: {name}")
            return []

        try:
            response = await transport.send_request(make_tools_list_request())
        except Exception as e:
            logger.error(f"[MCP] 工具发现失败 ({name}): {e}")
            return []

        if response.is_error:
            logger.error(f"[MCP] tools/list 返回错误 ({name}): {response.error_message}")
            return []

        raw_tools: list[dict] = response.result.get("tools", [])
        tools: list[ToolDefinition] = []
        server_prefix = name.replace("-", "_")

        for rt in raw_tools:
            tool_name = rt.get("name", "unknown")
            aide_name = f"mcp_{server_prefix}_{tool_name}"
            self._tool_mapping[aide_name] = (name, tool_name)

            params = rt.get("inputSchema", {})
            if not isinstance(params, dict):
                params = {"type": "object", "properties": {}}
            if "type" not in params:
                params = {"type": "object", "properties": params}

            aide_tool = ToolDefinition(
                name=aide_name,
                description=f"[MCP:{name}] {rt.get('description', tool_name)}",
                parameters=params,
                execute=None,  # discover_all_tools 中绑定
            )
            tools.append(aide_tool)

        self._tool_cache[name] = tools
        logger.info(f"[MCP] 从 {name} 发现 {len(tools)} 个工具")
        return tools

    async def refresh_tools(self, name: str) -> list[ToolDefinition]:
        """强制刷新工具列表。"""
        self._tool_cache.pop(name, None)
        return await self.discover_tools(name)

    # ── 工具执行 ────────────────────────────────────────────────────

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict,
        timeout: float = CALL_TIMEOUT,
    ) -> str:
        """执行 MCP 工具。

        - 熔断器已触发 → 直接返回错误，不实际调用
        - 进程已死 → 自动重连一次
        - 连续失败 3 次 → 熔断，后续全部跳过
        """
        if self._breaker.is_tripped(server_name):
            return (
                f"错误：MCP 服务端 {server_name} 已熔断（连续 {self._breaker.threshold} 次失败）。"
                f"\n使用 /mcp connect {server_name} 重置熔断器。"
            )

        if server_name not in self._transports:
            # 配置/状态错误，不触发熔断（熔断只计实际调用失败）
            return t("mcp.not_connected", server=server_name)

        transport = self._transports[server_name]

        try:
            request = make_tools_call_request(tool_name, arguments)
            response = await transport.send_request(request, timeout=timeout)
        except (RuntimeError, ConnectionError, BrokenPipeError) as e:
            logger.warning(f"[MCP] 工具调用失败，尝试重连 {server_name}: {e}")
            if await self.reconnect(server_name):
                try:
                    transport = self._transports[server_name]
                    request = make_tools_call_request(tool_name, arguments)
                    response = await transport.send_request(request, timeout=timeout)
                    self._breaker.on_success(server_name)
                except Exception as e2:
                    self._breaker.on_failure(server_name)
                    return t("mcp.reconnect_failed", e=e2)
            else:
                self._breaker.on_failure(server_name)
                return t("mcp.disconnected_reconnect_failed", server=server_name)
        except asyncio.TimeoutError:
            self._breaker.on_failure(server_name)
            return t("mcp.timeout", tool=tool_name, timeout=timeout)
        except Exception as e:
            self._breaker.on_failure(server_name)
            return t("mcp.call_failed", e=e)

        if response.is_error:
            self._breaker.on_failure(server_name)
            return t("mcp.error_response", msg=response.error_message)

        # 传输层成功（熔断不计业务错误——服务端还活着，只是工具执行失败）
        self._breaker.on_success(server_name)

        result = response.result
        content = result.get("content", []) if isinstance(result, dict) else []

        # MCP CallToolResult 工具级失败：JSON-RPC 层成功但 result.isError=true
        # （如 filesystem 服务端找不到文件）。不能把失败内容当成功结果喂给 LLM。
        if isinstance(result, dict) and result.get("isError"):
            err_text = ""
            if isinstance(content, str):
                err_text = content
            elif isinstance(content, list):
                err_text = "\n".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("text")
                )
            return t("mcp.tool_error", tool=tool_name, msg=err_text or "(无错误信息)")

        if isinstance(content, str):
            return content

        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "text")
                    if block_type == "text":
                        texts.append(block.get("text", ""))
                    elif block_type == "resource":
                        texts.append(f"[Resource: {block.get('resource', {})}]")
                    else:
                        texts.append(json.dumps(block, ensure_ascii=False))
                elif isinstance(block, str):
                    texts.append(block)
            return "\n".join(texts) if texts else "(空结果)"

        return json.dumps(result, ensure_ascii=False, indent=2)

    # ── 全部工具汇总 ────────────────────────────────────────────────

    async def discover_all_tools(self) -> list[ToolDefinition]:
        """从所有已连接服务端发现工具，汇总并绑定 execute 函数。"""
        all_tools: list[ToolDefinition] = []

        for name in list(self._transports.keys()):
            tools = await self.discover_tools(name)
            for tool in tools:
                server_name = name
                mapping = self._tool_mapping.get(tool.name, (name, tool.name))
                original_name = mapping[1]

                def _make_execute(server: str, tool: str):
                    async def _execute(args: dict, _s=server, _t=tool) -> str:
                        return await self.call_tool(_s, _t, args)
                    return _execute

                tool.execute = _make_execute(server_name, original_name)
            all_tools.extend(tools)

        return all_tools

    @property
    def connected_servers(self) -> list[str]:
        """返回已连接的服务端名称列表。"""
        return list(self._transports.keys())

    # ── 内置服务器加载 ──────────────────────────────────────────────

    async def load_builtin_servers(self, mcp_dir: str | None = None) -> tuple[int, list[str]]:
        """扫描 mcp/ 目录下所有 .json 文件，加载 MCP 服务端并连接已启用的。

        Returns:
            (connected_count, failed_names) — 调用方可根据 failed_names 报告用户
        """
        if mcp_dir is None:
            from core.setup import aide_dir
            mcp_dir = str(aide_dir() / "mcp")

        return await self._registry.load_from_directory(mcp_dir, self.connect)
