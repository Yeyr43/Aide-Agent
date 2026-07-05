"""测试命令系统 — 路由和处理器。"""

import json
from pathlib import Path

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from core.commands import CommandDefinition
from core.commands.builtin._compat import COMMANDS, route_command
from core.commands.builtin.handlers import (
    handle_help, handle_profile, handle_compress, handle_export, handle_import,
    handle_session, handle_memory, handle_tools, handle_update, handle_clear,
    handle_mcp, handle_rollback,
)


class TestCommandRouting:
    """测试命令路由。"""

    def test_not_command(self):
        assert route_command("hello") is None
        assert route_command("你好") is None

    def test_bare_slash(self):
        assert route_command("/") is None

    def test_exact_match(self):
        result = route_command("/help")
        assert result is not None
        handler, args = result
        assert handler == handle_help
        assert args == ""

    def test_prefix_match(self):
        result = route_command("/hel")
        assert result is not None
        handler, args = result
        assert handler == handle_help

    def test_with_args(self):
        result = route_command("/import test.zip")
        assert result is not None
        handler, args = result
        assert handler == handle_import
        assert args == "test.zip"

    def test_update_route(self):
        result = route_command("/update")
        assert result is not None
        handler, args = result
        assert handler == handle_update
        assert args == ""

    def test_all_commands_registered(self):
        """P5: 16 个内置命令。"""
        assert "/help" in COMMANDS
        assert "/profile" in COMMANDS
        assert "/compact" in COMMANDS
        assert "/export" in COMMANDS
        assert "/import" in COMMANDS
        assert "/plugin" in COMMANDS
        assert "/session" in COMMANDS
        assert "/memory" in COMMANDS
        assert "/tools" in COMMANDS
        assert "/update" in COMMANDS
        assert "/clear" in COMMANDS
        assert "/mcp" in COMMANDS
        assert "/rollback" in COMMANDS
        assert "/language" in COMMANDS
        assert "/api" in COMMANDS
        assert "/model" in COMMANDS
        assert len(COMMANDS) == 16

    def test_session_route(self):
        result = route_command("/session list")
        assert result is not None
        handler, args = result
        assert handler == handle_session
        assert args == "list"

    def test_memory_route(self):
        result = route_command("/memory")
        assert result is not None
        handler, args = result
        assert handler == handle_memory
        assert args == ""

    def test_tools_route(self):
        result = route_command("/tools")
        assert result is not None
        handler, args = result
        assert handler == handle_tools

    def test_clear_route(self):
        result = route_command("/clear")
        assert result is not None
        handler, args = result
        assert handler == handle_clear
        assert args == ""

    def test_rollback_route(self):
        result = route_command("/rollback 3")
        assert result is not None
        handler, args = result
        assert handler == handle_rollback
        assert args == "3"


class TestCommandHandlers:
    """测试命令处理器（不依赖 Textual App）。"""

    @pytest.mark.asyncio
    async def test_handle_help(self):
        # 构造 mock CommandRegistry（含 16 个内置命令）
        from core.commands import CommandDefinition
        mock_registry = MagicMock()
        mock_registry.list_all.return_value = [
            CommandDefinition(name=n, description="test", handler=handle_help)
            for n in ["/help", "/profile", "/compact", "/export", "/import",
                       "/plugin", "/session", "/memory", "/tools", "/update",
                       "/clear", "/rollback", "/mcp", "/language", "/api", "/model"]
        ]
        result = await handle_help(MagicMock(_cmd_registry=mock_registry), "")
        assert "可用命令" in result
        for cmd in ["/help", "/profile", "/compact", "/export", "/import", "/plugin",
                     "/session", "/memory", "/tools", "/update", "/clear", "/mcp",
                     "/rollback"]:
            assert cmd in result

    @pytest.mark.asyncio
    async def test_handle_update(self):
        result = await handle_update(MagicMock(), "")
        assert result == "__PROFILE_UPDATE__"

    @pytest.mark.asyncio
    async def test_handle_profile_no_update_subcommand(self):
        """/profile 不再处理 update 子命令。"""
        result = await handle_profile(MagicMock(), "update")
        assert "Profile" in result
        assert "__PROFILE_UPDATE__" not in result

    @pytest.mark.asyncio
    async def test_handle_compress(self):
        result = await handle_compress(MagicMock(), "")
        assert result == "__COMPRESS__"

    @pytest.mark.asyncio
    async def test_handle_export(self, tmp_path):
        """导出应生成 zip 文件。"""
        aide_root = tmp_path / ".aide"
        agent_root = aide_root / "agent"
        agent_root.mkdir(parents=True)
        (agent_root / "data").mkdir()
        (agent_root / "soul.md").write_text("test", encoding="utf-8")
        (agent_root / "data" / "preferences.json").write_text("[]", encoding="utf-8")
        (aide_root / "sessions").mkdir()

        with patch('core.commands.builtin.handlers.AIDE_ROOT', aide_root), \
             patch('core.commands.builtin.handlers.AGENT_ROOT', agent_root), \
             patch('core.commands.builtin.handlers.Path.home',
                   return_value=tmp_path):
            result = await handle_export(MagicMock(), "")
            assert "导出" in result
            zips = list(tmp_path.glob("aide_export_*.zip"))
            assert len(zips) >= 1

    @pytest.mark.asyncio
    async def test_handle_import_no_args(self):
        result = await handle_import(MagicMock(), "")
        assert "请指定" in result

    @pytest.mark.asyncio
    async def test_handle_import_file_not_found(self):
        result = await handle_import(MagicMock(), "/nonexistent/file.zip")
        assert "文件不存在" in result

    @pytest.mark.asyncio
    async def test_handle_import_not_zip(self, tmp_path):
        txt = tmp_path / "test.txt"
        txt.write_text("hello")
        result = await handle_import(MagicMock(), str(txt))
        assert ".zip" in result

    @pytest.mark.asyncio
    async def test_handle_profile_empty(self):
        """无文件时 /profile 应显示提示。"""
        from pathlib import Path
        with patch('core.commands.builtin.handlers.AGENT_ROOT',
                   Path("/nonexistent")):
            result = await handle_profile(MagicMock(), "")
            assert "不存在" in result.lower() or "Profile" in result

    # ── P4 Batch 2: 新命令测试 ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_handle_session_no_kernel(self):
        app = MagicMock(_kernel=None)
        result = await handle_session(app, "")
        assert "未初始化" in result

    @pytest.mark.asyncio
    async def test_handle_session_list_empty(self):
        kernel = AsyncMock()
        kernel.list_sessions.return_value = []
        app = MagicMock(_kernel=kernel)
        result = await handle_session(app, "list")
        assert "暂无" in result

    @pytest.mark.asyncio
    async def test_handle_session_list_with_sessions(self):
        from core.sessions.manager import SessionInfo
        kernel = AsyncMock()
        kernel.list_sessions.return_value = [
            SessionInfo(id="20260703_120000", name="Test"),
            SessionInfo(id="20260703_130000", name="Demo"),
        ]
        app = MagicMock(_kernel=kernel)
        result = await handle_session(app, "list")
        assert "Test" in result
        assert "Demo" in result
        assert "共 2 个会话" in result

    @pytest.mark.asyncio
    async def test_handle_session_delete_no_args(self):
        kernel = AsyncMock()
        app = MagicMock(_kernel=kernel)
        result = await handle_session(app, "delete")
        assert "用法" in result

    @pytest.mark.asyncio
    async def test_handle_session_delete_success(self):
        kernel = AsyncMock()
        kernel.delete_session.return_value = True
        app = MagicMock(_kernel=kernel)
        result = await handle_session(app, "delete my-id")
        assert "已删除" in result

    @pytest.mark.asyncio
    async def test_handle_session_delete_not_found(self):
        kernel = AsyncMock()
        kernel.delete_session.return_value = False
        app = MagicMock(_kernel=kernel)
        result = await handle_session(app, "delete bad-id")
        assert "未找到" in result

    @pytest.mark.asyncio
    async def test_handle_session_invalid_subcommand(self):
        kernel = AsyncMock()
        app = MagicMock(_kernel=kernel)
        result = await handle_session(app, "xyz")
        assert "未知子命令" in result

    @pytest.mark.asyncio
    async def test_handle_memory_no_data(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        agent_root = tmp_path
        with patch('core.commands.builtin.handlers.AGENT_ROOT', agent_root):
            result = await handle_memory(MagicMock(), "")
            assert "尚无数据" in result

    @pytest.mark.asyncio
    async def test_handle_memory_with_pending(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "preferences.json").write_text(
            json.dumps([
                {"content": "偏好 A", "status": "confirmed"},
                {"content": "偏好 B", "status": "pending"},
            ]),
            encoding="utf-8",
        )
        with patch('core.commands.builtin.handlers.AGENT_ROOT', tmp_path):
            result = await handle_memory(MagicMock(), "")
            assert "1 已确认" in result
            assert "1 待整合" in result

    @pytest.mark.asyncio
    async def test_handle_tools_no_kernel(self):
        app = MagicMock(_kernel=None)
        result = await handle_tools(app, "")
        assert "未初始化" in result

    @pytest.mark.asyncio
    async def test_handle_tools_with_tools(self):
        tool_reg = MagicMock()
        tool_reg.list_names.return_value = ["read_file", "write_file", "run_shell", "mcp_fs_read"]
        tool_reg.get.return_value = MagicMock(description="A test tool")

        kernel = MagicMock(tool_registry=tool_reg)
        app = MagicMock(_kernel=kernel)
        result = await handle_tools(app, "")
        assert "read_file" in result
        assert "mcp_fs_read" in result
        assert "内置工具" in result
        assert "MCP 工具" in result

    @pytest.mark.asyncio
    async def test_handle_clear_returns_marker(self):
        result = await handle_clear(MagicMock(), "")
        assert result == "__CLEAR_CONFIRM__"

    @pytest.mark.asyncio
    async def test_handle_rollback_no_kernel(self):
        """无内核时返回错误提示。"""
        app = MagicMock(_kernel=None)
        result = await handle_rollback(app, "3")
        assert "未初始化" in result

    @pytest.mark.asyncio
    async def test_handle_rollback_invalid_turn(self):
        """非法轮数参数。"""
        kernel = MagicMock()
        app = MagicMock(_kernel=kernel)
        result = await handle_rollback(app, "abc")
        assert "用法" in result

    @pytest.mark.asyncio
    async def test_handle_rollback_no_session(self):
        """无活动会话。"""
        kernel = MagicMock()
        ingester = MagicMock(_session_dir=None)
        app = MagicMock(_kernel=kernel, _ingester=ingester)
        result = await handle_rollback(app, "3")
        assert "没有活动会话" in result

    @pytest.mark.asyncio
    async def test_handle_rollback_bounds_check(self):
        """回滚轮数不能超过当前轮数。"""
        kernel = MagicMock()
        ingester = MagicMock(_session_dir=Path("/tmp/test"))
        session = MagicMock(turn=3)
        app = MagicMock(_kernel=kernel, _ingester=ingester, _session=session)
        result = await handle_rollback(app, "5")
        assert "无法回滚" in result

    @pytest.mark.asyncio
    async def test_handle_rollback_negative(self):
        """负数轮数。"""
        kernel = MagicMock()
        ingester = MagicMock(_session_dir=Path("/tmp/test"))
        session = MagicMock(turn=3)
        app = MagicMock(_kernel=kernel, _ingester=ingester, _session=session)
        result = await handle_rollback(app, "-1")
        assert "必须 >= 1" in result

    @pytest.mark.asyncio
    async def test_handle_rollback_sets_pending(self):
        """有效轮数 → 设置 pending 状态 + 返回确认提示。"""
        kernel = MagicMock()
        ingester = MagicMock(_session_dir=Path("/tmp/test"))
        session = MagicMock(turn=5)
        app = MagicMock(_kernel=kernel, _ingester=ingester, _session=session)
        result = await handle_rollback(app, "3")
        assert session.pending_rollback is True
        assert session.pending_rollback_turn == 3
        assert "确定要回滚" in result
        assert "yes" in result

    @pytest.mark.asyncio
    async def test_handle_tools_empty(self):
        tool_reg = MagicMock()
        tool_reg.list_names.return_value = []

        kernel = MagicMock(tool_registry=tool_reg)
        app = MagicMock(_kernel=kernel)
        result = await handle_tools(app, "")
        assert "没有" in result


class TestMCPCommand:
    """测试 /mcp 命令。"""

    @pytest.mark.asyncio
    async def test_handle_mcp_no_adapter(self):
        """无 MCP 适配器时提示未初始化。"""
        result = await handle_mcp(MagicMock(_mcp_adapter=None), "")
        assert "未初始化" in result

    @pytest.mark.asyncio
    async def test_handle_mcp_list_empty(self):
        """无服务端时显示提示。"""
        adapter = MagicMock()
        adapter.list_servers.return_value = []
        app = MagicMock(_mcp_adapter=adapter)
        result = await handle_mcp(app, "")
        assert "没有" in result or "暂无" in result

    @pytest.mark.asyncio
    async def test_handle_mcp_list_with_servers(self):
        """列出服务端状态。"""
        from core.tools.mcp.adapter import MCPServerConfig, MCPServerStatus

        adapter = MagicMock()
        adapter.list_servers.return_value = [
            MCPServerConfig(name="filesystem", command="npx", args=[], enabled=False),
            MCPServerConfig(name="git", command="npx", args=[], enabled=True),
        ]
        adapter.get_server_status.side_effect = [
            MCPServerStatus(name="filesystem", transport="none", connected=False, enabled=False, tool_count=0, healthy=False),
            MCPServerStatus(name="git", transport="stdio", connected=True, enabled=True, tool_count=3, healthy=True),
        ]
        app = MagicMock(_mcp_adapter=adapter)
        result = await handle_mcp(app, "list")
        assert "filesystem" in result
        assert "git" in result
        assert "3 工具" in result

    @pytest.mark.asyncio
    async def test_handle_mcp_connect_no_args(self):
        """connect 缺少参数。"""
        adapter = MagicMock()
        app = MagicMock(_mcp_adapter=adapter)
        result = await handle_mcp(app, "connect")
        assert "用法" in result

    @pytest.mark.asyncio
    async def test_handle_mcp_disconnect_no_args(self):
        """disconnect 缺少参数。"""
        adapter = MagicMock()
        app = MagicMock(_mcp_adapter=adapter)
        result = await handle_mcp(app, "disconnect")
        assert "用法" in result

    @pytest.mark.asyncio
    async def test_handle_mcp_invalid_subcommand(self):
        """无效子命令。"""
        adapter = MagicMock()
        app = MagicMock(_mcp_adapter=adapter)
        result = await handle_mcp(app, "xyz")
        assert "未知" in result

    @pytest.mark.asyncio
    async def test_handle_mcp_routing(self):
        """/mcp 路由正确。"""
        result = route_command("/mcp")
        assert result is not None
        handler, args = result
        assert handler == handle_mcp

    @pytest.mark.asyncio
    async def test_handle_mcp_routing_with_args(self):
        """/mcp connect 路由。"""
        result = route_command("/mcp connect filesystem")
        assert result is not None
        handler, args = result
        assert handler == handle_mcp
        assert args == "connect filesystem"


