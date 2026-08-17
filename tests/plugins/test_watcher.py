"""Tests for core.plugins.watcher — PluginWatcher 插件热重载。

覆盖：变更文件标签判定、精确重载范围、去抖调度、目录 mtime、start/stop 生命周期。
"""

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.plugins.watcher import PluginWatcher


def _make_plugin_dir(tmp_path: Path, name: str = "demo", files: dict | None = None) -> Path:
    """在 tmp_path 下建一个插件目录，可带文件。"""
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    for rel, content in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return d


class TestDetectChangedFiles:
    """_detect_changed_files — 变更文件类型标签判定。"""

    def test_no_files_no_changes(self, tmp_path):
        d = _make_plugin_dir(tmp_path)
        w = PluginWatcher(tmp_path, MagicMock())
        assert w._detect_changed_files(d) == set()

    @pytest.mark.parametrize("rel,tag", [
        (".claude-plugin/plugin.json", ".claude-plugin"),
        ("plugin.json", "plugin.json"),
        ("SKILL.md", "SKILL.md"),
        ("hooks/hooks.json", "hooks.json"),
        ("__init__.py", "__init__.py"),
        ("main.py", "main.py"),
    ])
    def test_detects_each_file_type(self, tmp_path, rel, tag):
        """每种受跟踪的文件类型都能被检出。"""
        d = _make_plugin_dir(tmp_path, files={rel: "content"})
        w = PluginWatcher(tmp_path, MagicMock())
        assert tag in w._detect_changed_files(d)

    def test_untracked_file_not_detected(self, tmp_path):
        """README 等未跟踪文件不产生变更标签。"""
        d = _make_plugin_dir(tmp_path, files={"README.md": "x"})
        w = PluginWatcher(tmp_path, MagicMock())
        assert w._detect_changed_files(d) == set()

    def test_unchanged_file_not_detected(self, tmp_path):
        """mtime 早于记录 → 不视为变更。"""
        d = _make_plugin_dir(tmp_path, files={"SKILL.md": "x"})
        w = PluginWatcher(tmp_path, MagicMock())
        w._mtimes["demo"] = 10**15  # 远大于文件 mtime
        assert w._detect_changed_files(d) == set()

    def test_mixed_changes(self, tmp_path):
        """多个文件同时变更 → 全部检出。"""
        d = _make_plugin_dir(tmp_path, files={"SKILL.md": "x", "__init__.py": "y"})
        w = PluginWatcher(tmp_path, MagicMock())
        changed = w._detect_changed_files(d)
        assert "SKILL.md" in changed
        assert "__init__.py" in changed


class TestDirMtime:
    """_dir_mtime — 目录最新 mtime。"""

    def test_empty_dir_zero(self, tmp_path):
        d = _make_plugin_dir(tmp_path)
        assert PluginWatcher._dir_mtime(d) == 0.0

    def test_missing_dir_zero(self, tmp_path):
        assert PluginWatcher._dir_mtime(tmp_path / "nope") == 0.0

    def test_returns_max_mtime(self, tmp_path):
        d = _make_plugin_dir(tmp_path, files={"a.txt": "1", "b.txt": "2"})
        os.utime(d / "a.txt", (1000, 1000))
        os.utime(d / "b.txt", (2000, 2000))
        assert PluginWatcher._dir_mtime(d) == 2000.0


class TestReloadPlugin:
    """_reload_plugin — 按变更范围精确重载。"""

    async def test_deleted_dir_unloads(self, tmp_path):
        host = MagicMock()
        host.unload = AsyncMock()
        w = PluginWatcher(tmp_path, host)
        d = _make_plugin_dir(tmp_path)
        d.rmdir()  # 目录已删除
        await w._reload_plugin(d)
        host.unload.assert_awaited_once_with("demo")
        assert "demo" not in w._mtimes

    async def test_new_dir_loads(self, tmp_path):
        host = MagicMock()
        host.is_loaded.return_value = False
        host.load = AsyncMock()
        w = PluginWatcher(tmp_path, host)
        await w._reload_plugin(_make_plugin_dir(tmp_path))
        host.load.assert_awaited_once_with("demo")

    async def test_manifest_change_reloads(self, tmp_path):
        host = MagicMock()
        host.is_loaded.return_value = True
        host.reload = AsyncMock()
        w = PluginWatcher(tmp_path, host)
        d = _make_plugin_dir(tmp_path, files={".claude-plugin/plugin.json": "{}"})
        await w._reload_plugin(d)
        host.reload.assert_awaited_once_with("demo")

    async def test_skill_change_reloads(self, tmp_path):
        host = MagicMock()
        host.is_loaded.return_value = True
        host.reload = AsyncMock()
        w = PluginWatcher(tmp_path, host)
        d = _make_plugin_dir(tmp_path, files={"SKILL.md": "x"})
        await w._reload_plugin(d)
        host.reload.assert_awaited_once_with("demo")

    async def test_python_entry_change_reloads(self, tmp_path):
        host = MagicMock()
        host.is_loaded.return_value = True
        host.reload = AsyncMock()
        w = PluginWatcher(tmp_path, host)
        d = _make_plugin_dir(tmp_path, files={"__init__.py": "x"})
        await w._reload_plugin(d)
        host.reload.assert_awaited_once_with("demo")

    async def test_no_relevant_change_noop(self, tmp_path):
        host = MagicMock()
        host.is_loaded.return_value = True
        host.reload = AsyncMock()
        host.load = AsyncMock()
        host.unload = AsyncMock()
        w = PluginWatcher(tmp_path, host)
        d = _make_plugin_dir(tmp_path, files={"README.md": "x"})
        await w._reload_plugin(d)
        host.reload.assert_not_awaited()
        host.load.assert_not_awaited()
        host.unload.assert_not_awaited()


class TestScheduleReload:
    """去抖调度。"""

    async def test_debounce_collapses_duplicate_schedules(self, tmp_path):
        """去抖窗口内同一目录多次调度 → 只重载一次。"""
        host = MagicMock()
        host.is_loaded.return_value = False
        host.load = AsyncMock()
        w = PluginWatcher(tmp_path, host)
        w.DEBOUNCE_SECONDS = 0.05
        d = _make_plugin_dir(tmp_path)
        await w._schedule_reload(d)
        await w._schedule_reload(d)  # 窗口内第二次 → 取消前一任务
        await asyncio.sleep(0.2)
        host.load.assert_awaited_once_with("demo")

    async def test_schedule_tracks_pending_task(self, tmp_path):
        w = PluginWatcher(tmp_path, MagicMock())
        w.DEBOUNCE_SECONDS = 0
        d = _make_plugin_dir(tmp_path)
        await w._schedule_reload(d)
        first = w._pending["demo"]
        assert first is not None
        await w._schedule_reload(d)
        assert w._pending["demo"] is not first  # 已替换为新的 task
        first.cancel()
        await asyncio.sleep(0.01)


class TestWatchfilesLoop:
    """_watchfiles_loop — watchfiles 变更事件。"""

    async def test_ignores_changes_outside_dir(self, tmp_path):
        """监听目录外的变更 → 跳过，不调度重载。"""
        host = MagicMock()
        host.load = AsyncMock()
        host.reload = AsyncMock()
        host.unload = AsyncMock()
        w = PluginWatcher(tmp_path, host)
        outside = (tmp_path.parent / "outside.txt").resolve()

        async def fake_awatch(*a, **k):
            yield {(1, str(outside))}

        await w._watchfiles_loop(fake_awatch)
        host.load.assert_not_awaited()
        host.reload.assert_not_awaited()
        host.unload.assert_not_awaited()

    async def test_schedules_reload_for_in_dir_change(self, tmp_path):
        """目录内文件变更 → 调度去抖重载。"""
        host = MagicMock()
        host.is_loaded.return_value = False
        host.load = AsyncMock()
        w = PluginWatcher(tmp_path, host)
        w.DEBOUNCE_SECONDS = 0
        d = _make_plugin_dir(tmp_path)

        async def fake_awatch(*a, **k):
            yield {(1, str(d / "SKILL.md"))}

        await w._watchfiles_loop(fake_awatch)
        await asyncio.sleep(0.05)
        host.load.assert_awaited_once_with("demo")


class TestStartStop:
    """start/stop 生命周期。"""

    async def test_start_polling_when_watchfiles_missing(self, tmp_path):
        """watchfiles 缺失 → polling 模式。"""
        w = PluginWatcher(tmp_path, MagicMock())
        with patch.dict(sys.modules, {"watchfiles": None}):
            await w.start()
        assert w._task is not None
        await w.stop()
        assert w._task is None

    async def test_start_uses_watchfiles_when_installed(self, tmp_path, monkeypatch):
        """watchfiles 可用 → 走 watchfiles 模式。"""
        fake = MagicMock()

        async def fake_awatch(*a, **k):
            if False:  # pragma: no cover
                yield
        fake.awatch = fake_awatch
        monkeypatch.setitem(sys.modules, "watchfiles", fake)

        w = PluginWatcher(tmp_path, MagicMock())
        await w.start()
        assert w._task is not None
        await w.stop()

    async def test_watchfiles_loop_error_falls_back_to_polling(self, tmp_path):
        """watchfiles 循环异常 → 切到 polling。"""
        w = PluginWatcher(tmp_path, MagicMock())

        async def broken_awatch(*a, **k):
            raise RuntimeError("boom")
            yield  # pragma: no cover

        await w._watchfiles_loop(broken_awatch)
        assert w._task is not None  # polling 任务
        await w.stop()

    async def test_detect_changed_files_stat_oserror(self, tmp_path):
        """文件 stat 抛 OSError → 忽略。"""
        d = _make_plugin_dir(tmp_path, files={"SKILL.md": "x"})
        w = PluginWatcher(tmp_path, MagicMock())
        with patch("pathlib.Path.stat", side_effect=OSError):
            assert w._detect_changed_files(d) == set()

    async def test_stop_without_start_is_noop(self, tmp_path):
        w = PluginWatcher(tmp_path, MagicMock())
        await w.stop()  # 不应抛异常

    async def test_stop_twice_is_noop(self, tmp_path):
        w = PluginWatcher(tmp_path, MagicMock())
        await w.stop()
        await w.stop()
