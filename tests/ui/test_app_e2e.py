"""真实 UI e2e：Textual run_test + pilot 驱动 AideApp 启动 → 建会话 → 对话 → 树渲染。

AppBootstrap.init 被 patch 为返回真实组件（FakeProvider 内核）的 BootstrapResult，
其余（HomeScreen、输入框、MessageList 树、落盘）全部走真实路径。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from textual.widgets import Input

from core.config import Config
from core.kernel.agent import AgentKernel
from core.kernel.bootstrap import BootstrapResult
from core.kernel.context import KernelContext, MemoryContext, ToolingContext, SessionContext
from core.tools import ToolRegistry
from core.commands import CommandRegistry
from core.context.pipeline import ContextPipeline
from core.context.ingester import ContextIngester
from core.storage import JsonStore
from core.sessions.manager import SessionManager
from core.llm_gateway import TextDelta, StreamEnd


def msg_list_has_reply(app) -> bool:
    """假回复是否已渲染进消息树。"""
    try:
        from ui.textual_app.widgets.message_list import MessageList
        msg_list = app.query_one("#messages", MessageList)
        return "假模型" in (msg_list._turn_ai_text or "")
    except Exception:
        return False


async def _make_bootstrap(tmp_path) -> BootstrapResult:
    """真实组件 + FakeProvider 的 BootstrapResult。"""
    agent_root = tmp_path / "agent"
    agent_root.mkdir(parents=True)
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    config = Config(aide_root=tmp_path)

    store = JsonStore()
    await store.start()

    tool_registry = ToolRegistry()
    cmd_registry = CommandRegistry()
    # 混合 mock：stop_watcher 被 await（AsyncMock），stop_health_check 同步调用（普通 mock）
    mcp_adapter = MagicMock()
    mcp_adapter.stop_watcher = AsyncMock()
    pipeline = ContextPipeline(agent_root=agent_root)
    ingester = ContextIngester(store, sessions_root=sessions_root)

    provider = MagicMock()
    provider.supports_vision = False

    async def _chat(messages, tools):
        yield TextDelta(content="这是假模型的回复。")
        yield StreamEnd(finish_reason="stop", tool_calls=[])

    provider.chat_with_tools = _chat

    kernel_ctx = KernelContext(
        config=config,
        provider=provider,
        tooling=ToolingContext(
            tool_registry=tool_registry,
            command_registry=cmd_registry,
            plugin_host=MagicMock(),
            slot_registry=MagicMock(),
        ),
        memory=MemoryContext(reflector=MagicMock()),
        session=SessionContext(
            context_pipeline=pipeline,
            ingester=ingester,
            session_manager=SessionManager(sessions_root),
        ),
    )
    kernel = AgentKernel(kernel_ctx)

    return BootstrapResult(
        config=config, provider=provider, model_name="fake-model",
        tool_registry=tool_registry, mcp_adapter=mcp_adapter, cmd_registry=cmd_registry,
        ingester=ingester, pipeline=pipeline, kernel=kernel, store=store, errors=[],
    )


class TestAppBootE2E:
    """AideApp 真实启动 → HomeScreen → 建会话 → 对话 → 假回复渲染 + 落盘。"""

    @pytest.mark.asyncio
    async def test_boot_create_chat_render(self, tmp_path):
        from ui.textual_app.app import AideApp
        from ui.textual_app.widgets.message_list import MessageList
        from ui.textual_app.widgets.input_box import InputBox

        bootstrap = await _make_bootstrap(tmp_path)

        with patch("ui.textual_app.app.AppBootstrap.init",
                   new=AsyncMock(return_value=bootstrap)), \
             patch("ui.textual_app.app.has_existing_config", return_value=True):
            app = AideApp()
            async with app.run_test() as pilot:
                # 等 _startup_worker 完成并 push HomeScreen（HomeScreen 是独立 screen，
                # 其 widget 须从 app.screen 查询，app.query 看不到被覆盖 screen 的内容）
                for _ in range(30):
                    await pilot.pause(0.05)
                    if app.screen.__class__.__name__ == "HomeScreen" \
                            and app.screen.query("#new-session-input"):
                        break

                # ── HomeScreen 显示，输入首条消息 → 建会话 ──
                home_input = app.screen.query_one("#new-session-input", Input)
                home_input.value = "帮我看看这个项目"
                await pilot.press("enter")
                # 等进入对话页（HomeScreen pop，app 根的 #input 可见）
                for _ in range(30):
                    await pilot.pause(0.05)
                    if app.query("#input"):
                        break

                # ── 进入对话页，发送消息 ──
                conv_input = app.query_one("#input", InputBox)
                conv_input.value = "继续"
                await pilot.press("enter")

                # 等待 chat worker 完成（FakeProvider 立即返回）
                for _ in range(30):
                    await pilot.pause(0.05)
                    if msg_list_has_reply(app):
                        break

                # ── 断言 1：假回复已渲染进消息树 ──
                msg_list = app.query_one("#messages", MessageList)
                assert msg_list._turn_ai_text, "AI 回复未累积到消息列表"
                assert "假模型" in msg_list._turn_ai_text

                # ── 断言 2：会话已创建并落盘 ──
                sessions_root = tmp_path / "sessions"
                session_dirs = [d for d in sessions_root.iterdir() if d.is_dir()]
                assert len(session_dirs) == 1, "应创建一个会话目录"
                turn_files = list((session_dirs[0] / "messages").glob("turn_*.json"))
                assert len(turn_files) >= 1, "对话应落盘到 turn 文件"

            # run_test 退出时 on_unmount 自动 close store / 停 MCP
