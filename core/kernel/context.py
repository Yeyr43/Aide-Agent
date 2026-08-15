"""KernelContext — AgentKernel 的所有依赖聚合为分层 dataclass。

P4 Batch 2: 替代 15 参数的构造函数，单一参数注入。
P5: 拆分为 3 个子 context — 按子系统聚合，降低 Bootstrap 耦合面。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config import Config
    from core.llm_gateway import AbstractProvider
    from core.tools import ToolRegistry
    from core.commands import CommandRegistry
    from core.context import ContextPipeline, ContextIngester
    from core.memory import ReflectEngine
    from core.plugins.host import PluginHost
    from core.plugins.slots import SlotRegistry
    from core.sessions.manager import SessionManager


# ── 子 Context（按领域聚合）───────────────────────────────────────────


@dataclass
class MemoryContext:
    """记忆管线：统一反思引擎 + 反馈验证器 + 自动提取器。"""
    reflector: ReflectEngine
    feedback_verifier: object | None = None  # FeedbackVerifier | None
    auto_memory: object | None = None        # AutoMemoryExtractor | None


@dataclass
class ToolingContext:
    """扩展系统：工具 + 命令 + 插件 + 生命周期插槽。"""
    tool_registry: ToolRegistry
    command_registry: CommandRegistry
    plugin_host: PluginHost
    slot_registry: SlotRegistry


@dataclass
class SessionContext:
    """会话 + 上下文管线：管道 → 摄取 → 存储。"""
    context_pipeline: ContextPipeline
    ingester: ContextIngester
    session_manager: SessionManager


# ── 聚合 Context ──────────────────────────────────────────────────────


@dataclass
class KernelContext:
    """AgentKernel 的所有依赖，按领域分 3 个子 context。

    由 AppBootstrap 构建，注入 AgentKernel。
    新增依赖只需修改对应子 context 和 AppBootstrap，不影响 AgentKernel 签名。
    子 context 可独立用于测试（如 MemoryContext 可单独 mock）。

    P7: 新增 hook_runner — 生命周期钩子运行时。
    """
    config: Config
    provider: AbstractProvider
    tooling: ToolingContext
    memory: MemoryContext
    session: SessionContext
    hook_runner: object | None = None  # HookRunner | None (P7)
