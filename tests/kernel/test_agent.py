"""Tests for AgentKernel facade."""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from core.kernel.agent import AgentKernel, ChatResult
from core.kernel.context import KernelContext, MemoryContext, ToolingContext, SessionContext
from core.config import Config
from core.errors import ProviderError, AideError


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


class TestAgentKernelExtensions:
    def test_set_provider_syncs_auto_memory(self, tmp_path):
        """set_provider 同步 auto_memory / reflector 的 provider 引用。"""
        ctx = _make_context(tmp_path)
        ctx.memory.auto_memory = MagicMock()
        kernel = AgentKernel(ctx)
        new_provider = MagicMock(supports_vision=False)
        kernel.set_provider(new_provider)
        assert kernel._auto_memory._provider is new_provider
        assert kernel._reflector._provider is new_provider

    @pytest.mark.asyncio
    async def test_rollback_session(self, kernel):
        kernel._sessions.rollback.return_value = 7
        result = kernel.rollback_session(Path("/tmp/s"), 3)
        assert result == 7
        kernel._sessions.rollback.assert_called_once_with(Path("/tmp/s"), 3)

    @pytest.mark.asyncio
    async def test_load_plugin(self, kernel):
        kernel._plugins.load = AsyncMock(return_value="loaded")
        assert await kernel.load_plugin("p1") == "loaded"
        kernel._plugins.load.assert_awaited_once_with("p1")

    @pytest.mark.asyncio
    async def test_unload_plugin(self, kernel):
        kernel._plugins.unload = AsyncMock(return_value=True)
        assert await kernel.unload_plugin("p1") is True
        kernel._plugins.unload.assert_awaited_once_with("p1")

    @pytest.mark.asyncio
    async def test_reflect_delegates(self, kernel):
        kernel._reflector.reflect = AsyncMock(return_value="reflection")
        result = await kernel.reflect(Path("/tmp/s"), 5)
        assert result == "reflection"
        kernel._reflector.reflect.assert_awaited_once_with(Path("/tmp/s"), 5)

    @pytest.mark.asyncio
    async def test_apply_reflection_delegates(self, kernel):
        kernel._reflector.apply = AsyncMock()
        await kernel.apply_reflection(Path("/tmp/s"), "result", 5)
        kernel._reflector.apply.assert_awaited_once_with(Path("/tmp/s"), "result", 5)


class TestMergeUpdated:
    def test_no_assistant_content_adds_placeholder(self):
        conversation = [{"role": "user", "content": "hi"}]
        updated = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": "res", "tool_call_id": "c1"},
        ]
        text, new_conv, turn_msgs = AgentKernel._merge_updated(conversation, updated)
        assert "未收到 AI 响应" in text
        assert new_conv[-1]["role"] == "assistant"
        assert new_conv[-1]["content"] == text


class TestAgentKernelErrorHandling:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc,expect", [
        (ProviderError("API down", provider="x", status_code=500), "LLM 服务异常"),
        (AideError("system broken"), "系统异常"),
        (RuntimeError("unexpected"), "系统错误"),
    ])
    async def test_chat_fc_failure_fallback(self, tmp_path, exc, expect):
        ctx = _make_context(tmp_path)
        ctx.session.context_pipeline.assemble.return_value = ([], [])
        kernel = AgentKernel(ctx)
        kernel._fc_loop = AsyncMock()
        kernel._fc_loop.run.side_effect = exc
        session_dir = tmp_path / "s1"
        session_dir.mkdir()
        ui = MagicMock()
        result = await kernel.chat("hi", session_dir, 1, [], ui)
        assert expect in result.assistant_text


class TestFeedbackIntegration:
    def _kernel_with_feedback(self, tmp_path):
        ctx = _make_context(tmp_path)
        ctx.memory.feedback_verifier = MagicMock()
        # get_last_memory_fragments 是同步方法 — 用 MagicMock 避免 AsyncMock 返回未 await 的协程
        ctx.session.context_pipeline = MagicMock()
        ctx.session.context_pipeline.assemble = AsyncMock(return_value=([], []))
        kernel = AgentKernel(ctx)
        kernel._fc_loop = AsyncMock()
        kernel._fc_loop.run.return_value = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "好的"},
        ]
        return kernel

    @pytest.mark.asyncio
    async def test_chat_runs_feedback_verifier(self, tmp_path):
        kernel = self._kernel_with_feedback(tmp_path)
        kernel._pipeline.get_last_memory_fragments.return_value = [
            {"id": "pref_001", "content": "保持简洁"},
        ]
        session_dir = tmp_path / "s1"
        session_dir.mkdir()
        ui = MagicMock()
        await kernel.chat("hi", session_dir, 5, [{"role": "user", "content": "hi"}], ui)
        kernel._feedback.verify.assert_called_once()
        kwargs = kernel._feedback.verify.call_args.kwargs
        assert kwargs["session_id"] == "s1"
        assert kwargs["turn"] == 5
        assert kwargs["user_msg"] == "hi"

    @pytest.mark.asyncio
    async def test_chat_feedback_exception_swallowed(self, tmp_path):
        kernel = self._kernel_with_feedback(tmp_path)
        kernel._pipeline.get_last_memory_fragments.return_value = [{"id": "x"}]
        kernel._feedback.verify.side_effect = RuntimeError("verifier bug")
        session_dir = tmp_path / "s1"
        session_dir.mkdir()
        ui = MagicMock()
        result = await kernel.chat("hi", session_dir, 5, [{"role": "user", "content": "hi"}], ui)
        assert result.assistant_text == "好的"


class TestAutoMemory:
    @pytest.mark.asyncio
    async def test_chat_schedules_auto_memory_extraction(self, tmp_path):
        ctx = _make_context(tmp_path)
        auto = MagicMock()
        auto.maybe_extract = AsyncMock()
        ctx.memory.auto_memory = auto
        ctx.session.context_pipeline.assemble.return_value = ([], [])
        kernel = AgentKernel(ctx)
        kernel._fc_loop = AsyncMock()
        kernel._fc_loop.run.return_value = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "好的"},
        ]
        session_dir = tmp_path / "s1"
        session_dir.mkdir()
        ui = MagicMock()
        await kernel.chat("hi", session_dir, 3, [], ui)
        await asyncio.sleep(0)  # 让 fire-and-forget 任务开始执行
        auto.maybe_extract.assert_called_once()


class TestLifecycleHooks:
    def _kernel_with_hooks(self, tmp_path, hook_runner):
        ctx = _make_context(tmp_path)
        ctx.hook_runner = hook_runner
        ctx.session.context_pipeline.assemble.return_value = ([], [])
        kernel = AgentKernel(ctx)
        kernel._fc_loop = AsyncMock()
        kernel._fc_loop.run.return_value = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "好的"},
        ]
        return kernel

    @pytest.mark.asyncio
    async def test_chat_fires_hooks(self, tmp_path):
        hook_runner = AsyncMock()
        hook_runner.run.return_value = []
        kernel = self._kernel_with_hooks(tmp_path, hook_runner)
        session_dir = tmp_path / "s1"
        session_dir.mkdir()
        ui = MagicMock()
        await kernel.chat("hi", session_dir, 1, [], ui)
        events = [c.args[0] for c in hook_runner.run.call_args_list]
        assert "UserPromptSubmit" in events
        assert "PreCompact" in events
        assert "Stop" in events
        assert "Notification" in events

    @pytest.mark.asyncio
    async def test_chat_logs_nonzero_hook_exit(self, tmp_path):
        from core.plugins.hook_runner import HookResult
        hook_runner = AsyncMock()
        hook_runner.run.return_value = [HookResult(exit_code=2, stderr="denied")]
        kernel = self._kernel_with_hooks(tmp_path, hook_runner)
        session_dir = tmp_path / "s1"
        session_dir.mkdir()
        ui = MagicMock()
        await kernel.chat("hi", session_dir, 1, [], ui)  # 不应抛异常

    @pytest.mark.asyncio
    async def test_chat_hook_exception_swallowed(self, tmp_path):
        hook_runner = AsyncMock()
        hook_runner.run.side_effect = RuntimeError("hook bug")
        kernel = self._kernel_with_hooks(tmp_path, hook_runner)
        session_dir = tmp_path / "s1"
        session_dir.mkdir()
        ui = MagicMock()
        result = await kernel.chat("hi", session_dir, 1, [], ui)
        assert result.assistant_text == "好的"
