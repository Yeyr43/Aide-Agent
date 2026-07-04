"""工具层 — ToolDefinition + ToolRegistry + 五工具注册。

ToolRegistry 维护 name → ToolDefinition 映射，
提供 get_schemas() 返回 OpenAI function calling 格式的 tools 数组。
P4 Batch 2: 集成重试机制（core.tools.retry），瞬态错误自动指数退避重试。
"""

from dataclasses import dataclass, field
from typing import Callable, Awaitable

from .builtin import read_file, write_file, run_shell, search_memory, web_search, list_dir, clipboard, web_fetch, search_in_files, edit_file
from .retry import RetryConfig, async_retry
from core.locale import t


@dataclass
class ToolDefinition:
    """单个工具的定义。

    Attributes:
        name: 工具名（LLM function calling 使用）
        description: 工具描述（注入 LLM context，指导何时调用）
        parameters: JSON Schema 格式的参数定义
        execute: 异步执行函数，签名为 async (arguments: dict) -> str
        retry: 工具级重试配置（None 使用注册中心默认值）
    """
    name: str
    description: str
    parameters: dict
    execute: Callable[[dict], Awaitable[str]] | None = None
    retry: RetryConfig | None = None


class ToolRegistry:
    """工具注册中心。

    注册所有内置工具，提供按 name 查找、生成 OpenAI tools schema、
    以及带重试的工具执行。

    瞬态错误（网络/超时）自动指数退避重试，永久错误（权限/不存在）立即返回。
    """

    def __init__(self, default_retry: RetryConfig | None = None) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self.default_retry = default_retry or RetryConfig()

    def register(self, tool: ToolDefinition) -> None:
        """注册一个工具。同名工具后注册的覆盖先注册的。"""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """移除一个工具。返回 True 表示成功移除。"""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def get(self, name: str) -> ToolDefinition | None:
        """按名称获取工具定义。"""
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        """返回所有已注册工具名称。"""
        return list(self._tools.keys())

    def get_schemas(self) -> list[dict]:
        """返回 OpenAI function calling 格式的 tools 数组。

        Returns:
            [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}, ...]
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    async def execute(self, name: str, arguments: dict) -> str:
        """执行指定工具（含重试）。

        瞬态错误（网络/超时）自动指数退避重试，永久错误立即返回。

        Args:
            name: 工具名
            arguments: LLM 返回的参数字典

        Returns:
            工具执行结果字符串。失败时返回 "错误：..." 描述。
        """
        tool = self._tools.get(name)
        if tool is None:
            return t("tool.registry.not_found", name=name, tools=', '.join(self.list_names()))
        if tool.execute is None:
            return t("tool.registry.no_execute", name=name)

        retry_cfg = tool.retry or self.default_retry

        async def _call() -> str:
            return await tool.execute(arguments)

        return await async_retry(_call, config=retry_cfg, tool_name=name)
