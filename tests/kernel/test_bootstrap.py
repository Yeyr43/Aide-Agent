"""Tests for AppBootstrap — composition root."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from core.kernel.bootstrap import AppBootstrap, BootstrapResult


class TestBootstrapResult:
    def test_dataclass_fields(self, tmp_path):
        result = BootstrapResult(
            config=MagicMock(),
            provider=MagicMock(),
            model_name="gpt-4o",
            tool_registry=MagicMock(),
            mcp_adapter=MagicMock(),
            cmd_registry=MagicMock(),
            ingester=MagicMock(),
            pipeline=MagicMock(),
            kernel=MagicMock(),
            store=MagicMock(),
            errors=[],
        )
        assert result.model_name == "gpt-4o"
        assert result.config is not None
        assert result.provider is not None
        assert result.kernel is not None


class TestAppBootstrap:
    @pytest.mark.asyncio
    async def test_init_returns_bootstrap_result(self, tmp_path):
        """Verify init() returns a valid BootstrapResult with mocked dependencies."""
        aide_root = tmp_path / ".aide"
        aide_root.mkdir(parents=True)
        (aide_root / "agent").mkdir()

        with patch("core.kernel.bootstrap.Config") as mock_cfg, \
             patch("core.kernel.bootstrap.create_provider") as mock_prov, \
             patch("core.kernel.bootstrap.JsonStore") as mock_store, \
             patch("core.kernel.bootstrap.ToolRegistry") as mock_tr, \
             patch("core.kernel.bootstrap.register_builtin_tools"), \
             patch("core.kernel.bootstrap.MCPAdapter") as mock_mcp, \
             patch("core.kernel.bootstrap.CommandRegistry") as mock_cr, \
             patch("core.kernel.bootstrap.ContextPipeline") as mock_pipe, \
             patch("core.kernel.bootstrap.ContextIngester") as mock_ingest, \
             patch("core.kernel.bootstrap.ContextCompactor") as mock_comp, \
             patch("core.kernel.bootstrap.CaptureEngine") as mock_ce, \
             patch("core.kernel.bootstrap.EntryManager") as mock_em, \
             patch("core.kernel.bootstrap.PromptUpdater") as mock_pu, \
             patch("core.kernel.bootstrap.TopicFrequencyTracker") as mock_tt, \
             patch("core.kernel.bootstrap.SlotRegistry") as mock_sr, \
             patch("core.kernel.bootstrap.PluginHost") as mock_ph, \
             patch("core.kernel.bootstrap.SessionManager") as mock_sm, \
             patch("core.kernel.bootstrap.AgentKernel") as mock_ak:

            # Config mock
            mock_cfg.load.return_value = MagicMock(
                aide_root=aide_root,
                sessions_root=aide_root / "sessions",
                llm=MagicMock(model="gpt-4o", provider="openai"),
            )
            # Provider mock
            mock_prov.return_value = MagicMock()
            # MCP mock
            mock_mcp.return_value = MagicMock()
            mock_mcp.return_value.load_builtin_servers = AsyncMock(return_value=0)
            mock_mcp.return_value.discover_all_tools = AsyncMock(return_value=[])
            mock_mcp.return_value.start_watcher = MagicMock()
            mock_mcp.return_value.connected_servers = []
            # Store mock
            mock_store.return_value = MagicMock()
            mock_store.return_value.start = AsyncMock()
            # Command registry mock
            mock_cr.return_value.list_all.return_value = []
            # Pipeline mock
            mock_pipe.return_value = MagicMock()
            # Kernel mock
            mock_ak.return_value = MagicMock()
            # Other mocks
            mock_ingest.return_value = MagicMock()
            mock_comp.return_value = MagicMock()
            mock_em.return_value = MagicMock()
            mock_tt.return_value = MagicMock()
            mock_ce.return_value = MagicMock()
            mock_pu.return_value = MagicMock()
            mock_sr.return_value = MagicMock()
            mock_sm.return_value = MagicMock()
            mock_ph.return_value = MagicMock()

            result = await AppBootstrap.init()
            assert isinstance(result, BootstrapResult)
            assert result.model_name is not None
            assert result.kernel is not None

    @pytest.mark.asyncio
    async def test_provider_failure_degrades_gracefully(self, tmp_path):
        """If provider creation fails, bootstrap should still succeed with None provider."""
        aide_root = tmp_path / ".aide"
        aide_root.mkdir(parents=True)
        (aide_root / "agent").mkdir()

        with patch("core.kernel.bootstrap.Config") as mock_cfg, \
             patch("core.kernel.bootstrap.create_provider") as mock_prov, \
             patch("core.kernel.bootstrap.JsonStore") as mock_store, \
             patch("core.kernel.bootstrap.ToolRegistry"), \
             patch("core.kernel.bootstrap.register_builtin_tools"), \
             patch("core.kernel.bootstrap.MCPAdapter") as mock_mcp, \
             patch("core.kernel.bootstrap.CommandRegistry"), \
             patch("core.kernel.bootstrap.ContextPipeline"), \
             patch("core.kernel.bootstrap.ContextIngester"), \
             patch("core.kernel.bootstrap.ContextCompactor"), \
             patch("core.kernel.bootstrap.CaptureEngine"), \
             patch("core.kernel.bootstrap.EntryManager"), \
             patch("core.kernel.bootstrap.PromptUpdater"), \
             patch("core.kernel.bootstrap.TopicFrequencyTracker"), \
             patch("core.kernel.bootstrap.SlotRegistry"), \
             patch("core.kernel.bootstrap.PluginHost"), \
             patch("core.kernel.bootstrap.SessionManager"), \
             patch("core.kernel.bootstrap.AgentKernel"):

            mock_cfg.load.return_value = MagicMock(
                aide_root=aide_root,
                sessions_root=aide_root / "sessions",
                llm=MagicMock(model="unknown-model", provider="openai"),
            )
            mock_prov.side_effect = ValueError("API key missing")
            mock_mcp.return_value = MagicMock()
            mock_mcp.return_value.load_builtin_servers = AsyncMock(return_value=0)
            mock_mcp.return_value.discover_all_tools = AsyncMock(return_value=[])
            mock_mcp.return_value.start_watcher = MagicMock()
            mock_mcp.return_value.connected_servers = []
            mock_store.return_value = MagicMock()
            mock_store.return_value.start = AsyncMock()

            result = await AppBootstrap.init()
            assert isinstance(result, BootstrapResult)
            assert result.provider is None
            assert result.model_name == "未配置"
