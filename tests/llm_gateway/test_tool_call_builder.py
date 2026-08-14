"""Tests for core.llm_gateway.tool_call_builder — SSE tool call accumulator."""

import pytest
import json

from core.llm_gateway.tool_call_builder import ToolCallAccumulator, build_tool_calls


class TestToolCallAccumulator:
    def test_empty_accumulator(self):
        acc = ToolCallAccumulator()
        result = acc.to_tool_call()
        assert result["type"] == "function"
        assert result["function"]["name"] == ""
        assert result["function"]["arguments"] == "{}"

    def test_with_valid_json_arguments(self):
        acc = ToolCallAccumulator(
            id="call_1",
            name="echo",
            arguments_str='{"text": "hello world"}',
        )
        result = acc.to_tool_call()
        assert result["id"] == "call_1"
        assert result["type"] == "function"
        assert result["function"]["name"] == "echo"
        assert json.loads(result["function"]["arguments"]) == {"text": "hello world"}

    def test_malformed_json_falls_back_to_empty(self):
        acc = ToolCallAccumulator(
            id="call_2",
            name="bad_tool",
            arguments_str="not valid json{{{{",
        )
        result = acc.to_tool_call()
        # Should not raise — falls back to empty dict
        assert result["function"]["arguments"] == "{}"

    def test_empty_arguments_string(self):
        acc = ToolCallAccumulator(
            id="call_3",
            name="no_args_tool",
            arguments_str="",
        )
        result = acc.to_tool_call()
        assert json.loads(result["function"]["arguments"]) == {}

    def test_whitespace_arguments(self):
        acc = ToolCallAccumulator(
            id="call_4",
            name="whitespace",
            arguments_str="   ",
        )
        result = acc.to_tool_call()
        assert json.loads(result["function"]["arguments"]) == {}


class TestBuildToolCalls:
    def test_empty_accumulators(self):
        result = build_tool_calls({})
        assert result == []

    def test_single_accumulator(self):
        acc = ToolCallAccumulator(
            id="call_1",
            name="echo",
            arguments_str='{"x": 1}',
        )
        result = build_tool_calls({0: acc})
        assert len(result) == 1
        assert result[0]["id"] == "call_1"

    def test_multiple_accumulators_sorted_by_index(self):
        """Output should be sorted by index ascending."""
        acc1 = ToolCallAccumulator(id="call_1", name="tool_1", arguments_str='{"a": 1}')
        acc2 = ToolCallAccumulator(id="call_2", name="tool_2", arguments_str='{"b": 2}')
        acc3 = ToolCallAccumulator(id="call_3", name="tool_3", arguments_str='{"c": 3}')

        # Insert out of order
        result = build_tool_calls({2: acc3, 0: acc1, 1: acc2})
        assert len(result) == 3
        assert result[0]["id"] == "call_1"
        assert result[1]["id"] == "call_2"
        assert result[2]["id"] == "call_3"

    def test_preserves_arguments_accuracy(self):
        """Round-trip: original args should survive accumulation."""
        original = {"text": "hello", "count": 42, "nested": {"key": "value"}}
        acc = ToolCallAccumulator(
            id="call_x",
            name="complex",
            arguments_str=json.dumps(original),
        )
        result = build_tool_calls({0: acc})
        restored = json.loads(result[0]["function"]["arguments"])
        assert restored == original
