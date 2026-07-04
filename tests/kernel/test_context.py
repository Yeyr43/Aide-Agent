"""Tests for KernelContext — dependency aggregation dataclass."""

import pytest
from unittest.mock import MagicMock
from pathlib import Path

from core.kernel.context import KernelContext
from core.config import Config


def _make_mock_ctx(tmp_path):
    """Build a fully populated KernelContext with mocks."""
    config = Config(aide_root=tmp_path / ".aide")
    return KernelContext(
        config=config,
        provider=MagicMock(),
        tool_registry=MagicMock(),
        command_registry=MagicMock(),
        context_pipeline=MagicMock(),
        ingester=MagicMock(),
        compactor=MagicMock(),
        session_manager=MagicMock(),
        capture_engine=MagicMock(),
        entry_manager=MagicMock(),
        prompt_updater=MagicMock(),
        topic_tracker=MagicMock(),
        plugin_host=MagicMock(),
        slot_registry=MagicMock(),
    )


class TestKernelContext:
    def test_all_fields_accessible(self, tmp_path):
        ctx = _make_mock_ctx(tmp_path)
        assert ctx.config is not None
        assert ctx.provider is not None
        assert ctx.tool_registry is not None
        assert ctx.command_registry is not None
        assert ctx.context_pipeline is not None
        assert ctx.ingester is not None
        assert ctx.compactor is not None
        assert ctx.session_manager is not None
        assert ctx.capture_engine is not None
        assert ctx.entry_manager is not None
        assert ctx.prompt_updater is not None
        assert ctx.topic_tracker is not None
        assert ctx.plugin_host is not None
        assert ctx.slot_registry is not None

    def test_config_preserves_aide_root(self, tmp_path):
        ctx = _make_mock_ctx(tmp_path)
        assert ctx.config.aide_root == tmp_path / ".aide"

    def test_is_dataclass(self, tmp_path):
        ctx = _make_mock_ctx(tmp_path)
        # dataclass repr includes field values
        assert "KernelContext" in repr(ctx)
