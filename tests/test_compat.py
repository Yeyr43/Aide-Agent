"""Tests for backward compatibility layer — COMMANDS dict + route_command + _cmd."""

import pytest
from unittest.mock import MagicMock

from core.commands.builtin._compat import (
    AIDE_ROOT,
    AGENT_ROOT,
    COMMANDS,
    _register_to_commands,
    route_command,
    _cmd,
)


class TestCompatPaths:
    def test_aide_root_is_in_home(self):
        assert AIDE_ROOT.name == ".aide"
        assert AIDE_ROOT.parent == __import__("pathlib").Path.home()

    def test_agent_root_is_under_aide(self):
        assert AGENT_ROOT.name == "agent"
        assert AGENT_ROOT.parent == AIDE_ROOT


class TestRegisterToCommands:
    def setup_method(self):
        COMMANDS.clear()

    def teardown_method(self):
        COMMANDS.clear()

    def test_registers_handler(self):
        async def dummy(app, args):
            return "ok"

        _register_to_commands("test", dummy, "Test command")
        assert "/test" in COMMANDS
        handler, desc = COMMANDS["/test"]
        assert desc == "Test command"

    def test_overwrites_existing(self):
        async def dummy1(app, args):
            return "1"

        async def dummy2(app, args):
            return "2"

        _register_to_commands("test", dummy1, "First")
        _register_to_commands("test", dummy2, "Second")
        handler, desc = COMMANDS["/test"]
        assert desc == "Second"


class TestCmdDecorator:
    def setup_method(self):
        COMMANDS.clear()

    def teardown_method(self):
        COMMANDS.clear()

    def test_registers_command(self):
        @_cmd("test", "A test command")
        async def handler(app, args):
            return "ok"

        assert "/test" in COMMANDS
        _, desc = COMMANDS["/test"]
        assert desc == "A test command"

    def test_handler_still_works(self):
        @_cmd("greet", "Greeting")
        async def handler(app, args):
            return "hello"

        result = __import__("asyncio").run(handler(None, ""))
        assert result == "hello"
