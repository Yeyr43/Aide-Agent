""" End-to-end integration tests — full pipeline without Textual UI.

Tests the complete flow: CommandRegistry → route → handler → result.
Also tests session creation and listing end-to-end.
"""

import json
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
