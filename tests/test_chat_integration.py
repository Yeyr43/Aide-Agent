"""Integration tests: mock LLM → real FC loop → full ChatResult.

Tests the complete chat pipeline without mocking the FunctionCallingLoop.
Uses a real async generator LLM provider + real ToolRegistry + real ContextPipeline.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from core.kernel.agent import AgentKernel, ChatResult
from core.kernel.context import KernelContext, MemoryContext, ToolingContext, SessionContext
from core.kernel.fc_loop import FunctionCallingLoop
from core.config import Config
from core.tools import ToolRegistry, ToolDefinition
from core.context.pipeline import ContextPipeline
from core.context.ingester import ContextIngester
from core.storage import JsonStore
from core.llm_gateway import TextDelta, ThinkingDelta, StreamEnd


# ── Helpers ────────────────────────────────────────────────────────────

def _text_delta(content: str):
    return TextDelta(content=content)


def _stream_end(finish_reason: str = "stop"):
    return StreamEnd(finish_reason=finish_reason, tool_calls=[])


def _make_agent_dir(tmp_path: Path) -> Path:
    """Create minimal agent directory with soul.md + empty data files."""
    agent_root = tmp_path / "agent"
    agent_root.mkdir(parents=True)
    (agent_root / "soul.md").write_text("# Test Soul\nBe helpful and concise.", encoding="utf-8")
    (agent_root / "data").mkdir(exist_ok=True)
    for fname in ["preferences.json", "workflows.json", "long_term_memory.json"]:
        (agent_root / "data" / fname).write_text("[]", encoding="utf-8")
    return agent_root


def _mock_provider_with_response(text: str):
    """Create a mock provider whose chat_with_tools yields one text delta then stops."""
    provider = AsyncMock()

    async def _mock_chat(messages, tools):
        yield _text_delta(text)
        yield _stream_end()

    provider.chat_with_tools = _mock_chat
    provider.supports_vision = False
    return provider


def _mock_provider_with_thinking(thinking: str, text: str):
    """Mock provider: yields thinking deltas first, then the final text reply."""
    provider = AsyncMock()

    async def _mock_chat(messages, tools):
        yield ThinkingDelta(content=thinking)
        yield _text_delta(text)
        yield _stream_end()

    provider.chat_with_tools = _mock_chat
    provider.supports_vision = False
    return provider


def _mock_provider_with_tool_call(tool_name: str, arguments: dict, final_text: str = "Done!"):
    """Create a mock provider that makes one tool call, then responds."""
    provider = AsyncMock()
    call_count = [0]  # mutable counter

    async def _mock_chat(messages, tools):
        if call_count[0] == 0:
            call_count[0] += 1
            yield StreamEnd(
                finish_reason="tool_calls",
                tool_calls=[{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }],
            )
        else:
            yield _text_delta(final_text)
            yield _stream_end()

    provider.chat_with_tools = _mock_chat
    provider.supports_vision = False
    return provider


async def _make_store():
    s = JsonStore()
    await s.start()
    return s


# ── Test: Simple text response ─────────────────────────────────────────

class TestChatIntegration:
    """End-to-end chat flow with real FC loop and mock LLM."""

    @pytest.fixture
    async def store(self):
        s = await _make_store()
        yield s
        await s.close()

    @pytest.fixture
    def kernel(self, store, tmp_path):
        """Build a full AgentKernel with real components and mock provider."""
        agent_root = _make_agent_dir(tmp_path)
        sessions_root = tmp_path / "sessions"
        sessions_root.mkdir()
        config = Config(aide_root=tmp_path / ".aide")

        provider = _mock_provider_with_response("Hello! How can I help?")

        # Real ToolRegistry (empty — no tools registered)
        tool_registry = ToolRegistry()

        # Real ContextPipeline
        pipeline = ContextPipeline(agent_root=agent_root)

        # Real ingester
        ingester = ContextIngester(store, sessions_root=sessions_root)

        # Mock memory (reflector — not used in chat flow)
        ctx = KernelContext(
            config=config,
            provider=provider,
            tooling=ToolingContext(
                tool_registry=tool_registry,
                command_registry=MagicMock(),
                plugin_host=MagicMock(),
                slot_registry=MagicMock(),
            ),
            memory=MemoryContext(
                reflector=MagicMock(),
            ),
            session=SessionContext(
                context_pipeline=pipeline,
                ingester=ingester,
                session_manager=MagicMock(),
            ),
        )
        return AgentKernel(ctx)

    @pytest.mark.asyncio
    async def test_simple_text_response(self, kernel, tmp_path):
        """Mock LLM returns plain text → ChatResult with assistant_text."""
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / "messages").mkdir()

        ui = MagicMock()
        result = await kernel.chat(
            user_msg="Hello",
            session_dir=session_dir,
            turn=1,
            conversation=[],
            ui=ui,
        )

        assert isinstance(result, ChatResult)
        assert result.assistant_text == "Hello! How can I help?"
        # FC loop's updated messages contain assistant reply; user msg is via ingester
        assert len(result.conversation) >= 1
        assert result.conversation[-1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_conversation_preserves_history(self, kernel, tmp_path):
        """Previous conversation turns are preserved in result."""
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / "messages").mkdir()

        prior = [
            {"role": "user", "content": "previous question"},
            {"role": "assistant", "content": "previous answer"},
            {"role": "user", "content": "new question"},
        ]

        ui = MagicMock()
        result = await kernel.chat(
            user_msg="new question",
            session_dir=session_dir,
            turn=3,
            conversation=prior,
            ui=ui,
        )

        # Prior conversation should be preserved + new assistant message appended
        assert len(result.conversation) >= 4
        # Old user message still there
        assert any(m.get("content") == "previous question" for m in result.conversation)

    @pytest.mark.asyncio
    async def test_tool_call_integration(self, store, tmp_path):
        """Full flow: mock LLM makes a tool call → tool executed → response returned."""
        agent_root = _make_agent_dir(tmp_path)
        sessions_root = tmp_path / "sessions"
        sessions_root.mkdir()
        config = Config(aide_root=tmp_path / ".aide")

        # Register a real tool that echoes
        tool_registry = ToolRegistry()

        async def _echo_tool(arguments: dict) -> str:
            return f"Echo: {arguments.get('text', '')}"

        echo_tool = ToolDefinition(
            name="echo",
            description="Echoes back text",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            execute=_echo_tool,
        )
        tool_registry.register(echo_tool)

        provider = _mock_provider_with_tool_call(
            tool_name="echo",
            arguments={"text": "hello world"},
            final_text="I called echo and it worked!",
        )

        pipeline = ContextPipeline(agent_root=agent_root)
        ingester = ContextIngester(store, sessions_root=sessions_root)

        ctx = KernelContext(
            config=config,
            provider=provider,
            tooling=ToolingContext(
                tool_registry=tool_registry,
                command_registry=MagicMock(),
                plugin_host=MagicMock(),
                slot_registry=MagicMock(),
            ),
            memory=MemoryContext(
                reflector=MagicMock(),
            ),
            session=SessionContext(
                context_pipeline=pipeline,
                ingester=ingester,
                session_manager=MagicMock(),
            ),
        )
        kernel = AgentKernel(ctx)

        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / "messages").mkdir()

        ui = MagicMock()
        result = await kernel.chat(
            user_msg="echo hello world",
            session_dir=session_dir,
            turn=1,
            conversation=[],
            ui=ui,
        )

        assert result.assistant_text == "I called echo and it worked!"
        # Should have assistant message with tool_calls + tool result in conversation
        has_tool_result = any(
            m.get("role") == "tool" for m in result.conversation
        )
        assert has_tool_result

    @pytest.mark.asyncio
    async def test_ui_callbacks_fired(self, kernel, tmp_path):
        """UI callbacks are invoked during streaming."""
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / "messages").mkdir()

        ui = MagicMock()
        await kernel.chat(
            user_msg="Hello",
            session_dir=session_dir,
            turn=1,
            conversation=[],
            ui=ui,
        )

        # Text tokens should have been streamed
        assert ui.on_text_token.called
        assert ui.on_text_done.called

    @pytest.mark.asyncio
    async def test_ingestion_writes_files(self, kernel, store, tmp_path):
        """After chat, ingester writes turn data to disk."""
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / "messages").mkdir()

        ui = MagicMock()
        await kernel.chat(
            user_msg="Hello",
            session_dir=session_dir,
            turn=1,
            conversation=[],
            ui=ui,
        )

        # Check turn file was written
        turn_file = session_dir / "messages" / "turn_001.json"
        assert turn_file.exists()
        data = json.loads(turn_file.read_text(encoding="utf-8"))
        assert data["turn"] == 1
        # 顶层不再冗余 user/assistant（messages 内完整）
        assert "user" not in data
        assert "assistant" not in data
        assert "tool_calls" not in data
        msgs = data["messages"]
        assert any(
            m.get("role") == "assistant" and "Hello! How can I help?" in m.get("content", "")
            for m in msgs
        )

        # Check timeline was written
        timeline = session_dir / "timeline.json"
        assert timeline.exists()
        from core.storage import read_jsonl
        tl = read_jsonl(timeline)
        assert len(tl) == 1
        assert tl[0]["turn"] == 1

    @pytest.mark.asyncio
    async def test_thinking_persisted_to_turn_file(self, store, tmp_path):
        """思考内容应落盘到 turn 文件，供退出重进后恢复显示。"""
        agent_root = _make_agent_dir(tmp_path)
        sessions_root = tmp_path / "sessions"
        sessions_root.mkdir()
        config = Config(aide_root=tmp_path / ".aide")

        provider = _mock_provider_with_thinking(
            thinking="用户想要一个简洁的脚本，先看现有代码结构。",
            text="我来帮你写。",
        )
        tool_registry = ToolRegistry()
        pipeline = ContextPipeline(agent_root=agent_root)
        ingester = ContextIngester(store, sessions_root=sessions_root)

        ctx = KernelContext(
            config=config,
            provider=provider,
            tooling=ToolingContext(
                tool_registry=tool_registry,
                command_registry=MagicMock(),
                plugin_host=MagicMock(),
                slot_registry=MagicMock(),
            ),
            memory=MemoryContext(reflector=MagicMock()),
            session=SessionContext(
                context_pipeline=pipeline,
                ingester=ingester,
                session_manager=MagicMock(),
            ),
        )
        kernel = AgentKernel(ctx)

        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / "messages").mkdir()

        await kernel.chat(
            user_msg="写个脚本",
            session_dir=session_dir,
            turn=1,
            conversation=[],
            ui=MagicMock(),
        )

        data = json.loads(
            (session_dir / "messages" / "turn_001.json").read_text(encoding="utf-8"))
        assert "用户想要一个简洁的脚本" in data["thinking"]
        # 思考不进对话消息（不进 LLM 上下文）
        assert not any(m.get("content", "").startswith("用户想要一个简洁的脚本")
                       for m in data["messages"])


class TestXmlToolCallPersistence:
    """回归：模型流式输出 <tool_call> XML → 工具执行 + 落盘 content 干净（再次渲染无乱码）。"""

    @pytest.mark.asyncio
    async def test_xml_tool_call_saved_clean(self, tmp_path):
        store = await _make_store()
        try:
            await self._run(store, tmp_path)
        finally:
            await store.close()

    async def _run(self, store, tmp_path):
        agent_root = _make_agent_dir(tmp_path)
        sessions_root = tmp_path / "sessions"
        sessions_root.mkdir()
        config = Config(aide_root=tmp_path / ".aide")

        provider = AsyncMock()
        call = [0]

        async def _mock_chat(messages, tools):
            call[0] += 1
            if call[0] == 1:
                yield TextDelta(content="开始执行。")
                yield TextDelta(content="\n<tool_call><function=echo><parameter=msg>hi</parameter></function></tool_call>")
            else:
                yield TextDelta(content="完成")
            yield StreamEnd(finish_reason="stop", tool_calls=[])

        provider.chat_with_tools = _mock_chat

        tool_registry = ToolRegistry()

        async def echo(args):
            return "echo: " + args.get("msg", "")
        tool_registry.register(ToolDefinition(
            name="echo", description="Echo", parameters={"type": "object", "properties": {}},
            execute=echo,
        ))

        pipeline = ContextPipeline(agent_root=agent_root)
        ingester = ContextIngester(store, sessions_root=sessions_root)
        kernel_ctx = KernelContext(
            config=config,
            provider=provider,
            tooling=ToolingContext(
                tool_registry=tool_registry, command_registry=MagicMock(),
                plugin_host=MagicMock(), slot_registry=MagicMock(),
            ),
            memory=MemoryContext(reflector=MagicMock()),
            session=SessionContext(
                context_pipeline=pipeline, ingester=ingester, session_manager=MagicMock(),
            ),
        )
        kernel = AgentKernel(kernel_ctx)

        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / "messages").mkdir()

        ui = MagicMock()
        await kernel.chat(user_msg="go", session_dir=session_dir, turn=1,
                          conversation=[{"role": "user", "content": "go"}], ui=ui)

        # 落盘 turn 文件必须干净（无 <tool_call>/<function=）
        turn_file = session_dir / "messages" / "turn_001.json"
        data = json.loads(turn_file.read_text(encoding="utf-8"))
        msgs = data.get("messages", [])
        dirty = [m for m in msgs
                 if isinstance(m.get("content"), str)
                 and ("<tool_call>" in m["content"] or "<function=" in m["content"])]
        assert dirty == [], f"落盘消息残留 XML: {[m['content'][:60] for m in dirty]}"

        # tool_calls 消息存在、content 干净
        tc_msgs = [m for m in msgs if m.get("tool_calls")]
        assert tc_msgs, "应有带 tool_calls 的消息"
        assert "<tool_call>" not in tc_msgs[0]["content"]
        assert tc_msgs[0]["tool_calls"][0]["function"]["name"] == "echo"
