"""Tests for core.plugins.state — PluginStateManager + RequirementsChecker。

覆盖：三态 CRUD、持久化往返、依赖检查四类、损坏 JSON 容错。
"""

import json
from pathlib import Path

import pytest

from core.plugins.state import (
    PluginStateManager,
    PluginStatus,
    PluginStateEntry,
    RequirementsChecker,
    Requirement,
)


def _manager(tmp_path) -> PluginStateManager:
    return PluginStateManager(config_root=tmp_path)


class TestRequirementsChecker:
    """依赖声明检查。"""

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("SOME_TEST_API_KEY", raising=False)
        missing = RequirementsChecker.check({"api_keys": ["SOME_TEST_API_KEY"]})
        assert missing == [Requirement(type="api_key", key="SOME_TEST_API_KEY")]

    def test_present_api_key(self, monkeypatch):
        monkeypatch.setenv("PRESENT_TEST_API_KEY", "x")
        missing = RequirementsChecker.check({"api_keys": ["PRESENT_TEST_API_KEY"]})
        assert missing == []

    def test_missing_system_package(self, monkeypatch):
        monkeypatch.setattr("core.plugins.state.shutil.which", lambda name: None)
        missing = RequirementsChecker.check({"system_packages": ["nonexistent-tool"]})
        assert missing == [Requirement(type="system_package", key="nonexistent-tool")]

    def test_present_system_package(self, monkeypatch):
        monkeypatch.setattr("core.plugins.state.shutil.which", lambda name: "/usr/bin/git")
        missing = RequirementsChecker.check({"system_packages": ["git"]})
        assert missing == []

    def test_missing_python_package(self):
        missing = RequirementsChecker.check({"python_packages": ["definitely_not_a_module_xyz"]})
        assert missing == [Requirement(type="python_package", key="definitely_not_a_module_xyz")]

    def test_present_python_package(self):
        missing = RequirementsChecker.check({"python_packages": ["os"]})
        assert missing == []

    def test_version_parsing_strips_constraints(self, monkeypatch):
        monkeypatch.setattr("core.plugins.state.shutil.which", lambda name: None)
        missing = RequirementsChecker.check({"system_packages": ["git>=2.0"]})
        assert missing == [Requirement(type="system_package", key="git>=2.0")]

    def test_multiple_missing(self, monkeypatch):
        monkeypatch.delenv("A", raising=False)
        missing = RequirementsChecker.check({"api_keys": ["A", "B"]})
        assert len(missing) == 2


class TestGet:
    """状态读取。"""

    def test_default_needs_setup(self, tmp_path):
        mgr = _manager(tmp_path)
        entry = mgr.get("my-plugin")
        assert entry.plugin_id == "my-plugin"
        assert entry.status == PluginStatus.NEEDS_SETUP
        assert entry.enabled is True
        assert entry.missing_requirements == []

    def test_get_returns_dataclass(self, tmp_path):
        mgr = _manager(tmp_path)
        assert isinstance(mgr.get("x"), PluginStateEntry)


class TestStatusCRUD:
    """状态写入。"""

    def test_set_status_persists(self, tmp_path):
        mgr = _manager(tmp_path)
        mgr.set_status("p", PluginStatus.READY)
        # 重建实例 → 从磁盘读回
        mgr2 = _manager(tmp_path)
        assert mgr2.get("p").status == PluginStatus.READY
        assert mgr2.get("p").last_verified  # 写入时间戳

    def test_enable_sets_ready(self, tmp_path):
        mgr = _manager(tmp_path)
        mgr.disable("p")
        mgr.enable("p")
        entry = mgr.get("p")
        assert entry.enabled is True
        assert entry.status == PluginStatus.READY

    def test_disable_sets_disabled(self, tmp_path):
        mgr = _manager(tmp_path)
        mgr.disable("p")
        entry = mgr.get("p")
        assert entry.enabled is False
        assert entry.status == PluginStatus.DISABLED

    def test_verify_requirements_missing_sets_needs_setup(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MISSING_KEY", raising=False)
        mgr = _manager(tmp_path)
        missing = mgr.verify_requirements("p", {"api_keys": ["MISSING_KEY"]})
        assert missing == ["api_key:MISSING_KEY"]
        entry = mgr.get("p")
        assert entry.status == PluginStatus.NEEDS_SETUP
        assert "api_key:MISSING_KEY" in entry.missing_requirements

    def test_verify_requirements_satisfied_sets_ready(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRESENT", "1")
        mgr = _manager(tmp_path)
        mgr.set_status("p", PluginStatus.NEEDS_SETUP)
        missing = mgr.verify_requirements("p", {"api_keys": ["PRESENT"]})
        assert missing == []
        assert mgr.get("p").status == PluginStatus.READY

    def test_verify_keeps_disabled_when_satisfied(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRESENT", "1")
        mgr = _manager(tmp_path)
        mgr.disable("p")
        mgr.verify_requirements("p", {"api_keys": ["PRESENT"]})
        assert mgr.get("p").status == PluginStatus.DISABLED  # 不因依赖满足而复活


class TestListAndRemove:
    """枚举与删除。"""

    def test_list_all_and_count(self, tmp_path):
        mgr = _manager(tmp_path)
        mgr.set_status("a", PluginStatus.READY)
        mgr.disable("b")
        mgr.set_status("c", PluginStatus.NEEDS_SETUP)
        entries = mgr.list_all()
        assert {e.plugin_id for e in entries} == {"a", "b", "c"}
        counts = mgr.count_by_status()
        assert counts == {"ready": 1, "needs_setup": 1, "disabled": 1}

    def test_empty_count(self, tmp_path):
        mgr = _manager(tmp_path)
        assert mgr.count_by_status() == {"ready": 0, "needs_setup": 0, "disabled": 0}

    def test_remove(self, tmp_path):
        mgr = _manager(tmp_path)
        mgr.set_status("a", PluginStatus.READY)
        mgr.remove("a")
        assert mgr.get("a").status == PluginStatus.NEEDS_SETUP  # 回到默认
        assert mgr.list_all() == []

    def test_remove_unknown_noop(self, tmp_path):
        mgr = _manager(tmp_path)
        mgr.remove("nope")  # 不抛异常


class TestPersistence:
    """持久化边界。"""

    def test_corrupt_json_loads_empty(self, tmp_path):
        path = tmp_path / "plugin_states.json"
        path.write_text("{not valid json", encoding="utf-8")
        mgr = _manager(tmp_path)
        assert mgr.list_all() == []

    def test_writes_atomic_json(self, tmp_path):
        mgr = _manager(tmp_path)
        mgr.set_status("p", PluginStatus.READY)
        assert (tmp_path / "plugin_states.json").exists()
        data = json.loads((tmp_path / "plugin_states.json").read_text(encoding="utf-8"))
        assert data["p"]["status"] == "ready"

    def test_get_with_partial_data(self, tmp_path):
        (tmp_path / "plugin_states.json").write_text(
            json.dumps({"p": {"version": "1.2.3"}}), encoding="utf-8")
        mgr = _manager(tmp_path)
        entry = mgr.get("p")
        assert entry.version == "1.2.3"
        assert entry.status == PluginStatus.NEEDS_SETUP  # 缺失字段用默认
        assert entry.enabled is True
