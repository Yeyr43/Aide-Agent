"""XML 工具调用解析 — fallback for 不支持原生 function calling 的模型。

兼容 Claude/Anthropic 格式的 XML invoke 语法：
  <invoke name="tool_name">
    <parameter name="arg1">value1</parameter>
    <parameter name="arg2">value2</parameter>
  </invoke>

解析结果是标准化工具调用列表，格式与原生 function calling 一致。
"""

from __future__ import annotations

import json
import re

# ── XML 模式 ──────────────────────────────────────────────────────────

_XML_INVOKE_RE = re.compile(
    r'<invoke\s+name="(\w+)"[^>]*>(.*?)</invoke>',
    re.DOTALL,
)
_XML_PARAM_RE = re.compile(
    r'<parameter\s+name="(\w+)"[^>]*>(.*?)</parameter>',
    re.DOTALL,
)


# ── 提取 ──────────────────────────────────────────────────────────────

def extract_xml_tool_calls(text: str) -> list[dict]:
    """从文本中提取 XML 格式的工具调用。

    Args:
        text: LLM 原始输出文本（可能含 <invoke> XML 块）

    Returns:
        标准化的 tool_calls 列表，格式与 OpenAI function calling 一致：
        [{"id": "xml_0", "type": "function",
          "function": {"name": "...", "arguments": "{...}"}}]
    """
    calls: list[dict] = []
    for i, match in enumerate(_XML_INVOKE_RE.finditer(text)):
        tool_name = match.group(1)
        params_block = match.group(2)

        args: dict[str, str] = {}
        for pm in _XML_PARAM_RE.finditer(params_block):
            args[pm.group(1)] = pm.group(2).strip()

        calls.append({
            "id": f"xml_{i}",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        })

    return calls


def try_parse_xml(text: str) -> tuple[str, list[dict]]:
    """从 LLM 输出中分离文本内容和 XML 工具调用。

    Args:
        text: LLM 原始输出文本

    Returns:
        (clean_text, tool_calls)
        clean_text: 剥离 XML 后的纯文本（可能为空）
        tool_calls: 标准化的 tool_calls 列表（可能为空）
    """
    xml_start = text.find("<invoke")
    if xml_start < 0:
        return text, []

    clean = text[:xml_start].strip()
    tool_calls = extract_xml_tool_calls(text)
    return clean, tool_calls
