"""测试 xml_tool_parser.py — XML fallback 工具调用解析。"""

import json

from core.kernel.xml_tool_parser import extract_xml_tool_calls, try_parse_xml


class TestExtractXmlToolCalls:
    """测试 XML 工具调用提取。"""

    def test_single_tool_call(self):
        text = '<invoke name="read_file"><parameter name="file_path">/tmp/test.txt</parameter></invoke>'
        calls = extract_xml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["id"] == "xml_0"
        assert calls[0]["type"] == "function"
        assert calls[0]["function"]["name"] == "read_file"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args["file_path"] == "/tmp/test.txt"

    def test_multiple_tool_calls(self):
        text = (
            '<invoke name="read_file"><parameter name="file_path">a.txt</parameter></invoke>\n'
            '<invoke name="read_file"><parameter name="file_path">b.txt</parameter></invoke>'
        )
        calls = extract_xml_tool_calls(text)
        assert len(calls) == 2
        assert calls[0]["id"] == "xml_0"
        assert calls[1]["id"] == "xml_1"

    def test_multiple_parameters(self):
        text = '<invoke name="run_shell"><parameter name="command">ls</parameter><parameter name="cwd">/tmp</parameter></invoke>'
        calls = extract_xml_tool_calls(text)
        assert len(calls) == 1
        args = json.loads(calls[0]["function"]["arguments"])
        assert args["command"] == "ls"
        assert args["cwd"] == "/tmp"

    def test_no_tool_calls(self):
        text = "This is just a normal response without any tool calls."
        calls = extract_xml_tool_calls(text)
        assert calls == []

    def test_empty_text(self):
        assert extract_xml_tool_calls("") == []

    def test_malformed_xml_ignored(self):
        """不完整 XML 应被跳过（正则需要完整闭合标签）。"""
        text = '<invoke name="tool"><parameter name="arg">incomplete'
        calls = extract_xml_tool_calls(text)
        assert calls == []  # 没闭合，不匹配

    def test_tool_name_with_underscores(self):
        text = '<invoke name="search_memory"><parameter name="query">test</parameter></invoke>'
        calls = extract_xml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "search_memory"

    def test_nested_xml_content_in_params(self):
        """参数值中有 XML-like 内容不应影响解析。"""
        text = '<invoke name="write_file"><parameter name="content"><html>hello</html></parameter></invoke>'
        calls = extract_xml_tool_calls(text)
        assert len(calls) == 1
        args = json.loads(calls[0]["function"]["arguments"])
        assert "<html>hello</html>" in args["content"]

    def test_xml_with_extra_attributes(self):
        """invoke 标签有额外属性时仍能解析。"""
        text = '<invoke name="search" id="call_1"><parameter name="query">test</parameter></invoke>'
        calls = extract_xml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "search"


class TestTryParseXml:
    """测试 try_parse_xml — 分离文本和工具调用。"""

    def test_text_before_xml(self):
        text = "Let me search for that.\n<invoke name=\"search\"><parameter name=\"query\">test</parameter></invoke>"
        clean, calls = try_parse_xml(text)
        assert clean == "Let me search for that."
        assert len(calls) == 1

    def test_only_xml(self):
        text = '<invoke name="read_file"><parameter name="file_path">/tmp/a.txt</parameter></invoke>'
        clean, calls = try_parse_xml(text)
        assert clean == ""
        assert len(calls) == 1

    def test_only_text(self):
        text = "This is a simple text response."
        clean, calls = try_parse_xml(text)
        assert clean == text
        assert calls == []

    def test_empty(self):
        clean, calls = try_parse_xml("")
        assert clean == ""
        assert calls == []

    def test_multiple_invoke_separated_by_text(self):
        text = (
            'First call: <invoke name="read_file"><parameter name="file_path">a.txt</parameter></invoke>\n'
            'Second call: <invoke name="read_file"><parameter name="file_path">b.txt</parameter></invoke>'
        )
        clean, calls = try_parse_xml(text)
        assert clean == "First call:"
        assert len(calls) == 2
