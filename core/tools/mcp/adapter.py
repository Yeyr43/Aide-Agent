"""MCP Adapter — 将 MCP (Model Context Protocol) 工具映射为 Aide ToolDefinition。

P4 Batch 2: 完整实现 — stdio + HTTP transport、工具发现、工具执行、
健康检查、自动重连、mcp/ 目录热加载。

生命周期管理（健康检查/文件监听/配置热加载）已拆至 lifecycle.py。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from core.tools import ToolDefinition
from core.locale import t
from .fault import CircuitBreaker
from .lifecycle import (
    HealthMonitor,
    ConfigWatcher,
    scan_mcp_directory,
    HEALTH_CHECK_INTERVAL,
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
        self._servers: dict[str, MCPServerConfig] = {}
        self._transports: dict[str, StdioTransport | HTTPTransport] = {}
        self._tool_cache: dict[str, list[ToolDefinition]] = {}
        # aide_tool_name → (server_name, original_tool_name) 映射
        # 用于 execute_aide_tool 可靠反查，避免 split("_", 2) 歧义
        self._tool_mapping: dict[str, tuple[str, str]] = {}
        # 熔断器
        self._breaker = CircuitBreaker(threshold=3)
        # 健康监控（延迟初始化，需要 self 引用）
        self._health = HealthMonitor(self)
        # 配置监听（延迟初始化，需要 mcp_dir）
        self._watcher: ConfigWatcher | None = None
        self._mcp_dir: str = ""

    # ── 服务端管理 ──────────────────────────────────────────────────

    def add_server(self, config: MCPServerConfig) -> None:
        """注册一个 MCP 服务端配置（不立即连接）。"""
        self._servers[config.name] = config

    def remove_server(self, name: str) -> bool:
        """移除一个 MCP 服务端配置。若已连接则先断开。"""
        if name not in self._servers:
            return False
        if name in self._transports:
            try:
                asyncio.ensure_future(self.disconnect(name))
            except RuntimeError:
                pass
        self._servers.pop(name, None)
        self._tool_cache.pop(name, None)
        # 清理 tool mapping（移除该服务端的所有映射）
        for aide_name in list(self._tool_mapping):
            if self._tool_mapping[aide_name][0] == name:
                del self._tool_mapping[aide_name]
        return True

    def list_servers(self) -> list[MCPServerConfig]:
        """列出所有已注册的服务端。"""
        return list(self._servers.values())

    def get_server_status(self, name: str) -> MCPServerStatus:
        """获取服务端运行状态。"""
        config = self._servers.get(name)
        transport = self._transports.get(name)
        tools = self._tool_cache.get(name, [])

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
        # 健康 = 已连接 + 缓存中有工具（说明 discover 成功过）
        healthy = connected and len(tools) > 0

        return MCPServerStatus(
            name=name,
            transport=t_type,
            connected=connected,
            enabled=config.enabled if config else False,
            tool_count=len(tools),
            healthy=healthy,
            circuit_tripped=self._breaker.is_tripped(name),
        )

    def get_all_status(self) -> list[MCPServerStatus]:
        """获取所有已注册服务端的状态。"""
        return [self.get_server_status(name) for name in self._servers]

    # 熔断器方法委托给 CircuitBreaker 实例，
    # 调用者直接用 self._breaker.on_success / on_failure / is_tripped / reset

    async def connect(self, name: str) -> None:
        """连接到指定 MCP 服务端。"""
        if name not in self._servers:
            raise KeyError(f"MCP 服务端未注册: {name}")

        config = self._servers[name]
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
        self._breaker.reset(name)  # 重连时重置熔断器
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

    async def disconnect_all(self) -> None:
        """断开所有 MCP 连接。"""
        for name in list(self._transports.keys()):
            await self.disconnect(name)

    # ── 健康检查 + 自动重连 ────────────────────────────────────────

    async def check_health(self, name: str) -> bool:
        """检查服务端是否健康（连接正常 + 进程存活）。"""
        transport = self._transports.get(name)
        if transport is None:
            return False
        return transport.is_connected

    async def reconnect(self, name: str) -> bool:
        """尝试重连服务端。成功返回 True。"""
        config = self._servers.get(name)
        if config is None or not config.enabled:
            return False

        # 先断开旧的
        try:
            await self.disconnect(name)
        except Exception:
            pass

        await asyncio.sleep(RECONNECT_DELAY)
        try:
            await self.connect(name)
            # 重连成功后刷新工具
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
        """启动 mcp/ 目录文件监听。

        Args:
            mcp_dir: 要监听的目录，默认 ~/.aide/mcp/
        """
        if mcp_dir is None:
            from core.setup import aide_dir
            mcp_dir = str(aide_dir() / "mcp")

        self._mcp_dir = mcp_dir
        self._watcher = ConfigWatcher(self, mcp_dir)
        self._watcher.start()

    def stop_watcher(self) -> None:
        """停止文件监听。"""
        if self._watcher:
            self._watcher.stop()
            self._watcher = None

    # ── 工具发现 ────────────────────────────────────────────────────

    async def discover_tools(self, name: str) -> list[ToolDefinition]:
        """从 MCP 服务端发现工具，映射为 Aide ToolDefinition。"""
        if name in self._tool_cache:
            return self._tool_cache[name]

        if name not in self._transports:
            logger.warning(f"[MCP] 服务端未连接: {name}")
            return []

        try:
            transport = self._transports[name]
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
            # 记录映射，用于 execute_aide_tool 可靠反查
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
        # 熔断检查
        if self._breaker.is_tripped(server_name):
            return (
                f"错误：MCP 服务端 {server_name} 已熔断（连续 {self._breaker.threshold} 次失败）。"
                f"\n使用 /mcp connect {server_name} 重置熔断器。"
            )

        if server_name not in self._transports:
            self._breaker.on_failure(server_name)
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

        self._breaker.on_success(server_name)

        result = response.result
        content = result.get("content", []) if isinstance(result, dict) else []

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

    async def execute_aide_tool(
        self,
        aide_tool_name: str,
        arguments: dict,
    ) -> str | None:
        """执行以 'mcp_' 为前缀的 Aide 工具。"""
        if not aide_tool_name.startswith("mcp_"):
            return None

        mapping = self._tool_mapping.get(aide_tool_name)
        if mapping is not None:
            server_name, tool_name = mapping
        else:
            # fallback: 从工具名解析（兼容未经过 discover 的工具名）
            parts = aide_tool_name.split("_", 2)
            if len(parts) < 3:
                return t("mcp.invalid_tool_name", name=aide_tool_name)
            server_name = parts[1]
            tool_name = parts[2]
        return await self.call_tool(server_name, tool_name, arguments)

    # ── 全部工具汇总 ────────────────────────────────────────────────

    async def discover_all_tools(self) -> list[ToolDefinition]:
        """从所有已连接服务端发现工具，汇总返回。

        每个工具绑定 execute 函数，可直接用于 ToolRegistry。
        """
        all_tools: list[ToolDefinition] = []

        for name in list(self._transports.keys()):
            tools = await self.discover_tools(name)
            for tool in tools:
                # 用工厂函数正确捕获闭包变量
                server_name = name
                mapping = self._tool_mapping.get(tool.name, (name, tool.name))
                original_name = mapping[1]

                def _make_execute(s: str, t: str):
                    async def _execute(args: dict, _s=s, _t=t) -> str:
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

    async def load_builtin_servers(self, mcp_dir: str | None = None) -> int:
        """扫描 mcp/ 目录下所有 .json 文件，加载 MCP 服务端并连接已启用的。"""
        if mcp_dir is None:
            from core.setup import aide_dir
            mcp_dir = str(aide_dir() / "mcp")

        all_configs = scan_mcp_directory(mcp_dir)
        if not all_configs:
            logger.debug(f"[MCP] 目录不存在或为空: {mcp_dir}")
            return 0

        connected = 0
        for name, cfg_dict in all_configs.items():
            config = MCPServerConfig(
                name=name,
                command=cfg_dict.get("command", ""),
                args=cfg_dict.get("args", []),
                url=cfg_dict.get("url", ""),
                enabled=cfg_dict.get("enabled", True),
            )
            self.add_server(config)

            if config.enabled:
                try:
                    await self.connect(name)
                    connected += 1
                except Exception as e:
                    logger.warning(f"[MCP] 连接 {name} 失败: {e}")

        return connected
