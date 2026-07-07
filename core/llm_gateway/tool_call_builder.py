"""共享工具调用构建器 — OpenAI + Anthropic SSE 流共用。

两个 SSE 解析器各自累积 tool call 片段（按 index 分组），
最终通过 build_tool_calls() 统一组装为 OpenAI 格式的 tool_calls 列表。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class ToolCallAccumulator:
    """累积单个 tool call 的 SSE 流片段。

    OpenAI SSE: delta.tool_calls[{index, function: {name, arguments}}] 分片到达
    Anthropic SSE: content_block_start + content_block_delta(input_json_delta) 分片到达
    """

    id: str = ""
    name: str = ""
    arguments_str: str = ""

    def to_tool_call(self) -> dict:
        """组装为 OpenAI 格式的 tool_call dict。

        Returns:
            {"id": "...", "type": "function",
             "function": {"name": "...", "arguments": "{...}"}}
        """
        try:
            args = json.loads(self.arguments_str) if self.arguments_str.strip() else {}
        except json.JSONDecodeError:
            args = {}  # JSON 畸形时降级为空对象，避免循环中断
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        }


def build_tool_calls(
    accumulators: dict[int, ToolCallAccumulator],
) -> list[dict]:
    """从 index → accumulator 映射构建排序后的 tool_calls 列表。

    Args:
        accumulators: {index: ToolCallAccumulator}

    Returns:
        OpenAI 格式的 tool_calls 列表，按 index 升序排列
    """
    return [
        acc.to_tool_call()
        for _, acc in sorted(accumulators.items())
    ]
