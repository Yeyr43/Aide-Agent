"""MCP JSON-RPC 2.0 协议类型和常量。

MCP (Model Context Protocol) 基于 JSON-RPC 2.0，定义了：
  - initialize/initialized 握手
  - tools/list 工具发现
  - tools/call 工具执行

Ref: https://spec.modelcontextprotocol.io/specification/
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# ── 协议常量 ─────────────────────────────────────────────────────────

MCP_VERSION = "2024-11-05"
JSONRPC_VERSION = "2.0"

# 客户端能力声明
CLIENT_CAPABILITIES = {
    "roots": {"listChanged": True},
    "sampling": {},
}

# ── JSON-RPC 2.0 消息类型 ────────────────────────────────────────────


@dataclass
class JSONRPCRequest:
    """JSON-RPC 2.0 请求。"""
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    jsonrpc: str = JSONRPC_VERSION
    id: int = 0

    def to_json(self) -> str:
        return json.dumps({
            "jsonrpc": self.jsonrpc,
            "id": self.id,
            "method": self.method,
            "params": self.params,
        }, ensure_ascii=False)


@dataclass
class JSONRPCNotification:
    """JSON-RPC 2.0 通知（无 id，无响应）。"""
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    jsonrpc: str = JSONRPC_VERSION

    def to_json(self) -> str:
        return json.dumps({
            "jsonrpc": self.jsonrpc,
            "method": self.method,
            "params": self.params,
        }, ensure_ascii=False)


@dataclass
class JSONRPCResponse:
    """JSON-RPC 2.0 响应（解析后的）。"""
    id: int
    result: Any = None
    error: dict[str, Any] | None = None

    @property
    def is_error(self) -> bool:
        return self.error is not None

    @property
    def error_message(self) -> str:
        if self.error:
            return self.error.get("message", str(self.error))
        return ""


def parse_response(data: str) -> JSONRPCResponse:
    """从 JSON 字符串解析 JSON-RPC 响应。"""
    obj = json.loads(data)
    return JSONRPCResponse(
        id=obj.get("id", 0),
        result=obj.get("result"),
        error=obj.get("error"),
    )


# ── MCP 握手方法 ────────────────────────────────────────────────────


def make_initialize_request(req_id: int = 1) -> JSONRPCRequest:
    """构建 initialize 请求。"""
    return JSONRPCRequest(
        method="initialize",
        params={
            "protocolVersion": MCP_VERSION,
            "capabilities": CLIENT_CAPABILITIES,
            "clientInfo": {
                "name": "AideAgent",
                "version": "0.1.0",
            },
        },
        id=req_id,
    )


def make_initialized_notification() -> JSONRPCNotification:
    """构建 initialized 通知。"""
    return JSONRPCNotification(method="notifications/initialized")


def make_tools_list_request(req_id: int = 2) -> JSONRPCRequest:
    """构建 tools/list 请求。"""
    return JSONRPCRequest(method="tools/list", id=req_id)


def make_tools_call_request(
    tool_name: str,
    arguments: dict[str, Any],
    req_id: int = 3,
) -> JSONRPCRequest:
    """构建 tools/call 请求。"""
    return JSONRPCRequest(
        method="tools/call",
        params={"name": tool_name, "arguments": arguments},
        id=req_id,
    )
