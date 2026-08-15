"""AutoMemoryExtractor — 自动记忆提取测试。"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.llm_gateway import TextDelta, StreamEnd
from core.memory.auto import AutoMemoryExtractor


class FakeProvider:
    """返回固定响应的假 provider（记录调用次数）。"""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    async def chat_with_tools(self, messages, tools):
        self.calls += 1
        yield TextDelta(self._response)
        yield StreamEnd("stop", [])


def _make_agent_dir(tmp_path: Path) -> Path:
    agent_root = tmp_path / "agent"
    agent_root.mkdir(parents=True)
    return agent_root


def _make_session(tmp_path: Path) -> Path:
    session_dir = tmp_path / "sessions" / "20260815_120000"
    session_dir.mkdir(parents=True)
    return session_dir


@pytest.fixture(autouse=True)
def _fresh_lock():
    """每个测试前重置模块级写锁（避免跨事件循环复用绑定报错）。"""
    import core.memory.auto as auto_mod
    auto_mod._write_lock = asyncio.Lock()
    yield


class TestParseSections:
    def test_three_sections(self):
        ex = AutoMemoryExtractor(None)
        parsed = ex._parse_sections(
            "## Preferences\n- a\n- b\n"
            "## Workflows\n- c\n"
            "## Long-Term Memory\n- d\n"
        )
        assert parsed == {
            "preferences": ["a", "b"],
            "workflows": ["c"],
            "long_term_memory": ["d"],
        }

    def test_empty_and_no_changes_skipped(self):
        ex = AutoMemoryExtractor(None)
        parsed = ex._parse_sections(
            "## Preferences\n- (无变更)\n## Workflows\n- keep\n"
        )
        assert parsed["preferences"] == []
        assert parsed["workflows"] == ["keep"]

    def test_no_sections(self):
        ex = AutoMemoryExtractor(None)
        parsed = ex._parse_sections("无记忆信号")
        assert parsed == {
            "preferences": [], "workflows": [], "long_term_memory": [],
        }


class TestExtraction:
    @pytest.mark.asyncio
    async def test_disabled_returns_false(self, tmp_path):
        """开关 off → 不调 LLM、不写文件。"""
        agent_root = _make_agent_dir(tmp_path)
        provider = FakeProvider("## Preferences\n- x")
        ex = AutoMemoryExtractor(provider, agent_root=agent_root)
        with patch("core.config.Config.load_settings",
                   return_value={"app": {"auto_memory": False}}):
            wrote = await ex.maybe_extract(_make_session(tmp_path), 3,
                                           "问题", "回复")
        assert wrote is False
        assert provider.calls == 0

    @pytest.mark.asyncio
    async def test_no_reply_skips(self, tmp_path):
        """无最终回复 → 不提取。"""
        agent_root = _make_agent_dir(tmp_path)
        provider = FakeProvider("## Preferences\n- x")
        ex = AutoMemoryExtractor(provider, agent_root=agent_root)
        with patch("core.config.Config.load_settings",
                   return_value={"app": {"auto_memory": True}}):
            wrote = await ex.maybe_extract(_make_session(tmp_path), 3,
                                           "问题", "")
        assert wrote is False
        assert provider.calls == 0

    @pytest.mark.asyncio
    async def test_reflected_through_skips(self, tmp_path):
        """本轮已被 /reflect 覆盖（last_reflected_turn >= turn）→ 跳过。"""
        agent_root = _make_agent_dir(tmp_path)
        session_dir = _make_session(tmp_path)
        (session_dir / "meta.json").write_text(
            json.dumps({"last_reflected_turn": 5}), encoding="utf-8")
        provider = FakeProvider("## Preferences\n- x")
        ex = AutoMemoryExtractor(provider, agent_root=agent_root)
        with patch("core.config.Config.load_settings",
                   return_value={"app": {"auto_memory": True}}):
            wrote = await ex.maybe_extract(session_dir, 3, "u", "a")
        assert wrote is False
        assert provider.calls == 0

    @pytest.mark.asyncio
    async def test_appends_preferences_and_workflows(self, tmp_path):
        """on + 有回复 → 追加到对应文件，格式正确，marker 更新。"""
        agent_root = _make_agent_dir(tmp_path)
        session_dir = _make_session(tmp_path)
        provider = FakeProvider(
            "## Preferences\n- 用户喜欢简洁回复\n"
            "## Workflows\n- 写代码先跑测试\n"
        )
        ex = AutoMemoryExtractor(provider, agent_root=agent_root)
        with patch("core.config.Config.load_settings",
                   return_value={"app": {"auto_memory": True}}):
            wrote = await ex.maybe_extract(session_dir, 3, "问题", "简洁回复更好。")
        assert wrote is True

        pref = (agent_root / "preferences.md").read_text(encoding="utf-8")
        assert "用户喜欢简洁回复" in pref
        assert "id: pref_a003_0" in pref
        assert "source: 20260815_120000/turn_3" in pref
        assert "created: " in pref

        wf = (agent_root / "workflows.md").read_text(encoding="utf-8")
        assert "写代码先跑测试" in wf
        assert "id: wf_a003_0" in wf

        # 无信号的 ltm 不写
        assert not (agent_root / "long_term_memory.md").exists()

        # marker 更新
        meta = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["last_auto_memory_turn"] == 3

    @pytest.mark.asyncio
    async def test_dedup_and_id_increment(self, tmp_path):
        """已有内容去重 + 新 id 不与现有冲突。"""
        agent_root = _make_agent_dir(tmp_path)
        (agent_root / "preferences.md").write_text(
            "---\nid: pref_a003_0\ncreated: 2026-08-01\n---\n- 旧偏好\n",
            encoding="utf-8")
        session_dir = _make_session(tmp_path)
        provider = FakeProvider("## Preferences\n- 旧偏好\n- 新偏好\n")
        ex = AutoMemoryExtractor(provider, agent_root=agent_root)
        with patch("core.config.Config.load_settings",
                   return_value={"app": {"auto_memory": True}}):
            wrote = await ex.maybe_extract(session_dir, 3, "u", "a")
        assert wrote is True
        pref = (agent_root / "preferences.md").read_text(encoding="utf-8")
        assert "旧偏好" in pref
        assert "新偏好" in pref
        assert "id: pref_a003_1" in pref  # 递增避开已占用 id

    @pytest.mark.asyncio
    async def test_llm_failure_silent(self, tmp_path):
        """LLM 调用异常 → 静默返回 False，不写文件。"""
        agent_root = _make_agent_dir(tmp_path)

        class BrokenProvider:
            async def chat_with_tools(self, messages, tools):
                raise RuntimeError("boom")
                yield  # pragma: no cover

        ex = AutoMemoryExtractor(BrokenProvider(), agent_root=agent_root)
        with patch("core.config.Config.load_settings",
                   return_value={"app": {"auto_memory": True}}):
            wrote = await ex.maybe_extract(_make_session(tmp_path), 3, "u", "a")
        assert wrote is False
