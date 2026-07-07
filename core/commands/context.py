"""CommandContext — 命令处理器可访问的应用接口。

定义 Protocol 替代 handler 签名中的 `app: Any`，
让 handler 获得类型检查 + 可独立测试（Mock 实现此 Protocol 即可）。

使用方式:
    from core.commands.context import CommandContext

    async def handle_help(app: CommandContext, args: str) -> str:
        ...

AideApp 自动满足此 Protocol（结构类型），无需显式继承。
"""

from __future__ import annotations

from typing import Protocol, Any


class CommandContext(Protocol):
    """命令处理器对应用的视图 — AideApp 的公共接口子集。

    仅暴露 handler 实际使用的属性和方法。
    测试中可创建 MockCommandContext 替代 AideApp。
    """

    # ── 核心依赖 ──
    _kernel: Any          # AgentKernel（提供 chat/create_session/list_sessions 等）
    _cmd_registry: Any    # CommandRegistry（提供 list_all/register/route）
    _ingester: Any        # ContextIngester（提供 _session_dir/_session_id）
    _session: Any         # SessionContext（提供 name/turn/is_ensured/reset 等）
    _cmd_handler: Any     # CommandHandler（提供 exit_maintenance）
    _config: Any          # Config（提供 app.locale/sessions_root 等）
    _tool_registry: Any   # ToolRegistry（提供 get/list_names）
    _mcp_adapter: Any     # MCPAdapter（提供 list_servers/connect 等）

    # ── 运行时状态 ──
    provider: Any         # AbstractProvider | None
    _model_name: str
    _api_name: str

    # ── UI 方法 ──
    def query_one(self, selector: str, expected_type: type | None = None) -> Any:
        """查找子 widget。"""
        ...

    async def open_api_config_screen(self, edit_name: str | None = None) -> dict | None:
        """打开 API 配置屏幕，返回用户配置或 None（取消时）。"""
        ...

    def refresh_command_palette(self) -> None:
        """刷新命令面板（语言切换后重新加载描述）。"""
        ...

    def refresh_status_bar_model(
        self, model: str | None = None, api_name: str | None = None,
    ) -> None:
        """刷新状态栏模型/API 名。"""
        ...
