"""AppBootstrap — 应用组合根。

将 on_mount 中的组件初始化逻辑提取为独立服务。
单一职责：创建所有组件并注入 AgentKernel。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.config import Config
from core.llm_gateway import create_provider
from core.storage import JsonStore
from core.tools import ToolRegistry
from core.tools.discovery import register_builtin_tools
from core.tools.mcp import MCPAdapter
from core.commands import CommandRegistry
from core.context import ContextPipeline, ContextIngester, ContextCompactor
from core.memory import CaptureEngine, EntryManager, PromptUpdater, TopicFrequencyTracker
from core.plugins.host import PluginHost
from core.plugins.slots import SlotRegistry
from core.sessions.manager import SessionManager
from .agent import AgentKernel
from .context import KernelContext

if TYPE_CHECKING:
    from core.llm_gateway import AbstractProvider

logger = logging.getLogger(__name__)


@dataclass
class BootstrapResult:
    """AppBootstrap.init() 的返回结果 — 所有已初始化的组件。"""
    config: Config
    provider: AbstractProvider | None
    model_name: str
    tool_registry: ToolRegistry
    mcp_adapter: MCPAdapter
    cmd_registry: CommandRegistry
    ingester: ContextIngester
    pipeline: ContextPipeline
    kernel: AgentKernel
    store: JsonStore
    errors: list[str]


class AppBootstrap:
    """应用组合根 — 构建所有组件并连接依赖。

    用法:
        result = await AppBootstrap.init()
        app._kernel = result.kernel
        ...
    """

    @staticmethod
    async def init() -> BootstrapResult:
        """初始化所有组件（配置 → 工具 → 上下文 → 记忆 → 内核）。

        Returns:
            BootstrapResult 包含所有已初始化的组件。
        """
        errors: list[str] = []

        # ── 1. 配置 + Provider ──
        config = Config.load()
        try:
            provider = create_provider(config.llm)
            model_name = config.llm.model or config.llm.provider
        except Exception as e:
            provider = None
            model_name = "未配置"
            errors.append(f"Provider 初始化失败: {e}")

        # ── 2. 工具 + MCP ──
        tool_registry = ToolRegistry()
        register_builtin_tools(tool_registry)

        mcp_adapter = MCPAdapter()
        mcp_connected = await mcp_adapter.load_builtin_servers()
        if mcp_connected > 0:
            mcp_tools = await mcp_adapter.discover_all_tools()
            for tool in mcp_tools:
                tool_registry.register(tool)
            mcp_adapter.start_health_check()
        mcp_adapter.start_watcher()

        # ── 3. 命令注册 ──
        cmd_registry = CommandRegistry()

        # ── 4. 存储 + 上下文 + 记忆 + 会话 + 插件 ──
        store = JsonStore(config.aide_root)
        await store.start()

        ingester = ContextIngester(store)
        pipeline = ContextPipeline(
            agent_root=config.aide_root / "agent",
            window_turns=config.app.window_turns,
            relevance_threshold=config.app.relevance_threshold,
        )
        compactor = ContextCompactor(provider, store)
        entry_mgr = EntryManager(store)
        tracker = TopicFrequencyTracker(store)
        capture_engine = CaptureEngine(entry_mgr, tracker)
        prompt_updater = PromptUpdater(
            provider, entry_mgr,
            on_cache_flush=pipeline.flush_cache,
        )
        session_mgr = SessionManager(config.sessions_root)
        slot_registry = SlotRegistry()
        plugin_host = PluginHost(
            config, tool_registry, cmd_registry, slot_registry,
        )

        # ── 自动发现并加载全部插件/技能 ──
        manifests = plugin_host.discover()
        for m in manifests:
            try:
                info = await plugin_host.load(m.id)
                if info:
                    logger.info(f"启动加载: {m.id} ({m.kind})")
                else:
                    logger.warning(f"启动加载失败: {m.id}")
            except Exception as e:
                logger.warning(f"启动加载异常 {m.id}: {e}")
        logger.info(f"插件: {plugin_host.count()} 已加载")

        # ── 5. 内核 ──
        ctx = KernelContext(
            config=config,
            provider=provider,
            tool_registry=tool_registry,
            command_registry=cmd_registry,
            context_pipeline=pipeline,
            ingester=ingester,
            compactor=compactor,
            session_manager=session_mgr,
            capture_engine=capture_engine,
            entry_manager=entry_mgr,
            prompt_updater=prompt_updater,
            topic_tracker=tracker,
            plugin_host=plugin_host,
            slot_registry=slot_registry,
        )
        kernel = AgentKernel(ctx)

        logger.info(
            f"Bootstrap 完成 — 模型: {model_name}, "
            f"工具: {len(tool_registry.list_names())}, "
            f"MCP: {len(mcp_adapter.connected_servers)} 服务端"
        )

        return BootstrapResult(
            config=config,
            provider=provider,
            model_name=model_name,
            tool_registry=tool_registry,
            mcp_adapter=mcp_adapter,
            cmd_registry=cmd_registry,
            ingester=ingester,
            pipeline=pipeline,
            kernel=kernel,
            store=store,
            errors=errors,
        )
