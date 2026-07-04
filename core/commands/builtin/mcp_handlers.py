"""/mcp 命令 — MCP 服务端管理。

P5: 从 handlers.py 拆分，保持独立。
"""

from typing import Any

from core.locale import t
from ._compat import _cmd


@_cmd("mcp", t("cmd.mcp.desc"))
async def handle_mcp(app: Any, args: str) -> str:
    """MCP 服务端管理命令。

    子命令:
      list                  — 列出所有服务端状态
      connect <name>        — 连接指定服务端
      disconnect <name>     — 断开指定服务端
      reload                — 重载 mcp/ 目录配置
    """
    adapter = getattr(app, '_mcp_adapter', None)
    if adapter is None:
        return t("cmd.mcp.no_adapter")

    parts = args.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else "list"
    rest = parts[1] if len(parts) > 1 else ""

    if sub == "list" or sub == "":
        servers = adapter.list_servers()
        if not servers:
            return t("cmd.mcp.empty")

        lines = [t("cmd.mcp.list_title")]
        for s in servers:
            status = adapter.get_server_status(s.name)
            if status.circuit_tripped:
                icon = "⚡"
                state = t("cmd.mcp.state_circuit_broken")
            elif status.healthy:
                icon = "🟢"
                state = t("cmd.mcp.state_running")
            elif status.connected:
                icon = "🟡"
                state = t("cmd.mcp.state_connected")
            else:
                icon = "🔴"
                state = t("cmd.mcp.state_disconnected")

            extra = ""
            if status.tool_count > 0:
                extra = f" — {t('cmd.mcp.tool_count', n=status.tool_count)}"
            if status.circuit_tripped:
                extra += t("cmd.mcp.failure_hint", n=3, name=s.name)
            lines.append(
                f"- {icon} **{s.name}** ({status.transport}) — {state}{extra}"
            )
        lines.append(t("cmd.mcp.total_servers", n=len(servers)))
        lines.append(t("cmd.mcp.hint"))
        return "\n".join(lines)

    elif sub == "connect":
        if not rest:
            return t("cmd.mcp.usage_connect")
        try:
            await adapter.connect(rest)
            tools = await adapter.discover_tools(rest)
            if app is not None and hasattr(app, '_tool_registry'):
                for tool in tools:
                    app._tool_registry.register(tool)
            return t("cmd.mcp.connected", name=rest, count=len(tools))
        except KeyError:
            return t("cmd.mcp.not_found", name=rest)
        except Exception as e:
            return t("cmd.mcp.connect_failed", e=str(e))

    elif sub == "disconnect":
        if not rest:
            return t("cmd.mcp.usage_disconnect")
        await adapter.disconnect(rest)
        return t("cmd.mcp.disconnected", name=rest)

    elif sub == "reload":
        added, disconnected, reconnected = await adapter.reload_config()
        all_tools = await adapter.discover_all_tools()
        if app is not None and hasattr(app, '_tool_registry'):
            for tool in all_tools:
                app._tool_registry.register(tool)
        return t("cmd.mcp.reloaded", added=added, reconnected=reconnected,
                 disconnected=disconnected)

    else:
        return t("cmd.mcp.unknown_sub")
