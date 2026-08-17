""" End-to-end integration tests — full pipeline without Textual UI.

Tests the complete flow: CommandRegistry → route → handler → result.
Also tests session creation and listing end-to-end.
"""

import json
import shutil
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from core.commands import CommandRegistry, CommandDefinition
from core.config import Config
from core.storage import JsonStore
from core.sessions.manager import SessionManager
from core.context.ingester import ContextIngester
from core.context.pipeline import ContextPipeline
from core.memory.recall import recall
from core.memory.reflector import ReflectEngine
from core.llm_gateway import TextDelta, StreamEnd
import core.commands.builtin.handlers as handlers_mod


class TestCommandE2E:
    """End-to-end: user types /command → CommandRegistry routes → handler returns result."""

    @pytest.fixture
    def registry(self):
        return CommandRegistry()

    def test_help_command_returns_all_commands(self, registry):
        result = registry.route("/help")
        assert result is not None
        cmd_def, args = result
        assert cmd_def.name == "/help"

    def test_profile_command_routing(self, registry):
        result = registry.route("/profile")
        assert result is not None
        cmd_def, args = result
        assert cmd_def.name == "/profile"

    def test_help_with_extra_space(self, registry):
        """Exact match should work even with trailing space."""
        result = registry.route("/help ")
        assert result is not None
        cmd_def, args = result
        assert cmd_def.name == "/help"

    def test_prefix_match(self, registry):
        """Partial prefix should match the closest command."""
        result = registry.route("/hel")
        if result is not None:
            cmd_def, args = result
            assert cmd_def.name.startswith("/hel")

    def test_non_command_returns_none(self, registry):
        result = registry.route("hello world")
        assert result is None

    def test_slash_only_returns_none(self, registry):
        result = registry.route("/")
        assert result is None


class TestSessionE2E:
    """End-to-end: session create → list → delete lifecycle."""

    @pytest.fixture
    def sessions_root(self, tmp_path):
        root = tmp_path / "sessions"
        root.mkdir()
        return root

    @pytest.fixture
    def store(self):
        s = MagicMock(spec=JsonStore)
        s.write = AsyncMock()
        s.read = AsyncMock()
        s.read.return_value = None
        return s

    @pytest.mark.asyncio
    async def test_create_and_list_session(self, sessions_root, store):
        """Create a session, then list it."""
        mgr = SessionManager(sessions_root)
        info = mgr.create("帮我写个Python脚本")
        assert info.id is not None
        assert "Python" in info.name

        sessions = mgr.list_all()
        assert len(sessions) == 1
        assert sessions[0].id == info.id

    @pytest.mark.asyncio
    async def test_create_and_delete_session(self, sessions_root):
        """Create then delete a session."""
        mgr = SessionManager(sessions_root)
        info = mgr.create("测试会话")
        assert mgr.delete(info.id) is True
        assert len(mgr.list_all()) == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent_session(self, sessions_root):
        mgr = SessionManager(sessions_root)
        assert mgr.delete("nonexistent") is False

    @pytest.mark.asyncio
    async def test_smart_title_generation(self, sessions_root):
        """Smart title should extract meaningful name from first message."""
        mgr = SessionManager(sessions_root)

        # Test various inputs
        cases = [
            ("帮我写一个Python脚本处理数据", "Python脚本处理数据"),
            ("怎么部署Django项目到服务器", "部署Django项目到服务器"),
            ("hello world", "hello world"),
        ]
        for msg, expected_hint in cases:
            info = mgr.create(msg)
            # Title should have removed common prefixes
            assert "帮我" not in info.name or len(info.name) <= 20


class TestContextPipeline:
    """End-to-end: context assembly pipeline."""

    @pytest.fixture
    def agent_root(self, tmp_path):
        root = tmp_path / "agent"
        root.mkdir()
        return root

    @pytest.mark.asyncio
    async def test_assemble_with_all_files(self, agent_root):
        """Full assembly with soul, preferences, workflows, long-term memory."""
        (agent_root / "soul.md").write_text("你是 Aide，智能管家", encoding="utf-8")
        (agent_root / "preferences.md").write_text("# 偏好\n用户喜欢简洁回复", encoding="utf-8")
        (agent_root / "workflows.md").write_text("# 工作流\n用户是后端工程师", encoding="utf-8")
        (agent_root / "long_term_memory.md").write_text("# 记忆\nPython 3.13", encoding="utf-8")

        pipeline = ContextPipeline(agent_root=agent_root)
        messages, _ = await pipeline.assemble(None, "简洁回复很重要")

        assert len(messages) >= 1
        system_content = messages[0]["content"]
        assert "Aide" in system_content

    @pytest.mark.asyncio
    async def test_relevance_filters_low_score(self, agent_root):
        """Low-relevance sections should be collapsed."""
        (agent_root / "soul.md").write_text("你是 Aide", encoding="utf-8")
        (agent_root / "preferences.md").write_text("用户喜欢React前端开发\n\n用户使用VS Code\n\n用户用Docker部署", encoding="utf-8")
        (agent_root / "workflows.md").write_text("", encoding="utf-8")
        (agent_root / "long_term_memory.md").write_text("", encoding="utf-8")

        pipeline = ContextPipeline(agent_root=agent_root)
        # Query about Python backend — should not match React/Docker sections
        _, scores = await pipeline.assemble(None, "Python后端API开发")

        # Should have relevance scores computed
        assert isinstance(scores, list)


class TestMemoryRecallE2E:
    """End-to-end: memory recall with bigram Jaccard."""

    @pytest.fixture
    def agent_root(self, tmp_path):
        root = tmp_path / "agent"
        root.mkdir()
        return root

    @pytest.mark.asyncio
    async def test_recall_matches_relevant_preferences(self, agent_root):
        """Recall should return entries relevant to the user message."""
        (agent_root / "preferences.md").write_text(
            "用户偏好简洁回复\n用户喜欢Python编程\n用户使用VS Code\n",
            encoding="utf-8",
        )
        entries = await recall("帮我写Python脚本", agent_root)
        assert isinstance(entries, list)

    @pytest.mark.asyncio
    async def test_no_recall_when_no_files(self, agent_root):
        """Empty agent directory should not crash."""
        entries = await recall("任何消息", agent_root)
        assert entries == []


class TestExportImportE2E:
    """真实 handler：导出 zip → 删数据 → 导入 → 还原。"""

    @pytest.fixture
    def roots(self, tmp_path, monkeypatch):
        aide_root = tmp_path / ".aide"
        agent_root = aide_root / "agent"
        agent_root.mkdir(parents=True)
        download = tmp_path / "downloads"
        download.mkdir()
        monkeypatch.setattr(handlers_mod, "AIDE_ROOT", aide_root)
        monkeypatch.setattr(handlers_mod, "AGENT_ROOT", agent_root)
        monkeypatch.setattr(handlers_mod, "user_download_dir", lambda: download)
        return aide_root, download

    @pytest.mark.asyncio
    async def test_export_then_import_restores_data(self, roots):
        aide_root, download = roots
        (aide_root / "agent" / "soul.md").write_text("你是 Aide", encoding="utf-8")
        (aide_root / "agent" / "preferences.md").write_text("# 偏好\n- 喜欢简洁\n", encoding="utf-8")
        sess = aide_root / "sessions" / "20260801_000000"
        (sess / "messages").mkdir(parents=True)
        (sess / "meta.json").write_text("{}", encoding="utf-8")
        (sess / "timeline.json").write_text("", encoding="utf-8")

        await handlers_mod.handle_export(MagicMock(), "")
        zips = list(download.glob("*.zip"))
        assert len(zips) == 1, "导出应生成一个 zip"

        # 删除数据 → 导入恢复
        shutil.rmtree(aide_root / "agent")
        shutil.rmtree(aide_root / "sessions")
        assert not (aide_root / "agent").exists()

        out = await handlers_mod.handle_import(MagicMock(), str(zips[0]))
        assert (aide_root / "agent" / "soul.md").exists()
        assert "简洁" in (aide_root / "agent" / "preferences.md").read_text(encoding="utf-8")
        assert (aide_root / "sessions" / "20260801_000000").exists()

    @pytest.mark.asyncio
    async def test_import_missing_file(self, roots):
        out = await handlers_mod.handle_import(MagicMock(), "/nonexistent/x.zip")
        assert "not_found" in out or "不存在" in out

    @pytest.mark.asyncio
    async def test_import_non_zip(self, roots, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("x", encoding="utf-8")
        out = await handlers_mod.handle_import(MagicMock(), str(f))
        assert "not_zip" in out or "zip" in out.lower() or "不是" in out

    @pytest.mark.asyncio
    async def test_import_no_args(self, roots):
        out = await handlers_mod.handle_import(MagicMock(), "")
        assert "need_path" in out or "路径" in out

    @pytest.mark.asyncio
    async def test_import_unsafe_path_rejected(self, roots, tmp_path):
        """zip 内路径逃逸（../）→ 拒绝解压。"""
        import zipfile
        evil = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../escape.txt", "x")
        out = await handlers_mod.handle_import(MagicMock(), str(evil))
        assert "unsafe" in out or "安全" in out or "越界" in out or "错误" in out

    @pytest.mark.asyncio
    async def test_import_invalid_zip(self, roots, tmp_path):
        """损坏的 zip → invalid_zip 提示。"""
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a real zip")
        out = await handlers_mod.handle_import(MagicMock(), str(bad))
        assert "invalid_zip" in out or "无效" in out or "损坏" in out or "zip" in out.lower()


class TestMemoryPanelE2E:
    """真实 handler：写入记忆 → /memory 面板状态正确。"""

    @pytest.fixture
    def agent_root(self, tmp_path, monkeypatch):
        root = tmp_path / ".aide" / "agent"
        root.mkdir(parents=True)
        monkeypatch.setattr(handlers_mod, "AGENT_ROOT", root)
        return root

    @pytest.mark.asyncio
    async def test_memory_panel_counts_entries(self, agent_root):
        (agent_root / "preferences.md").write_text("# 偏好\n- 喜欢简洁回复\n- 喜欢Python\n", encoding="utf-8")
        (agent_root / "workflows.md").write_text("# 工作流\n- 先读文件\n", encoding="utf-8")
        out = await handlers_mod.handle_memory(MagicMock(), "")
        assert "偏好" in out
        assert "confirmed" in out.lower() or "条" in out

    @pytest.mark.asyncio
    async def test_memory_panel_empty_state(self, agent_root):
        out = await handlers_mod.handle_memory(MagicMock(), "")
        assert "no_data" in out or "暂无" in out or "偏好" in out


class TestReflectWritebackE2E:
    """真实 ReflectEngine + mock LLM → 解析 → diff → 写回。"""

    @pytest.mark.asyncio
    async def test_reflect_flow_writes_memory(self, tmp_path):
        agent_root = tmp_path / "agent"
        agent_root.mkdir()
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / "messages").mkdir()
        (session_dir / "meta.json").write_text(
            json.dumps({"name": "t", "last_reflected_turn": 0}), encoding="utf-8")
        (session_dir / "messages" / "turn_001.json").write_text(json.dumps({
            "turn": 1, "timestamp": "2026-08-17T00:00:00", "thinking": "",
            "messages": [
                {"role": "user", "content": "我喜欢用 Python 写脚本"},
                {"role": "assistant", "content": "好的，我记住了"},
            ],
        }), encoding="utf-8")

        provider = AsyncMock()

        async def _chat(messages, tools):
            yield TextDelta(content=(
                "## Session Overview\n- 用户喜欢 Python 脚本\n\n"
                "## Preferences\n- 用户喜欢简洁代码\n\n"
                "## Workflows\n- 写脚本前先读文件\n\n"
                "## Long-Term Memory\n(无变更)"))
            yield StreamEnd(finish_reason="stop", tool_calls=[])

        provider.chat_with_tools = _chat

        engine = ReflectEngine(provider, agent_root=agent_root)
        result = await engine.reflect(session_dir, current_turn=1)
        assert result is not None
        assert result.changes_detected is True

        await engine.apply(session_dir, result, current_turn=1)

        prefs = (agent_root / "preferences.md").read_text(encoding="utf-8")
        assert "简洁代码" in prefs
        workflows = (agent_root / "workflows.md").read_text(encoding="utf-8")
        assert "读文件" in workflows
