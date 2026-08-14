"""Tests for KernelContext — dependency aggregation dataclass."""

import pytest
from unittest.mock import MagicMock
from pathlib import Path

from core.kernel.context import KernelContext, MemoryContext, ToolingContext, SessionContext
from core.config import Config


def _make_mock_ctx(tmp_path):
    """Build a fully populated KernelContext with mocks."""
    config = Config(aide_root=tmp_path / ".aide")
    return KernelContext(
        config=config,
        provider=MagicMock(),
        tooling=ToolingContext(
            tool_registry=MagicMock(),
            command_registry=MagicMock(),
            plugin_host=MagicMock(),
            slot_registry=MagicMock(),
        ),
        memory=MemoryContext(
            reflector=MagicMock(),
        ),
        session=SessionContext(
            context_pipeline=MagicMock(),
            ingester=MagicMock(),
            session_manager=MagicMock(),
        ),
    )


class TestKernelContext:
    def test_all_fields_accessible(self, tmp_path):
        ctx = _make_mock_ctx(tmp_path)
        assert ctx.config is not None
        assert ctx.provider is not None
        assert ctx.tooling.tool_registry is not None
        assert ctx.tooling.command_registry is not None
        assert ctx.session.context_pipeline is not None
        assert ctx.session.ingester is not None
        assert ctx.session.session_manager is not None
        assert ctx.memory.reflector is not None
        assert ctx.tooling.plugin_host is not None
        assert ctx.tooling.slot_registry is not None

    def test_config_preserves_aide_root(self, tmp_path):
        ctx = _make_mock_ctx(tmp_path)
        assert ctx.config.aide_root == tmp_path / ".aide"

    def test_is_dataclass(self, tmp_path):
        ctx = _make_mock_ctx(tmp_path)
        # dataclass repr includes field values
        assert "KernelContext" in repr(ctx)
