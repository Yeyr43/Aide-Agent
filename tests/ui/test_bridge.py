"""Tests for UIBridge — kernel ↔ Textual bridge layer."""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

from ui.textual_app.bridge import UIBridge


class TestUIBridgeInit:
    def test_init_stores_app(self):
        app = MagicMock()
        bridge = UIBridge(app)
        assert bridge._app is app
        assert bridge._last_ai_text == ""

    def test_reset_text(self):
        app = MagicMock()
        bridge = UIBridge(app)
        bridge._last_ai_text = "some text"
        bridge.reset_text()
        assert bridge._last_ai_text == ""


class TestUIBridgeTextHandling:
    @pytest.fixture
    def bridge_with_mock_msg_list(self):
        """Create a bridge with a mocked MessageList widget."""
        app = MagicMock()
        mock_msg_list = MagicMock()
        app.query_one.return_value = mock_msg_list
        bridge = UIBridge(app)
        return bridge, mock_msg_list

    def test_on_text_token_accumulates(self, bridge_with_mock_msg_list):
        bridge, msg_list = bridge_with_mock_msg_list
        bridge.on_text_token("Hello")
        bridge.on_text_token(" World")
        assert bridge._last_ai_text == "Hello World"
        assert msg_list.add_ai_chunk.call_count == 2

    def test_on_text_done_finishes_message(self, bridge_with_mock_msg_list):
        bridge, msg_list = bridge_with_mock_msg_list
        msg_list.has_pending.return_value = True
        msg_list.finish_ai_message.return_value = "Final text"

        bridge.on_text_done()
        msg_list.finish_ai_message.assert_called_once()

    def test_on_text_done_no_pending(self, bridge_with_mock_msg_list):
        bridge, msg_list = bridge_with_mock_msg_list
        msg_list.has_pending.return_value = False

        bridge.on_text_done()
        msg_list.finish_ai_message.assert_not_called()


class TestUIBridgeToolEvents:
    @pytest.fixture
    def bridge_with_mock_msg_list(self):
        app = MagicMock()
        mock_msg_list = MagicMock()
        app.query_one.return_value = mock_msg_list
        bridge = UIBridge(app)
        return bridge, mock_msg_list

    def test_tool_start_forwards_to_msg_list(self, bridge_with_mock_msg_list):
        bridge, msg_list = bridge_with_mock_msg_list
        bridge.on_tool_start("read_file", {"path": "/tmp/x"})
        msg_list.add_tool_start.assert_called_once_with(
            "read_file", {"path": "/tmp/x"}
        )

    def test_tool_done_forwards_to_msg_list(self, bridge_with_mock_msg_list):
        bridge, msg_list = bridge_with_mock_msg_list
        bridge.on_tool_done("read_file", "content")
        msg_list.add_tool_done.assert_called_once_with("read_file", "content")

    def test_tool_error_forwards_to_msg_list(self, bridge_with_mock_msg_list):
        bridge, msg_list = bridge_with_mock_msg_list
        bridge.on_tool_error("read_file", "Permission denied")
        msg_list.add_tool_error.assert_called_once_with("read_file", "Permission denied")

    def test_max_turns_notice(self, bridge_with_mock_msg_list):
        bridge, msg_list = bridge_with_mock_msg_list
        bridge.on_max_turns()
        msg_list.add_system_notice.assert_called_once()
        call_arg = msg_list.add_system_notice.call_args[0][0]
        assert "5" in call_arg  # max turns


class TestUIBridgeStreamingReplace:
    @pytest.fixture
    def bridge_with_mock_msg_list(self):
        app = MagicMock()
        mock_msg_list = MagicMock()
        app.query_one.return_value = mock_msg_list
        bridge = UIBridge(app)
        return bridge, mock_msg_list

    def test_replace_streamed_text(self, bridge_with_mock_msg_list):
        bridge, msg_list = bridge_with_mock_msg_list
        bridge.on_replace_streamed_text("clean text without xml")
        msg_list.replace_streamed_text.assert_called_once_with(
            "clean text without xml"
        )


