"""KernelContext — AgentKernel 的所有依赖聚合为一个 dataclass。

P4 Batch 2: 替代 15 参数的构造函数，单一参数注入。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config import Config
    from core.llm_gateway import AbstractProvider
    from core.tools import ToolRegistry
    from core.commands import CommandRegistry
    from core.context import ContextPipeline, ContextIngester, ContextCompactor
    from core.memory import CaptureEngine, EntryManager, PromptUpdater, TopicFrequencyTracker
    from core.plugins.host import PluginHost
    from core.plugins.slots import SlotRegistry
    from core.sessions.manager import SessionManager


@dataclass
class KernelContext:
    """AgentKernel 的所有依赖。

    由 AppBootstrap 构建，注入 AgentKernel。
    新增依赖只需修改此 dataclass 和 AppBootstrap，不影响 AgentKernel 签名。
    """
    config: Config
    provider: AbstractProvider
    tool_registry: ToolRegistry
    command_registry: CommandRegistry
    context_pipeline: ContextPipeline
    ingester: ContextIngester
    compactor: ContextCompactor
    session_manager: SessionManager
    capture_engine: CaptureEngine
    entry_manager: EntryManager
    prompt_updater: PromptUpdater
    topic_tracker: TopicFrequencyTracker
    plugin_host: PluginHost
    slot_registry: SlotRegistry
