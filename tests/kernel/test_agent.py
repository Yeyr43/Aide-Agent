"""Tests for AgentKernel facade."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from core.kernel.agent import AgentKernel, ChatResult
from core.kernel.context import KernelContext
from core.config import Config


def _make_context(tmp_path):
    """构建 KernelContext，所有字段用 MagicMock 填充。"""
    config = Config(aide_root=tmp_path / ".aide")
    return KernelContext(
        config=config,
        provider=MagicMock(),
        tool_registry=MagicMock(),
        command_registry=MagicMock(),
        context_pipeline=AsyncMock(),
        ingester=AsyncMock(),
        compactor=MagicMock(),
        session_manager=MagicMock(),
        capture_engine=AsyncMock(),
        entry_manager=MagicMock(),
        prompt_updater=AsyncMock(),
        topic_tracker=MagicMock(),
        plugin_host=MagicMock(),
        slot_registry=MagicMock(),
    )


@pytest.fixture
def kernel(tmp_path):
    ctx = _make_context(tmp_path)
    ctx.context_pipeline.assemble.return_value = ([], [])
    ctx.capture_engine.capture.return_value = []
    return AgentKernel(ctx)


class TestAgentKernel:
    @pytest.mark.asyncio
    async def test_list_sessions_delegates(self, kernel):
        kernel._sessions.list_all.return_value = []
        result = await kernel.list_sessions()
        assert result == []

    @pytest.mark.asyncio
    async def test_delete_session(self, kernel):
        kernel._sessions.delete.return_value = True
        result = await kernel.delete_session("test-id")
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_session_not_found(self, kernel):
        kernel._sessions.delete.return_value = False
        result = await kernel.delete_session("nonexistent")
        assert result is False

    def test_flush_cache_delegates(self, kernel):
        kernel._pipeline.flush_cache = MagicMock()
        kernel.flush_cache()  # 不应抛异常

    @pytest.mark.asyncio
    async def test_create_session(self, kernel):
        from core.sessions.manager import SessionInfo
        expected_info = SessionInfo(id="20260702_120000", name="Test")
        kernel._sessions.create.return_value = expected_info
        kernel._sessions._root = Path("/tmp/sessions")

        info, session_dir = await kernel.create_session("Test message")
        assert info == expected_info
        assert session_dir == Path("/tmp/sessions") / "20260702_120000"
        kernel._sessions.create.assert_called_once_with("Test message")

    @pytest.mark.asyncio
    async def test_list_plugins_delegates(self, kernel):
        kernel._plugins.list_loaded.return_value = []
        result = kernel.list_plugins()
        assert result == []


class TestAgentKernelChat:
    @pytest.fixture
    def kernel_with_fc(self, tmp_path):
        ctx = _make_context(tmp_path)
        ctx.context_pipeline.assemble.return_value = (
            [{"role": "system", "content": "You are helpful."}],
            [{"role": "user", "content": "hello"}],
        )
        ctx.capture_engine.capture.return_value = [
            {"content": "用户偏好简洁", "status": "pending"},
        ]
        kernel = AgentKernel(ctx)

        # Mock the FC loop
        kernel._fc_loop = AsyncMock()
        kernel._fc_loop.run.return_value = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        return kernel

    @pytest.mark.asyncio
    async def test_chat_returns_chat_result(self, kernel_with_fc, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()

        ui = MagicMock()
        result = await kernel_with_fc.chat(
            user_msg="hello",
            session_dir=session_dir,
            turn=1,
            conversation=[],
            ui=ui,
        )

        assert isinstance(result, ChatResult)
        assert result.assistant_text == "Hi there!"
        assert len(result.captured_entries) == 1
        assert result.captured_entries[0]["content"] == "用户偏好简洁"

    @pytest.mark.asyncio
    async def test_chat_calls_pipeline_assemble(self, kernel_with_fc, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()

        ui = MagicMock()
        await kernel_with_fc.chat(
            user_msg="hello",
            session_dir=session_dir,
            turn=1,
            conversation=[{"role": "user", "content": "previous"}],
            ui=ui,
        )

        # P4 Batch 2: assemble now accepts context_providers from PluginHost
        call_args = kernel_with_fc._pipeline.assemble.call_args
        assert call_args is not None
        assert call_args[0][0] == session_dir
        assert call_args[0][1] == "hello"
        assert call_args[0][2] == [{"role": "user", "content": "previous"}]
        assert "context_providers" in call_args[1]

    @pytest.mark.asyncio
    async def test_chat_calls_ingester(self, kernel_with_fc, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()

        ui = MagicMock()
        await kernel_with_fc.chat(
            user_msg="hello",
            session_dir=session_dir,
            turn=3,
            conversation=[],
            ui=ui,
        )

        kernel_with_fc._ingester.ingest.assert_called_once()
        call_kwargs = kernel_with_fc._ingester.ingest.call_args.kwargs
        assert call_kwargs["turn"] == 3
        assert call_kwargs["user_msg"] == "hello"
        assert call_kwargs["assistant_msg"] == "Hi there!"

    @pytest.mark.asyncio
    async def test_chat_calls_capture_engine(self, kernel_with_fc, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()

        ui = MagicMock()
        await kernel_with_fc.chat(
            user_msg="hello",
            session_dir=session_dir,
            turn=1,
            conversation=[],
            ui=ui,
        )

        # P4: capture 在 LLM 之后运行 → 下轮注入上下文让 AI 自然回应
        kernel_with_fc._capture.capture.assert_called_once_with(
            user_msg="hello",
            assistant_msg="Hi there!",
            session_id=session_dir.name,
            turn=1,
        )
