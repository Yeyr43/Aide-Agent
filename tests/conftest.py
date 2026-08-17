"""共享测试 fixtures — 伪 LLM provider + 最小 agent 目录。

供集成测试复用，避免每个文件重复内联 helper。
命名加 aide_ 前缀避免与各测试文件的局部 fixture 冲突。
"""

from unittest.mock import AsyncMock

import pytest

from core.llm_gateway import TextDelta, ThinkingDelta, StreamEnd


def _text_delta(content: str) -> TextDelta:
    return TextDelta(content=content)


def _stream_end(finish_reason: str = "stop", tool_calls: list | None = None) -> StreamEnd:
    return StreamEnd(finish_reason=finish_reason, tool_calls=tool_calls or [])


@pytest.fixture
def aide_agent_dir(tmp_path):
    """最小 agent 目录：soul.md + 空数据文件。

    与冷启动判断兼容（soul 存在 → 非 cold start）。
    """
    agent_root = tmp_path / "agent"
    agent_root.mkdir(parents=True)
    (agent_root / "soul.md").write_text(
        "# Test Soul\nBe helpful and concise.", encoding="utf-8")
    (agent_root / "data").mkdir(exist_ok=True)
    for fname in ["preferences.json", "workflows.json", "long_term_memory.json"]:
        (agent_root / "data" / fname).write_text("[]", encoding="utf-8")
    return agent_root


@pytest.fixture
def aide_fake_provider():
    """伪 LLM provider：每次调用流式返回一段固定文本。"""
    provider = AsyncMock()
    provider.supports_vision = False

    async def _chat(messages, tools):
        yield _text_delta("Hello from fake provider!")
        yield _stream_end()

    provider.chat_with_tools = _chat
    return provider


@pytest.fixture
def aide_fake_provider_with_tool_call():
    """伪 LLM provider：第一次调用发一个工具调用，之后返回文本。

    用法：在测试里给返回的 provider 配置 `tool_name` / `arguments` / `final_text`。
    """

    def _build(tool_name: str = "echo", arguments: dict | None = None,
               final_text: str = "Done!"):
        import json
        provider = AsyncMock()
        provider.supports_vision = False
        call_count = [0]
        arguments = arguments or {}

        async def _chat(messages, tools):
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

        provider.chat_with_tools = _chat
        return provider

    return _build
