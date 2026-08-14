"""PluginStateManager — 插件状态管理 + Requirements 检查器。

三态模型：
  READY       — 所有依赖满足，正常运行
  NEEDS_SETUP — 缺少 API key / 系统包 / 配置项
  DISABLED    — 用户手动关闭

状态持久化到 ~/.aide/config/plugin_states.json。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from core.setup import aide_dir
from core.storage import atomic_write_json

logger = logging.getLogger(__name__)


# ── 枚举 ──────────────────────────────────────────────────────────────────


class PluginStatus(Enum):
    READY = "ready"
    NEEDS_SETUP = "needs_setup"
    DISABLED = "disabled"


# ── 数据 ───────────────────────────────────────────────────────────────────


@dataclass
class PluginStateEntry:
    """单个插件的状态条目。"""
    plugin_id: str
    version: str = "0.0.0"
    status: PluginStatus = PluginStatus.NEEDS_SETUP
    enabled: bool = True
    missing_requirements: list[str] = field(default_factory=list)
    usage_count: int = 0
    last_verified: str = ""  # ISO timestamp


# ── Requirements 检查器 ──────────────────────────────────────────────────


@dataclass
class Requirement:
    """一个依赖声明。"""
    type: str       # "api_key" | "system_package" | "python_package" | "config"
    key: str        # "OPENAI_API_KEY" | "git" | "black>=22.0" | "llm.model"
    version: str = ""


class RequirementsChecker:
    """检查插件的依赖声明是否满足。

    支持四种类型：
      - api_key: 检查环境变量
      - system_package: 检查 PATH（shutil.which）
      - python_package: 检查 importlib / pip
      - config: 检查 Aide 配置项
    """

    @staticmethod
    def check(requirements: dict) -> list[Requirement]:
        """检查依赖，返回未满足的列表。"""
        missing: list[Requirement] = []

        # API keys
        for key in requirements.get("api_keys", []):
            if not os.environ.get(key):
                missing.append(Requirement(type="api_key", key=key))

        # System packages
        for pkg in requirements.get("system_packages", []):
            name = pkg.split(">=")[0].split(">")[0].strip()
            if not shutil.which(name):
                missing.append(Requirement(type="system_package", key=pkg))

        # Python packages
        for pkg in requirements.get("python_packages", []):
            name = pkg.split(">=")[0].split("==")[0].split(">")[0].strip()
            try:
                __import__(name.replace("-", "_"))
            except ImportError:
                missing.append(Requirement(type="python_package", key=pkg))

        return missing


# ── PluginStateManager ────────────────────────────────────────────────────


class PluginStateManager:
    """插件状态管理器 — 读写 ~/.aide/config/plugin_states.json。

    用法:
        mgr = PluginStateManager()
        mgr.set_status("my-plugin", PluginStatus.READY)
        entry = mgr.get("my-plugin")
    """

    def __init__(self, config_root: Path | None = None) -> None:
        self._root = config_root or (aide_dir() / "config")
        self._path = self._root / "plugin_states.json"
        self._entries: dict[str, dict] = {}
        self._load()

    # ── 持久化 ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._entries = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._entries = {}

    def _save(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._path, self._entries)

    # ── CRUD ─────────────────────────────────────────────────────────────

    def get(self, plugin_id: str) -> PluginStateEntry:
        """获取插件状态（不存在则返回默认 NEEDS_SETUP）。"""
        data = self._entries.get(plugin_id)
        if data is None:
            return PluginStateEntry(plugin_id=plugin_id)
        return PluginStateEntry(
            plugin_id=plugin_id,
            version=data.get("version", "0.0.0"),
            status=PluginStatus(data.get("status", "needs_setup")),
            enabled=data.get("enabled", True),
            missing_requirements=data.get("missing_requirements", []),
            usage_count=data.get("usage_count", 0),
            last_verified=data.get("last_verified", ""),
        )

    def set_status(self, plugin_id: str, status: PluginStatus) -> None:
        """更新插件状态。"""
        entry = self._entries.get(plugin_id, {})
        entry["status"] = status.value
        entry["last_verified"] = datetime.now(timezone.utc).isoformat()
        self._entries[plugin_id] = entry
        self._save()

    def enable(self, plugin_id: str) -> None:
        entry = self._entries.get(plugin_id, {})
        entry["enabled"] = True
        entry["status"] = PluginStatus.READY.value
        self._entries[plugin_id] = entry
        self._save()

    def disable(self, plugin_id: str) -> None:
        entry = self._entries.get(plugin_id, {})
        entry["enabled"] = False
        entry["status"] = PluginStatus.DISABLED.value
        self._entries[plugin_id] = entry
        self._save()

    def verify_requirements(self, plugin_id: str,
                            requirements: dict) -> list[str]:
        """检查并更新 missing_requirements。返回缺失的 key 列表。"""
        missing = RequirementsChecker.check(requirements)
        missing_keys = [f"{r.type}:{r.key}" for r in missing]

        entry = self._entries.get(plugin_id, {})
        entry["missing_requirements"] = missing_keys
        if missing_keys:
            entry["status"] = PluginStatus.NEEDS_SETUP.value
        elif entry.get("status") != PluginStatus.DISABLED.value:
            entry["status"] = PluginStatus.READY.value
        entry["last_verified"] = datetime.now(timezone.utc).isoformat()
        self._entries[plugin_id] = entry
        self._save()

        return missing_keys

    def record_usage(self, plugin_id: str) -> None:
        """记录一次插件使用。"""
        entry = self._entries.get(plugin_id, {})
        entry["usage_count"] = entry.get("usage_count", 0) + 1
        self._entries[plugin_id] = entry
        self._save()

    def list_all(self) -> list[PluginStateEntry]:
        """列出所有已知插件状态。"""
        return [self.get(pid) for pid in self._entries]

    def list_by_status(self, status: PluginStatus) -> list[PluginStateEntry]:
        """按状态筛选。"""
        return [e for e in self.list_all() if e.status == status]

    def count_by_status(self) -> dict[str, int]:
        """统计各状态插件数量。"""
        counts = {"ready": 0, "needs_setup": 0, "disabled": 0}
        for e in self.list_all():
            counts[e.status.value] += 1
        return counts

    def remove(self, plugin_id: str) -> None:
        """删除插件状态记录（卸载时调用）。"""
        self._entries.pop(plugin_id, None)
        self._save()
