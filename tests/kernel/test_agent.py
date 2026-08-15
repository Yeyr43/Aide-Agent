"""Tests for AgentKernel facade."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from core.kernel.agent import AgentKernel, ChatResult
from core.kernel.context import KernelContext, MemoryContext, ToolingContext, SessionContext
from core.config import Config


def _make_context(tmp_path):
    """构建 KernelContext，所有字段用 MagicMock 填充。"""
    config = Config(aide_root=tmp_path / ".aide")
    # ingester: MagicMock (set_session is sync, ingest is async)
    ingester_mock = MagicMock()
    ingester_mock.ingest = AsyncMock()

    return KernelContext(
        config=config,
        provider=MagicMock(),
        tooling=ToolingContext(
            tool_registry=MagicMock(),
            command_registry=MagicMock(),
            plugin_host=MagicMock(),
            slot_registry=MagicMock(),
        ),
        memory=MemoryContext(
            reflector=MagicMock(),
        ),
        session=SessionContext(
            context_pipeline=AsyncMock(),
            ingester=ingester_mock,
            session_manager=MagicMock(),
        ),
    )


@pytest.fixture
def kernel(tmp_path):
    ctx = _make_context(tmp_path)
    ctx.session.context_pipeline.assemble.return_value = ([], [])
    return AgentKernel(ctx)


class TestAgentKernel:
    def test_set_provider_syncs_supports_vision(self, kernel):
        """set_provider 同步 fc_loop 的 supports_vision。

        回归：fc_loop 在 __init__ 一次性读取该值，不刷新会导致切换视觉
        模型后图片仍被 _sanitize_messages 替换成占位。
        """
        new_provider = MagicMock(supports_vision=True)
        kernel.set_provider(new_provider)
        assert kernel._fc_loop.provider is new_provider
        assert kernel._fc_loop.supports_vision is True

        non_visual = MagicMock(supports_vision=False)
        kernel.set_provider(non_visual)
        assert kernel._fc_loop.supports_vision is False

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
        ctx.session.context_pipeline.assemble.return_value = (
            [{"role": "system", "content": "You are helpful."}],
            [{"role": "user", "content": "hello"}],
        )
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
    async def test_chat_returns_without_capture(self, kernel_with_fc, tmp_path):
        """P5 重构后 chat() 不再调用 capture engine。"""
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

        # 验证结果不含 captured_entries
        assert not hasattr(result, 'captured_entries')
        assert result.assistant_text == "Hi there!"
