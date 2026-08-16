"""XML 工具调用解析 — fallback for 不支持原生 function calling 的模型。

兼容两种 XML 方言：
1. <invoke name="tool_name"><parameter name="arg1">value1</parameter></invoke>
2. Claude Code/OpenClaw 风格：
     <tool_call>
       <function=run_shell>
         <parameter=command>...</parameter>
       </function>
     </tool_call>

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
_XML_TOOLCALL_RE = re.compile(
    r'<tool_call[^>]*>(.*?)</tool_call>',
    re.DOTALL,
)
_XML_FUNCTION_RE = re.compile(
    r'<function\b(?:=|\s+name=)["\']?(\w+)["\']?[^>]*>',
    re.DOTALL,
)
# 参数名：<parameter name="x"> 或 <parameter=x> 两种写法都兼容
_XML_PARAM_RE = re.compile(
    r'<parameter(?:\s+name=|=)["\']?(\w+)["\']?[^>]*>(.*?)</parameter>',
    re.DOTALL,
)


# ── 提取 ──────────────────────────────────────────────────────────────

def find_xml_start(text: str) -> int:
    """最早出现的 XML 工具调用标记位置（<invoke 或 <tool_call），无则 -1。"""
    positions = [p for p in (text.find("<invoke"), text.find("<tool_call")) if p >= 0]
    return min(positions) if positions else -1


def _iter_xml_blocks(text: str):
    """迭代所有 XML 工具块，产出 (params_block, tool_name)。兼容两种方言。"""
    for m in _XML_INVOKE_RE.finditer(text):
        yield m.group(2), m.group(1)
    for m in _XML_TOOLCALL_RE.finditer(text):
        block = m.group(1)
        fm = _XML_FUNCTION_RE.search(block)
        if fm:
            yield block, fm.group(1)


def extract_xml_tool_calls(text: str) -> list[dict]:
    """从文本中提取 XML 格式的工具调用（兼容 <invoke> 与 <tool_call> 两种方言）。

    Args:
        text: LLM 原始输出文本（可能含 XML 工具调用块）

    Returns:
        标准化的 tool_calls 列表，格式与 OpenAI function calling 一致：
        [{"id": "xml_0", "type": "function",
          "function": {"name": "...", "arguments": "{...}"}}]
    """
    calls: list[dict] = []
    for i, (params_block, tool_name) in enumerate(_iter_xml_blocks(text)):
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


def strip_xml_tool_blocks(text: str) -> str:
    """从文本中移除 XML 工具调用块（<invoke>…</invoke> 与 <tool_call>…</tool_call>）。

    用于渲染/恢复时清理旧数据中残留的 XML 乱码（修复前的 turn 文件可能把
    未提取的 XML 工具块写进了 assistant content）。
    """
    if "<invoke" not in text and "<tool_call" not in text:
        return text
    result = _XML_INVOKE_RE.sub("", text)
    result = _XML_TOOLCALL_RE.sub("", result)
    return result.strip()


def try_parse_xml(text: str) -> tuple[str, list[dict]]:
    """从 LLM 输出中分离文本内容和 XML 工具调用。

    Args:
        text: LLM 原始输出文本

    Returns:
        (clean_text, tool_calls)
        clean_text: 剥离 XML 后的纯文本（可能为空）
        tool_calls: 标准化的 tool_calls 列表（可能为空）
    """
    xml_start = find_xml_start(text)
    if xml_start < 0:
        return text, []

    clean = text[:xml_start].strip()
    tool_calls = extract_xml_tool_calls(text)
    return clean, tool_calls
