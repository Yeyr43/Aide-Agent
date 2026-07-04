"""MCP tools — MCP 协议适配与工具发现。

P4 Batch 2: 完整实现 — stdio + HTTP transport、工具发现、工具执行、
健康检查、自动重连、文件监听热加载。

生命周期管理（健康检查/配置热加载）在 lifecycle.py 中。
"""

from .adapter import MCPAdapter, MCPServerConfig, MCPServerStatus
from .transport import StdioTransport, HTTPTransport, create_transport
from .lifecycle import HealthMonitor, ConfigWatcher, scan_mcp_directory

__all__ = [
    "MCPAdapter",
    "MCPServerConfig",
    "MCPServerStatus",
    "StdioTransport",
    "HTTPTransport",
    "create_transport",
    "HealthMonitor",
    "ConfigWatcher",
    "scan_mcp_directory",
]
