"""Plugins — 插件协议、宿主、SDK 与插槽管理。"""

from .contract import PluginManifest, PluginAPI, PluginSlot, ContextProvider
from .host import PluginHost
from .sdk import define_plugin
from .slots import SlotRegistry

__all__ = [
    "PluginManifest", "PluginAPI", "PluginSlot", "ContextProvider",
    "PluginHost", "define_plugin", "SlotRegistry",
]
