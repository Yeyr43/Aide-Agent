"""Ctrl+Q 强制终止 agent 工作 — 端到端测试（mock bootstrap）。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ui.textual_app.app import AideApp


def _fake_bootstrap_result():
    """构造 AppBootstrap.init() 的假结果，供 on_mount 消费。"""
    kernel = MagicMock()

    async def hanging_chat(**kwargs):
        # 挂起直到被取消（模拟长任务）
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise

    kernel.chat = hanging_chat
    kernel.create_session = AsyncMock(return_value=(
        MagicMock(id="s1", name="测试会话"),
        MagicMock(),
    ))

    result = MagicMock()
    result.kernel = kernel
    result.config.app.locale = "zh"
    result.config.app.active_api = "openai"
    result.config.app.context_window = 128000  # _update_status_bar 会做 int 比较
    result.provider = MagicMock()
    result.model_name = "test-model"
    result.store = MagicMock()
    result.store.close = AsyncMock()  # on_unmount 会 await
    result.tool_registry = MagicMock()
    result.tool_registry.get_schemas = lambda: []  # 状态栏 token 估算会遍历
    result.mcp_adapter = MagicMock()
    result.mcp_adapter.stop_watcher = AsyncMock()  # on_unmount 会 await
    result.cmd_registry = MagicMock()
    result.ingester = MagicMock()
    result.pipeline = MagicMock()
    return result


@pytest.fixture
def _bootstrap_env():
    """patch 掉 on_mount 的依赖：bootstrap / 启动导航 / 配置检测。"""
    with patch("ui.textual_app.app.AppBootstrap.init",
               new=AsyncMock(return_value=_fake_bootstrap_result())), \
         patch("ui.textual_app.app.has_existing_config", return_value=True), \
         patch.object(AideApp, "_startup_worker", lambda self: None):
        yield


@pytest.mark.asyncio
async def test_ctrl_q_binding_maps_to_cancel_agent():
    bindings = dict((key, action) for key, action, _ in AideApp.BINDINGS)
    assert bindings.get("ctrl+q") == "cancel_agent"


@pytest.mark.asyncio
async def test_ctrl_q_cancels_running_chat_worker(_bootstrap_env):
    app = AideApp()
    async with app.run_test() as pilot:
        # 直接启动 chat_worker（绕过输入流程）：kernel.chat 挂起 → worker 运行中
        app._session.is_ensured = False
        app._chat_worker = app.chat_worker()
        await pilot.pause()
        assert app._chat_worker is not None and app._chat_worker.is_running

        # Ctrl+Q → 强制终止
        app.action_cancel_agent()
        await pilot.pause()

        assert not app._chat_worker.is_running, "worker 应被取消"
        # 系统提示已出现
        from ui.textual_app.widgets.tree_nodes import SystemNode
        notices = [w for w in app.query(".tree-node")
                   if type(w).__name__ == "SystemNode"]
        assert len(notices) == 1, "应显示已终止提示"


@pytest.mark.asyncio
async def test_ctrl_q_with_no_running_worker_is_noop(_bootstrap_env):
    app = AideApp()
    async with app.run_test():
        # 无进行中任务：按键应安全忽略（不崩溃、无提示）
        app.action_cancel_agent()
        from ui.textual_app.widgets.tree_nodes import SystemNode
        notices = [w for w in app.query(".tree-node")
                   if type(w).__name__ == "SystemNode"]
        assert len(notices) == 0
