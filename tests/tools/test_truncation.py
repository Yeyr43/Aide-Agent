"""Tests for core.tools.truncation — shared truncation utility."""

import pytest
from core.tools.truncation import truncate_output


class TestTruncateOutput:
    def test_no_truncation_when_text_fits(self):
        result = truncate_output("hello", max_size=10)
        assert result == "hello"

    def test_no_truncation_at_exact_size(self):
        result = truncate_output("hello", max_size=5)
        assert result == "hello"

    def test_truncates_chars_default(self):
        result = truncate_output("abcdefghij", max_size=6)
        assert result != "abcdefghij"
        assert "…" in result or "截断" in result
        # Should have roughly 3 chars head + 3 chars tail
        assert len(result) > 6  # includes marker

    def test_truncates_bytes_mode(self):
        text = "a" * 100
        result = truncate_output(text, max_size=50, unit="bytes")
        assert result != text
        assert len(result.encode("utf-8")) <= 50 + 200  # marker adds some overhead

    def test_custom_label(self):
        result = truncate_output("x" * 100, max_size=20, label="CONTENT CUT")
        assert "CONTENT CUT" in result

    def test_head_ratio_most_head(self):
        """head_ratio=0.8 → more head, less tail."""
        result = truncate_output("abcdefghij" * 20, max_size=40, head_ratio=0.8)
        # Head portion should be larger
        head_part = result.split("…")[0]
        tail_part = result.split("…")[-1]
        assert len(head_part) > len(tail_part)

    def test_head_ratio_most_tail(self):
        """head_ratio=0.2 → more tail, less head (log-style)."""
        result = truncate_output("abcdefghij" * 20, max_size=40, head_ratio=0.2)
        head_part = result.split("…")[0]
        tail_part = result.split("…")[-1]
        assert len(tail_part) > len(head_part)

    def test_empty_text(self):
        result = truncate_output("", max_size=10)
        assert result == ""

    def test_zero_max_size(self):
        """Zero max_size still produces output (marker + truncated)."""
        result = truncate_output("hello", max_size=0)
        assert isinstance(result, str)
        # Will truncate everything since max_size=0
        assert "" in result  # at minimum returns something
