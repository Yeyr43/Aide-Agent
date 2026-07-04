"""MCP Transport 实现 — stdio + HTTP。

StdioTransport: 通过子进程 stdin/stdout 通信，每行一个 JSON-RPC 消息。
HTTPTransport: 通过 HTTP POST + SSE 通信（Streamable HTTP Transport）。

两种 transport 都实现了 MCPTransport Protocol。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from .protocol import (
    JSONRPCRequest,
    JSONRPCNotification,
    JSONRPCResponse,
    parse_response,
    make_initialize_request,
    make_initialized_notification,
)

logger = logging.getLogger(__name__)

# ── 超时配置 ─────────────────────────────────────────────────────────

INIT_TIMEOUT = 30.0      # initialize 握手超时
REQUEST_TIMEOUT = 60.0   # 普通请求超时
CALL_TIMEOUT = 120.0     # tools/call 执行超时（可能很长）


# ── Stdio Transport ──────────────────────────────────────────────────


class StdioTransport:
    """MCP stdio transport — 子进程 + JSON-RPC 行协议。

    用法:
        transport = StdioTransport()
        await transport.connect(MCPServerConfig(name="filesystem", command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/path"]))
        tools = await transport.send_request(make_tools_list_request())
        result = await transport.send_request(make_tools_call_request("read_file", {"path": "/tmp/x"}))
        await transport.disconnect()
    """

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._connected = False

    # ── 连接管理 ──────────────────────────────────────────────────

    async def connect(self, command: str, args: list[str] | None = None) -> None:
        """启动 MCP 服务端子进程并完成 initialize 握手。

        Args:
            command: 可执行文件路径或命令名
            args: 命令行参数列表

        Raises:
            FileNotFoundError: 找不到命令
            RuntimeError: 连接或握手失败
        """
        if self._connected:
            await self.disconnect()

        args = args or []

        try:
            self._proc = await asyncio.create_subprocess_exec(
                command,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise FileNotFoundError(f"MCP 服务端命令未找到: {command}")
        except Exception as e:
            raise RuntimeError(f"启动 MCP 子进程失败: {e}") from e

        # 启动 stdout 读取循环
        self._reader_task = asyncio.create_task(self._read_loop())

        # initialize 握手
        try:
            response = await self.send_request(
                make_initialize_request(),
                timeout=INIT_TIMEOUT,
            )
        except Exception:
            await self._cleanup()
            raise

        if response.is_error:
            await self._cleanup()
            raise RuntimeError(
                f"MCP initialize 失败: {response.error_message}"
            )

        # 发送 initialized 通知
        notification = make_initialized_notification()
        self._send_line(notification.to_json())

        self._connected = True
        logger.info(f"[MCP stdio] 已连接: {command} {' '.join(args)}")

    async def disconnect(self) -> None:
        """断开连接，终止子进程。"""
        self._connected = False
        await self._cleanup()

    async def _cleanup(self) -> None:
        """清理子进程和 reader task。"""
        # 取消 reader
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        # 终止子进程
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass

        self._proc = None
        self._reader_task = None

        # 取消所有未完成的 pending futures
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError("MCP 连接已断开"))
        self._pending.clear()

    # ── 请求/响应 ──────────────────────────────────────────────────

    async def send_request(
        self,
        request: JSONRPCRequest,
        timeout: float = REQUEST_TIMEOUT,
    ) -> JSONRPCResponse:
        """发送 JSON-RPC 请求并等待响应。

        Args:
            request: JSON-RPC 请求对象
            timeout: 超时秒数

        Returns:
            解析后的 JSON-RPC 响应

        Raises:
            RuntimeError: 连接未建立或已断开
            asyncio.TimeoutError: 响应超时
        """
        if not self._proc or self._proc.returncode is not None:
            raise RuntimeError("MCP 子进程未运行")

        self._request_id += 1
        request.id = self._request_id

        # 注册 pending future
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[request.id] = future

        try:
            self._send_line(request.to_json())
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request.id, None)

    def _send_line(self, line: str) -> None:
        """写入一行 JSON 到子进程 stdin。"""
        if self._proc and self._proc.stdin:
            self._proc.stdin.write((line + "\n").encode("utf-8"))

    async def _read_loop(self) -> None:
        """持续读取子进程 stdout，分发响应到 pending futures。"""
        if not self._proc or not self._proc.stdout:
            return

        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:  # EOF
                    break

                line_str = line.decode("utf-8").strip()
                if not line_str:
                    continue

                try:
                    response = parse_response(line_str)
                except json.JSONDecodeError:
                    logger.warning(f"[MCP stdio] 无法解析响应行: {line_str[:100]}")
                    continue

                # 分发到对应的 pending future
                future = self._pending.get(response.id)
                if future and not future.done():
                    future.set_result(response)
                elif response.id == 0:
                    # 通知或服务端推送（无 id），暂不处理
                    logger.debug(f"[MCP stdio] 收到服务端推送: {line_str[:100]}")
                else:
                    logger.debug(f"[MCP stdio] 收到无匹配请求的响应 id={response.id}")

        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[MCP stdio] read loop 异常")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._proc is not None and self._proc.returncode is None


# ── HTTP Transport ───────────────────────────────────────────────────


class HTTPTransport:
    """MCP HTTP transport — 使用 Streamable HTTP (POST + 可选 SSE)。

    发送 JSON-RPC 请求到 HTTP 端点，通过响应体获取结果。
    支持标准 HTTP POST 模式。

    用法:
        transport = HTTPTransport()
        await transport.connect("http://localhost:8080/mcp")
        tools = await transport.send_request(make_tools_list_request())
        await transport.disconnect()
    """

    def __init__(self) -> None:
        self._url: str = ""
        self._session_id: str | None = None
        self._request_id = 0
        self._connected = False

    async def connect(self, url: str) -> None:
        """连接到 MCP HTTP 服务端并完成 initialize 握手。

        Args:
            url: MCP 服务端端点 URL

        Raises:
            RuntimeError: 连接或握手失败
        """
        self._url = url.rstrip("/")

        # initialize 握手
        try:
            response = await self._http_post(
                make_initialize_request(),
                timeout=INIT_TIMEOUT,
            )
        except Exception as e:
            raise RuntimeError(f"MCP HTTP 连接失败: {e}") from e

        if response.is_error:
            raise RuntimeError(
                f"MCP HTTP initialize 失败: {response.error_message}"
            )

        # 发送 initialized 通知
        notification = make_initialized_notification()
        try:
            await self._http_post_notification(notification)
        except Exception:
            pass  # 通知失败不阻断连接

        self._connected = True
        logger.info(f"[MCP HTTP] 已连接: {self._url}")

    async def disconnect(self) -> None:
        """断开连接。"""
        self._connected = False
        self._session_id = None
        self._url = ""

    async def send_request(
        self,
        request: JSONRPCRequest,
        timeout: float = REQUEST_TIMEOUT,
    ) -> JSONRPCResponse:
        """发送 JSON-RPC 请求并等待响应。

        Args:
            request: JSON-RPC 请求对象
            timeout: 超时秒数

        Returns:
            解析后的 JSON-RPC 响应

        Raises:
            RuntimeError: 连接未建立
        """
        if not self._connected:
            raise RuntimeError("MCP HTTP 连接未建立")

        self._request_id += 1
        request.id = self._request_id
        return await self._http_post(request, timeout=timeout)

    async def _http_post(
        self,
        request: JSONRPCRequest,
        timeout: float = REQUEST_TIMEOUT,
    ) -> JSONRPCResponse:
        """发送 JSON-RPC 请求并解析响应。"""
        import urllib.request
        import urllib.error

        body = request.to_json().encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        loop = asyncio.get_running_loop()

        def _sync_post():
            req = urllib.request.Request(
                self._url,
                data=body,
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    # 捕获 session ID
                    sid = resp.headers.get("Mcp-Session-Id", "")
                    if sid:
                        self._session_id = sid
                    return resp.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                error_body = ""
                try:
                    error_body = e.read().decode("utf-8")
                except Exception:
                    pass
                raise RuntimeError(
                    f"HTTP {e.code} from MCP server: {error_body[:200]}"
                ) from e
            except urllib.error.URLError as e:
                raise ConnectionError(f"MCP HTTP 请求失败: {e.reason}") from e

        try:
            data = await asyncio.wait_for(
                loop.run_in_executor(None, _sync_post),
                timeout=timeout + 5,
            )
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError(f"MCP HTTP 请求超时 ({timeout}s)")

        # 处理可能的 SSE 或 chunked 响应
        data = data.strip()
        if not data:
            return JSONRPCResponse(id=request.id, result={})

        # 如果是 SSE 格式，提取 data: 行
        if data.startswith("event:") or data.startswith("data:"):
            for line in data.split("\n"):
                if line.startswith("data:"):
                    data = line[5:].strip()
                    break

        try:
            return parse_response(data)
        except json.JSONDecodeError:
            logger.warning(f"[MCP HTTP] 无法解析响应: {data[:100]}")
            return JSONRPCResponse(
                id=request.id,
                error={"code": -32000, "message": f"无法解析响应: {data[:100]}"},
            )

    async def _http_post_notification(
        self, notification: JSONRPCNotification,
    ) -> None:
        """发送 JSON-RPC 通知（不等待响应）。"""
        import urllib.request

        body = notification.to_json().encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        loop = asyncio.get_running_loop()

        def _sync_post():
            req = urllib.request.Request(
                self._url, data=body, headers=headers, method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=10)
            except Exception:
                pass  # 通知失败不抛异常

        await loop.run_in_executor(None, _sync_post)

    @property
    def is_connected(self) -> bool:
        return self._connected and bool(self._url)


# ── Transport 工厂 ───────────────────────────────────────────────────


async def create_transport(
    command: str = "",
    args: list[str] | None = None,
    url: str = "",
) -> StdioTransport | HTTPTransport:
    """根据配置创建合适的 transport。

    - 如果提供了 command → StdioTransport
    - 如果提供了 url → HTTPTransport

    Raises:
        ValueError: 既未提供 command 也未提供 url
    """
    if command:
        transport = StdioTransport()
        await transport.connect(command, args or [])
        return transport

    if url:
        transport = HTTPTransport()
        await transport.connect(url)
        return transport

    raise ValueError("必须提供 command（stdio）或 url（HTTP）")
