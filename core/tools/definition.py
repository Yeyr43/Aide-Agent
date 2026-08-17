"""工具声明数据模型 — ToolDefinition + ToolContext。

独立成叶子模块，避免工具模块 import ToolDefinition 时
与 core.tools.__init__ 形成循环导入（__init__ 会 import 各工具模块）。

ToolDefinition 是工具的声明式定义：声明一个工具的名称、功能（description）、
调用方式（parameters + execute）。每个工具模块自包含一份 definition，
discovery.py 只负责收集注册。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Awaitable, TYPE_CHECKING

from .retry import RetryConfig

if TYPE_CHECKING:
    from core.search import SearchIndex


@dataclass
class ToolContext:
    """工具执行时可访问的共享服务。

    由 AppBootstrap 创建并注入 ToolRegistry。
    每个工具可选择性地接收此上下文来访问全局服务，
    而不依赖模块级单例。

    Attributes:
        search_index: 全局会话搜索索引（search_chat / recall 使用）
        sessions_root: 会话存储根目录
        agent_root: agent 记忆文件根目录
        current_session_id: 当前会话 ID（如适用）
        provider: LLM provider（子 agent delegate 工具使用）
        tool_registry: 工具注册中心（子 agent delegate 工具复用，自引用）
        hook_runner: 生命周期钩子运行时（子 agent 的 PermissionRequest/SubagentStop）
        plugin_host: 插件宿主（plugin 管理工具使用；Phase 4 后注入）
    """
    search_index: SearchIndex | None = None
    sessions_root: Path | None = None
    agent_root: Path | None = None
    current_session_id: str | None = None
    provider: object | None = None
    tool_registry: object | None = None
    hook_runner: object | None = None
    plugin_host: object | None = None


@dataclass
class ToolDefinition:
    """单个工具的声明式定义。

    声明一个工具的完整信息：名称、功能描述、参数 schema、执行函数。
    这是工具的"单一事实来源"——注册、生成 schema、注入 LLM 上下文都从这里派生。

    Attributes:
        name: 工具名（LLM function calling 使用）
        description: 工具描述（注入 LLM context，指导何时调用）
        parameters: JSON Schema 格式的参数定义
        execute: 异步执行函数，签名为 async (arguments: dict, ctx?: ToolContext) -> str
        retry: 工具级重试配置（None 使用注册中心默认值）
    """
    name: str
    description: str
    parameters: dict
    execute: Callable[..., Awaitable[str]] | None = None
    retry: RetryConfig | None = None
