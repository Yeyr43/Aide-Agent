"""Tests for backward compatibility layer — route_command."""

import pytest

from core.commands.builtin._compat import route_command


class TestRouteCommand:
    def test_not_command(self):
        assert route_command("hello") is None
        assert route_command("/") is None

    def test_exact_match(self):
        result = route_command("/help")
        assert result is not None
        handler, args = result
        assert callable(handler)
        assert args == ""

    def test_prefix_match(self):
        result = route_command("/hel")
        assert result is not None

    def test_with_args(self):
        result = route_command("/import test.zip")
        assert result is not None
        handler, args = result
        assert args == "test.zip"

    def test_rollback_with_args(self):
        result = route_command("/rollback 3")
        assert result is not None
        handler, args = result
        assert args == "3"
