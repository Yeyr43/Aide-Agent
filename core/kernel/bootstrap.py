"""AppBootstrap — 应用组合根。

将 on_mount 中的组件初始化逻辑提取为独立服务。
单一职责：创建所有组件并注入 AgentKernel。

P6: init() 拆分为 5 个 private phase 方法，每 phase 可独立理解、测试、修改。
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
from core.mcp import MCPAdapter
from core.commands import CommandRegistry
from core.context import ContextPipeline, ContextIngester
from core.memory import ReflectEngine, FeedbackStore, FeedbackVerifier
from core.plugins.host import PluginHost
from core.plugins.slots import SlotRegistry
from core.plugins.hook_runner import HookRunner
from core.sessions.manager import SessionManager
from core.search import SearchIndex
from .agent import AgentKernel
from .context import KernelContext, MemoryContext, ToolingContext, SessionContext

if TYPE_CHECKING:
    from core.llm_gateway import AbstractProvider
    from core.mcp import MCPAdapter as MCPAdapterType

logger = logging.getLogger(__name__)


@dataclass
class BootstrapResult:
    """AppBootstrap.init() 的返回结果 — 所有已初始化的组件。"""
    config: Config
    provider: AbstractProvider | None
    model_name: str
    tool_registry: ToolRegistry
    mcp_adapter: "MCPAdapterType"
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

        # 冷启动后重载 provider（无需完整重新初始化）
        provider, model_name = AppBootstrap.reload_provider(config, kernel, pipeline)
    """

    # ── 公开 API ──────────────────────────────────────────────────────

    @staticmethod
    def reload_provider(config: "Config",
                        kernel: "AgentKernel | None" = None,
                        pipeline: "ContextPipeline | None" = None):
        """冷启动/配置变更后重新创建 provider 并更新内核引用。

        与 init() 不同：不重建工具/MCP/插件/会话等组件，只更新 LLM 层。
        由 AideApp._reload_after_onboarding() 及 /model /api 命令调用。
        """
        from core.llm_gateway import create_provider

        provider = create_provider(config.llm)
        model_name = config.llm.model or config.llm.provider

        if kernel is not None:
            kernel.set_provider(provider)
            kernel._fc_loop.max_turns = config.app.max_turns

        if pipeline is not None:
            pipeline.full_text_turns = config.app.full_text_turns
            pipeline.summary_turns = config.app.summary_turns
            pipeline.relevance_threshold = config.app.relevance_threshold

        logger.info(f"Provider 重载完成 — 模型: {model_name}")
        return provider, model_name

    @staticmethod
    async def init() -> BootstrapResult:
        """初始化所有组件（编排 5 个 phase）。"""
        config = Config.load()

        # Phase 1: LLM Provider
        provider, model_name, errors = AppBootstrap._init_provider(config)

        # Phase 2: 工具 + MCP + 命令
        tool_registry, mcp_adapter, cmd_registry = (
            await AppBootstrap._init_tooling(config)
        )

        # Phase 3: 存储 + 上下文 + 记忆 + 反馈 + 会话
        phase3 = await AppBootstrap._init_storage_and_context(config)

        # 注入 ToolContext（连接 Phase 2 和 Phase 3 的产物）
        from core.tools import ToolContext
        tool_registry.tool_context = ToolContext(
            search_index=phase3["search_index"],
            sessions_root=config.sessions_root,
            agent_root=config.aide_root / "agent",
            provider=provider,
            tool_registry=tool_registry,
        )

        # Phase 4: 插件
        plugin_host = await AppBootstrap._init_plugins(
            config, tool_registry, cmd_registry,
        )

        # P7: 构建 HookRunner（聚合所有插件的 hooks）
        hook_runner = HookRunner()
        all_hooks = plugin_host.get_hooks()
        if all_hooks:
            hook_runner.load_from_dicts([
                {"event": h.event, "matcher": h.matcher,
                 "type": h.type, "command": h.command, "timeout": h.timeout}
                for h in all_hooks
            ])
            logger.info(f"HookRunner: {len(all_hooks)} hooks 已注册")

        # P7: SessionStart hook — 通知所有插件系统已就绪
        from core.plugins.hook_runner import HookContext
        try:
            await hook_runner.run("SessionStart", HookContext(
                event="SessionStart",
                session_id=str(config.sessions_root),
            ))
        except Exception:
            logger.debug("SessionStart hook 异常", exc_info=True)

        # P7: 注入 HookRunner 到 ToolRegistry（用于 PreToolUse/PostToolUse）
        tool_registry.hook_runner = hook_runner
        # 子 agent delegate 工具也需访问 hook_runner（PermissionRequest / SubagentStop）
        tool_registry.tool_context.hook_runner = hook_runner

        # Phase 5: 内核装配
        kernel = AppBootstrap._init_kernel(
            config, provider, tool_registry, cmd_registry,
            phase3["pipeline"], phase3["ingester"],
            phase3["session_mgr"], phase3["reflector"],
            plugin_host, phase3["feedback_verifier"],
            hook_runner,
        )

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
            ingester=phase3["ingester"],
            pipeline=phase3["pipeline"],
            kernel=kernel,
            store=phase3["store"],
            errors=errors,
        )

    # ── Phase 1: Provider ─────────────────────────────────────────────

    @staticmethod
    def _init_provider(config: Config) -> tuple:
        """创建 LLM Provider。失败不抛异常，返回 None + 错误信息。"""
        errors: list[str] = []
        try:
            provider = create_provider(config.llm)
            model_name = config.llm.model or config.llm.provider
        except Exception as e:
            provider = None
            model_name = "未配置"
            errors.append(f"Provider 初始化失败: {e}")
        return provider, model_name, errors

    # ── Phase 2: 工具 + MCP + 命令 ────────────────────────────────────

    @staticmethod
    async def _init_tooling(config: Config) -> tuple:
        """初始化工具注册中心、MCP 适配器、命令注册中心。"""
        tool_registry = ToolRegistry()
        register_builtin_tools(tool_registry)

        mcp_adapter = MCPAdapter()
        mcp_adapter.set_tool_registry(tool_registry)
        mcp_connected, mcp_failed = await mcp_adapter.load_builtin_servers()
        if mcp_connected > 0:
            mcp_tools = await mcp_adapter.discover_all_tools()
            for tool in mcp_tools:
                tool_registry.register(tool)
            mcp_adapter.start_health_check()
        mcp_adapter.start_watcher()

        cmd_registry = CommandRegistry()

        return tool_registry, mcp_adapter, cmd_registry

    # ── Phase 3: 存储 + 上下文 + 记忆 + 反馈 + 会话 ──────────────────

    @staticmethod
    async def _init_storage_and_context(config: Config) -> dict:
        """初始化 JsonStore、SearchIndex、ContextIngester/Pipeline、
        ReflectEngine、FeedbackVerifier/Store、SessionManager。"""
        store = JsonStore(config.aide_root)
        await store.start()

        # 全局搜索索引：从所有会话 timeline.json 重建（timeline 是唯一源）
        # await 保证首次搜索前索引已就绪（个人量级扫描为毫秒级）
        search_index = SearchIndex(config.sessions_root)
        await search_index.rebuild()

        ingester = ContextIngester(store, sessions_root=config.sessions_root, search_index=search_index)

        # 反馈闭环
        feedback_store = FeedbackStore(config.aide_root / "agent")
        feedback_verifier = FeedbackVerifier(feedback_store)

        pipeline = ContextPipeline(
            agent_root=config.aide_root / "agent",
            full_text_turns=config.app.full_text_turns,
            summary_turns=config.app.summary_turns,
            relevance_threshold=config.app.relevance_threshold,
            feedback_store=feedback_store,
        )

        reflector = ReflectEngine(
            provider=None,  # 由 kernel.set_provider() 设置
            agent_root=config.aide_root / "agent",
            sessions_root=config.sessions_root,
        )
        reflector._on_cache_flush = lambda: pipeline.flush_cache()

        session_mgr = SessionManager(config.sessions_root, search_index=search_index)

        return {
            "store": store,
            "search_index": search_index,
            "ingester": ingester,
            "pipeline": pipeline,
            "feedback_store": feedback_store,
            "feedback_verifier": feedback_verifier,
            "reflector": reflector,
            "session_mgr": session_mgr,
        }

    # ── Phase 4: 插件 ─────────────────────────────────────────────────

    @staticmethod
    async def _init_plugins(
        config: Config,
        tool_registry: ToolRegistry,
        cmd_registry: CommandRegistry,
    ) -> PluginHost:
        """自动发现并加载全部插件/技能。"""
        slot_registry = SlotRegistry()
        plugin_host = PluginHost(config, tool_registry, cmd_registry, slot_registry)

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

        return plugin_host

    # ── Phase 5: 内核装配 ─────────────────────────────────────────────

    @staticmethod
    def _init_kernel(
        config: Config,
        provider: "AbstractProvider | None",
        tool_registry: ToolRegistry,
        cmd_registry: CommandRegistry,
        pipeline: ContextPipeline,
        ingester: ContextIngester,
        session_mgr: SessionManager,
        reflector: ReflectEngine,
        plugin_host: PluginHost,
        feedback_verifier: FeedbackVerifier,
        hook_runner: object | None = None,
    ) -> AgentKernel:
        """装配 AgentKernel 及其所有依赖。"""
        # 更新 reflector provider（Phase 3 时 provider 尚不可用）
        if provider is not None:
            reflector._provider = provider

        ctx = KernelContext(
            config=config,
            provider=provider,
            tooling=ToolingContext(
                tool_registry=tool_registry,
                command_registry=cmd_registry,
                plugin_host=plugin_host,
                slot_registry=plugin_host.slot_registry,
            ),
            memory=MemoryContext(
                reflector=reflector,
                feedback_verifier=feedback_verifier,
            ),
            session=SessionContext(
                context_pipeline=pipeline,
                ingester=ingester,
                session_manager=session_mgr,
            ),
            hook_runner=hook_runner,
        )
        return AgentKernel(ctx)
