"""Plugin SDK — define_plugin() 装饰器 + 对外 API surface。"""

from __future__ import annotations

from typing import Callable
from .contract import PluginAPI

PluginEntry = Callable[[PluginAPI], None]


def define_plugin(plugin_id: str) -> Callable[[PluginEntry], PluginEntry]:
    """装饰器：标记 Python 函数为插件入口。

    Usage:
        @define_plugin("my-plugin")
        def register(api: PluginAPI):
            api.register_tool(my_tool)
    """
    def decorator(fn: PluginEntry) -> PluginEntry:
        fn.__aide_plugin_id__ = plugin_id  # type: ignore[attr-defined]
        return fn
    return decorator
