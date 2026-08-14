"""Plugins — 插件协议、宿主、SDK、钩子、状态、安全、热重载。"""

from .contract import PluginManifest, PluginAPI, PluginSlot, ContextProvider
from .host import PluginHost
from .sdk import define_plugin
from .slots import SlotRegistry

# P7: 新子系统
from .manifest_v2 import PluginManifestV2, detect_plugin_format
from .adapter import PluginFormatDetector, ClaudeCodeAdapter, OpenClawSkillAdapter, AideNativeAdapter
from .hook_runner import HookRunner, HookDefinition, HookContext, HookResult, MatcherCompiler, check_hook_results
from .state import PluginStateManager, PluginStatus, PluginStateEntry, RequirementsChecker
from .security import PluginPreflightCheck, PreflightWarning, PreflightResult
from .watcher import PluginWatcher

__all__ = [
    # Core (existing)
    "PluginManifest", "PluginAPI", "PluginSlot", "ContextProvider",
    "PluginHost", "define_plugin", "SlotRegistry",
    # P7: Manifest
    "PluginManifestV2", "detect_plugin_format",
    # P7: Adapters
    "PluginFormatDetector", "ClaudeCodeAdapter", "OpenClawSkillAdapter", "AideNativeAdapter",
    # P7: Hooks
    "HookRunner", "HookDefinition", "HookContext", "HookResult", "MatcherCompiler", "check_hook_results",
    # P7: State
    "PluginStateManager", "PluginStatus", "PluginStateEntry", "RequirementsChecker",
    # P7: Security
    "PluginPreflightCheck", "PreflightWarning", "PreflightResult",
    # P7: Watcher
    "PluginWatcher",
]
