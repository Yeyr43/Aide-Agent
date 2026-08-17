"""测试 ReflectEngine — 备份、版本日志、回滚。"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from core.llm_gateway import TextDelta, StreamEnd
from core.memory.version import (
    _backup_prompt, _append_version_log, rollback_prompt,
    BACKUPS_DIR, AGENT_ROOT,
)
from core.memory.reflector import (
    ReflectEngine, ReflectResult, _clean_markdown_response, _msg_text,
)


class TestBackupPrompt:
    """测试 _backup_prompt()。"""

    def test_creates_backup(self, tmp_path):
        prompt = tmp_path / "preferences.md"
        prompt.write_text("test content", encoding="utf-8")
        with patch('core.memory.version.BACKUPS_DIR', tmp_path):
            backup_name = _backup_prompt(prompt)
            assert backup_name is not None
            assert backup_name.startswith("preferences.md_")
            assert backup_name.endswith(".backup")
            backup_file = tmp_path / backup_name
            assert backup_file.exists()
            assert backup_file.read_text(encoding="utf-8") == "test content"

    def test_returns_none_for_missing_file(self, tmp_path):
        prompt = tmp_path / "nonexistent.md"
        with patch('core.memory.version.BACKUPS_DIR', tmp_path):
            result = _backup_prompt(prompt)
            assert result is None


class TestVersionLog:
    """测试 _append_version_log()。"""

    def test_creates_new_log(self, tmp_path):
        with patch('core.memory.version.BACKUPS_DIR', tmp_path):
            _append_version_log("preferences.md", "preferences.md_test.backup")
            log_path = tmp_path / "version_log.json"
            assert log_path.exists()
            log = json.loads(log_path.read_text(encoding="utf-8"))
            assert "preferences.md" in log
            assert len(log["preferences.md"]) == 1
            assert log["preferences.md"][0]["backup"] == "preferences.md_test.backup"

    def test_appends_to_existing_log(self, tmp_path):
        log_path = tmp_path / "version_log.json"
        log_path.write_text(json.dumps({"preferences.md": [{"old": "entry"}]}), encoding="utf-8")
        with patch('core.memory.version.BACKUPS_DIR', tmp_path):
            _append_version_log("preferences.md", "preferences.md_test.backup")
            log = json.loads(log_path.read_text(encoding="utf-8"))
            assert len(log["preferences.md"]) == 2


class TestRollbackPrompt:
    """测试 rollback_prompt()。"""

    def test_no_log_returns_error(self, tmp_path):
        with patch('core.memory.version.BACKUPS_DIR', tmp_path):
            success, msg = rollback_prompt("preferences", 0)
            assert not success
            assert "无版本历史" in msg or "does not exist" in msg.lower()

    def test_invalid_n_returns_error(self, tmp_path):
        log_path = tmp_path / "version_log.json"
        log_path.write_text(json.dumps({"preferences.md": []}), encoding="utf-8")
        with patch('core.memory.version.BACKUPS_DIR', tmp_path):
            success, msg = rollback_prompt("preferences", 0)
            assert not success
            assert "无备份记录" in msg or "No backup" in msg

    def test_restores_content(self, tmp_path):
        # Setup backup
        backup_name = "preferences.md_test.backup"
        backup_file = tmp_path / backup_name
        backup_file.write_text("restored content", encoding="utf-8")

        # Setup version log
        log_path = tmp_path / "version_log.json"
        log_path.write_text(json.dumps({
            "preferences.md": [{
                "timestamp": "2024-01-01T00:00:00+00:00",
                "backup": backup_name,
                "size": len("restored content"),
            }]
        }), encoding="utf-8")

        # Setup prompt
        prompt_path = tmp_path / "preferences.md"
        prompt_path.write_text("old content", encoding="utf-8")

        with patch('core.memory.version.BACKUPS_DIR', tmp_path), \
             patch('core.memory.version.AGENT_ROOT', tmp_path):
            success, msg = rollback_prompt("preferences", 0)
            assert success
            assert prompt_path.read_text(encoding="utf-8") == "restored content"

    def test_missing_backup_returns_error(self, tmp_path):
        log_path = tmp_path / "version_log.json"
        log_path.write_text(json.dumps({
            "preferences.md": [{
                "timestamp": "2024-01-01T00:00:00+00:00",
                "backup": "missing.backup",
                "size": 100,
            }]
        }), encoding="utf-8")
        with patch('core.memory.version.BACKUPS_DIR', tmp_path):
            success, msg = rollback_prompt("preferences", 0)
            assert not success
            assert "丢失" in msg or "lost" in msg.lower()


class TestReflectDiff:
    """回归测试：reflect() 的 key 归一化 + prompt 示例头格式。

    audit 发现：_parse_reflection_output 返回无 .md 后缀的 key，而
    _compute_diff / changes 用 .md key，导致 changes_detected 恒 True、
    diff 算成"整删"；且 prompt 示例用 "### ## Preferences" 头，
    split_sections 只认 "## " 前缀，LLM 照示例输出会静默丢失记忆更新。
    """

    @staticmethod
    def _setup(agent_root: Path, session_dir: Path) -> None:
        agent_root.mkdir()
        (agent_root / "preferences.md").write_text("# 偏好\n\n- 用户喜欢简洁\n", encoding="utf-8")
        (agent_root / "workflows.md").write_text("# 工作流\n\n- 用中文回复\n", encoding="utf-8")
        (agent_root / "long_term_memory.md").write_text("# 长记忆\n", encoding="utf-8")
        (session_dir / "messages").mkdir(parents=True)
        (session_dir / "messages" / "turn_001.json").write_text(json.dumps({
            "turn": 1,
            "messages": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好，有什么可以帮你？"},
            ],
        }), encoding="utf-8")
        (session_dir / "meta.json").write_text(json.dumps({"last_reflected_turn": 0}), encoding="utf-8")

    def test_no_change_returns_false_changes_and_empty_diff(self, tmp_path, monkeypatch):
        agent_root = tmp_path / "agent"
        session_dir = tmp_path / "sess"
        self._setup(agent_root, session_dir)
        engine = ReflectEngine(provider=object(), agent_root=agent_root,
                               sessions_root=tmp_path)

        no_change = (
            "## Preferences\n(无变更)\n"
            "## Workflows\n(无变更)\n"
            "## Long-Term Memory\n(无变更)\n"
        )
        async def _fake(*a, **k): return no_change
        monkeypatch.setattr(engine, "_call_llm_for_reflection", _fake)
        result = self._run(engine, session_dir)
        assert result is not None
        assert result.changes_detected is False, "无变更时不应报告有变更"
        assert result.diff == ""

    def test_change_parsed_from_section_format(self, tmp_path, monkeypatch):
        """LLM 按示例格式输出新增条目 → 能被解析进 proposed_files（防静默丢记忆）。"""
        agent_root = tmp_path / "agent"
        session_dir = tmp_path / "sess"
        self._setup(agent_root, session_dir)
        engine = ReflectEngine(provider=object(), agent_root=agent_root,
                               sessions_root=tmp_path)

        llm_out = (
            "## Preferences\n"
            "---\nid: pref_001\ncreated: 2026-08-16\n"
            "---\n- 用户喜欢中文\n"
            "## Workflows\n(无变更)\n"
            "## Long-Term Memory\n(无变更)\n"
        )
        async def _fake2(*a, **k): return llm_out
        monkeypatch.setattr(engine, "_call_llm_for_reflection", _fake2)
        result = self._run(engine, session_dir)
        assert result is not None
        assert result.changes_detected is True
        assert "用户喜欢中文" in result.proposed_files["preferences.md"]
        assert "pref_001" in result.proposed_files["preferences.md"]
        assert result.diff != ""

    def test_system_prompt_uses_parseable_section_headers(self):
        """prompt 示例头必须是 "## Preferences"（split_sections 可解析），不能是 "### ##"。"""
        from core.memory.reflector import ReflectEngine
        engine = ReflectEngine(provider=object())
        prompt = engine._build_system_prompt()
        assert "## Preferences" in prompt
        assert "### ##" not in prompt
        assert "## Workflows" in prompt

    @staticmethod
    def _run(engine, session_dir):
        import asyncio
        return asyncio.run(engine.reflect(session_dir, current_turn=1))


class TestMsgText:
    """_msg_text 提取纯文本的兼容分支。"""

    def test_string_passthrough(self):
        assert _msg_text("hello") == "hello"

    def test_multimodal_list_joins_text_parts(self):
        content = [
            {"type": "text", "text": "第一个"},
            {"type": "image_url", "url": "x"},
            "not-a-dict",
        ]
        assert _msg_text(content) == "第一个"

    def test_other_types_return_empty(self):
        assert _msg_text(None) == ""
        assert _msg_text(123) == ""


class TestReflectFlow:
    """reflect() 的提前返回分支。"""

    @pytest.mark.asyncio
    async def test_no_transcript_returns_none(self, tmp_path):
        agent_root = tmp_path / "agent"
        agent_root.mkdir()
        session_dir = tmp_path / "sess"
        session_dir.mkdir()
        engine = ReflectEngine(provider=object(), agent_root=agent_root,
                               sessions_root=tmp_path)
        result = await engine.reflect(session_dir, current_turn=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_llm_response_returns_none(self, tmp_path, monkeypatch):
        agent_root = tmp_path / "agent"
        agent_root.mkdir()
        session_dir = tmp_path / "sess"
        (session_dir / "messages").mkdir(parents=True)
        (session_dir / "messages" / "turn_001.json").write_text(json.dumps({
            "messages": [{"role": "user", "content": "你好"}],
        }), encoding="utf-8")
        engine = ReflectEngine(provider=object(), agent_root=agent_root,
                               sessions_root=tmp_path)

        async def _fake(*a, **k):
            return ""

        monkeypatch.setattr(engine, "_call_llm_for_reflection", _fake)
        result = await engine.reflect(session_dir, current_turn=1)
        assert result is None


class TestApply:
    """apply() 全流程：备份 → 原子写 → marker → 缓存刷新。"""

    @pytest.mark.asyncio
    async def test_apply_writes_changes(self, tmp_path):
        agent_root = tmp_path / "agent"
        agent_root.mkdir()
        (agent_root / "preferences.md").write_text("# 偏好\n- 旧\n", encoding="utf-8")
        (agent_root / "workflows.md").write_text("# 工作流\n- 不变\n", encoding="utf-8")
        (agent_root / "long_term_memory.md").write_text("# 长记忆\n", encoding="utf-8")
        session_dir = tmp_path / "sess"
        session_dir.mkdir()
        (session_dir / "meta.json").write_text(
            json.dumps({"last_reflected_turn": 0}), encoding="utf-8")

        result = ReflectResult(
            overview="## Session Overview\n- 总结",
            proposed_files={
                "preferences.md": "# 偏好\n- 新条目\n",
                "workflows.md": "# 工作流\n- 不变\n",
                "long_term_memory.md": "# 长记忆\n",
            },
            current_files={
                "preferences.md": "# 偏好\n- 旧\n",
                "workflows.md": "# 工作流\n- 不变\n",
                "long_term_memory.md": "# 长记忆\n",
            },
            diff="dummy",
            changes_detected=True,
        )

        flushed = []
        engine = ReflectEngine(provider=object(), agent_root=agent_root,
                               sessions_root=tmp_path)
        engine._on_cache_flush = lambda: flushed.append(True)
        with patch("core.memory.version.BACKUPS_DIR", tmp_path / "backups"):
            await engine.apply(session_dir, result, 5)

        # 变更文件写入，无变更文件跳过
        assert (agent_root / "preferences.md").read_text(encoding="utf-8") == "# 偏好\n- 新条目\n"
        assert (agent_root / "workflows.md").read_text(encoding="utf-8") == "# 工作流\n- 不变\n"
        # 备份 + 版本日志
        backups = tmp_path / "backups"
        assert list(backups.glob("preferences.md_*.backup"))
        assert (backups / "version_log.json").exists()
        # overview 检查点
        cps = json.loads((session_dir / "overview.json").read_text(encoding="utf-8"))
        assert cps[-1]["to_turn"] == 5
        assert cps[-1]["overview_md"] == "## Session Overview\n- 总结"
        # 反思 marker
        meta = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["last_reflected_turn"] == 5
        assert flushed == [True]


class TestReadCurrentMemory:
    def test_oserror_falls_back_empty(self, tmp_path):
        agent_root = tmp_path / "agent"
        agent_root.mkdir()
        # 目录 → read_text 抛 OSError → 记为空
        (agent_root / "preferences.md").mkdir()
        (agent_root / "workflows.md").write_text("# 工作流\n", encoding="utf-8")
        engine = ReflectEngine(provider=object(), agent_root=agent_root,
                               sessions_root=tmp_path)
        memory = engine._read_current_memory()
        assert memory["preferences.md"] == ""
        assert memory["workflows.md"] == "# 工作流\n"


class TestReflectionMarker:
    """_read_reflection_marker 的旧标记文件回退路径。"""

    @staticmethod
    def _engine(tmp_path):
        return ReflectEngine(provider=object(), agent_root=tmp_path / "agent",
                             sessions_root=tmp_path)

    def test_corrupt_meta_falls_back_to_legacy(self, tmp_path):
        session_dir = tmp_path / "sess"
        session_dir.mkdir()
        (session_dir / "meta.json").write_text("{bad", encoding="utf-8")
        (session_dir / ".reflection_marker").write_text(
            json.dumps({"last_turn": 4}), encoding="utf-8")
        assert self._engine(tmp_path)._read_reflection_marker(session_dir) == 4

    def test_corrupt_legacy_returns_zero(self, tmp_path):
        session_dir = tmp_path / "sess"
        session_dir.mkdir()
        (session_dir / "meta.json").write_text("{bad", encoding="utf-8")
        (session_dir / ".reflection_marker").write_text("not json", encoding="utf-8")
        assert self._engine(tmp_path)._read_reflection_marker(session_dir) == 0

    def test_no_marker_files_returns_zero(self, tmp_path):
        session_dir = tmp_path / "sess"
        session_dir.mkdir()
        assert self._engine(tmp_path)._read_reflection_marker(session_dir) == 0


class TestReadRecentTurns:
    def test_builds_transcript_skips_bad_turns(self, tmp_path):
        session_dir = tmp_path / "sess"
        messages = session_dir / "messages"
        messages.mkdir(parents=True)
        # turn_001：非 dict 消息 + 多模态 user + assistant 带 tool_calls（含无名调用）
        (messages / "turn_001.json").write_text(json.dumps({
            "turn": 1,
            "messages": [
                "not-a-dict",
                {"role": "user", "content": [
                    {"type": "text", "text": "你好"},
                    {"type": "image_url", "url": "x"},
                ]},
                {"role": "assistant", "content": "收到",
                 "tool_calls": [
                     {"id": "1", "name": "run_shell", "function": {"name": "run_shell"}},
                     {"function": {"name": "search_chat"}},
                     {"type": "function", "arguments": "{}"},
                 ]},
            ],
        }), encoding="utf-8")
        # turn_002 缺失 → continue
        # turn_004 损坏 JSON → except 跳过
        (messages / "turn_004.json").write_text("{invalid json", encoding="utf-8")

        engine = ReflectEngine(provider=object(), agent_root=tmp_path / "agent",
                               sessions_root=tmp_path)
        transcript = engine._read_recent_turns(session_dir, since_turn=0, to_turn=5)
        assert "--- Turn 1 ---" in transcript
        assert "User: 你好" in transcript
        assert "[工具调用: run_shell, search_chat]" in transcript
        assert "Assistant: 收到" in transcript
        assert "Turn 4" not in transcript

    def test_old_format_top_level_fields(self, tmp_path):
        """旧格式：无 messages 列表，顶层 user/assistant 字段回退。"""
        session_dir = tmp_path / "sess"
        messages = session_dir / "messages"
        messages.mkdir(parents=True)
        (messages / "turn_001.json").write_text(json.dumps({
            "turn": 1,
            "user": "旧格式提问",
            "assistant": "旧格式回答",
        }), encoding="utf-8")

        engine = ReflectEngine(provider=object(), agent_root=tmp_path / "agent",
                               sessions_root=tmp_path)
        transcript = engine._read_recent_turns(session_dir, since_turn=0, to_turn=1)
        assert "User: 旧格式提问" in transcript
        assert "Assistant: 旧格式回答" in transcript


class TestParseReflectionOutput:
    """_parse_reflection_output 的 section 标题分支。"""

    @staticmethod
    def _engine(tmp_path):
        return ReflectEngine(provider=object(), agent_root=tmp_path / "agent",
                             sessions_root=tmp_path)

    def test_all_sections_prepend_headers(self, tmp_path):
        empty = {"preferences.md": "", "workflows.md": "", "long_term_memory.md": ""}
        raw = (
            "## Session Overview\n- 总结\n"
            "## Preferences\n- 中文\n"
            "## Workflows\n- 测试\n"
            "## Long-Term Memory\n- 记住\n"
        )
        parsed = self._engine(tmp_path)._parse_reflection_output(raw, empty)
        assert parsed["overview"] == "## Session Overview\n\n- 总结"
        assert parsed["preferences"].startswith("# 偏好")
        assert parsed["workflows"].startswith("# 工作流")
        assert parsed["long_term_memory"].startswith("# 长记忆")

    def test_section_already_starts_with_hash(self, tmp_path):
        empty = {"preferences.md": "", "workflows.md": "", "long_term_memory.md": ""}
        raw = "## Workflows\n# 工作流\n- x\n"
        parsed = self._engine(tmp_path)._parse_reflection_output(raw, empty)
        assert parsed["workflows"] == "# 工作流\n- x"


class TestCallLlm:
    """_call_llm_for_reflection 成功与异常分支。"""

    @pytest.mark.asyncio
    async def test_success_cleans_response(self, tmp_path):
        class FakeProvider:
            async def chat_with_tools(self, messages, tools):
                yield TextDelta("```markdown\n## Preferences\n- 中文\n```")
                yield StreamEnd("stop", [])

        session_dir = tmp_path / "sess"
        engine = ReflectEngine(provider=FakeProvider(), agent_root=tmp_path / "agent",
                               sessions_root=tmp_path)
        text = await engine._call_llm_for_reflection(
            {"preferences.md": ""}, "新对话",
            "## Session Overview\n- 旧总览", session_dir)
        assert text == "## Preferences\n- 中文"

    @pytest.mark.asyncio
    async def test_type_error_returns_none(self, tmp_path):
        class SyncGenProvider:
            def chat_with_tools(self, messages, tools):
                yield TextDelta("x")  # 同步生成器 → async for 抛 TypeError

        engine = ReflectEngine(provider=SyncGenProvider(), agent_root=tmp_path / "agent",
                               sessions_root=tmp_path)
        result = await engine._call_llm_for_reflection({}, "t", "", tmp_path / "sess")
        assert result is None

    @pytest.mark.asyncio
    async def test_generic_error_returns_none(self, tmp_path):
        class RaisingProvider:
            async def chat_with_tools(self, messages, tools):
                raise RuntimeError("boom")
                yield  # pragma: no cover

        engine = ReflectEngine(provider=RaisingProvider(), agent_root=tmp_path / "agent",
                               sessions_root=tmp_path)
        result = await engine._call_llm_for_reflection({}, "t", "", tmp_path / "sess")
        assert result is None


class TestBuildUserPrompt:
    def test_with_existing_overview(self, tmp_path):
        session_dir = tmp_path / "sess"
        engine = ReflectEngine(provider=object(), agent_root=tmp_path / "agent",
                               sessions_root=tmp_path)
        prompt = engine._build_user_prompt(
            {"preferences.md": "- 偏好", "workflows.md": "", "long_term_memory.md": ""},
            "新对话", "## Session Overview\n- 旧总览", session_dir)
        assert "已有会话总览" in prompt
        assert "sess" in prompt  # session_id 注入
        assert "新对话" in prompt

    def test_without_existing_overview(self, tmp_path):
        engine = ReflectEngine(provider=object(), agent_root=tmp_path / "agent",
                               sessions_root=tmp_path)
        prompt = engine._build_user_prompt({}, "新对话", "", None)
        assert "已有会话总览" not in prompt
        assert "?" in prompt  # session_dir 为 None → 占位符


class TestAppendCheckpoint:
    """_append_checkpoint 的去重与读失败回退。"""

    @staticmethod
    def _engine(tmp_path):
        return ReflectEngine(provider=object(), agent_root=tmp_path / "agent",
                             sessions_root=tmp_path)

    def test_dedup_same_turn(self, tmp_path):
        session_dir = tmp_path / "sess"
        session_dir.mkdir()
        (session_dir / "overview.json").write_text(
            '{"to_turn": 2, "overview_md": "v2"}\n'
            '{"to_turn": 3, "overview_md": "old-v3"}\n',
            encoding="utf-8")
        self._engine(tmp_path)._append_checkpoint(session_dir, "new-v3", 3)
        cps = json.loads((session_dir / "overview.json").read_text(encoding="utf-8"))
        assert [c["to_turn"] for c in cps] == [2, 3]
        assert cps[-1]["overview_md"] == "new-v3"

    def test_read_error_falls_back(self, tmp_path, monkeypatch):
        session_dir = tmp_path / "sess"
        session_dir.mkdir()
        (session_dir / "overview.json").write_text("[1]", encoding="utf-8")

        def _boom(path):
            raise ValueError("corrupt")

        monkeypatch.setattr("core.storage.read_jsonl", _boom)
        self._engine(tmp_path)._append_checkpoint(session_dir, "fresh", 1)
        cps = json.loads((session_dir / "overview.json").read_text(encoding="utf-8"))
        assert [c["to_turn"] for c in cps] == [1]


class TestCleanMarkdown:
    def test_full_fence(self):
        assert _clean_markdown_response("```markdown\n# 标题\n```") == "# 标题"

    def test_unclosed_fence_stripped(self):
        out = _clean_markdown_response("```python\ncode\n```\nextra")
        # 非完整包裹：仅剥掉开头 ```，正文保留
        assert out == "python\ncode\n```\nextra"

    def test_plain_text(self):
        assert _clean_markdown_response("  纯文本  ") == "纯文本"
