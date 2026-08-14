"""测试 adapter.py — 三种格式适配器 + PluginFormatDetector。"""

import json
import pytest
from pathlib import Path

from core.plugins.adapter import (
    PluginFormatDetector,
    ClaudeCodeAdapter,
    OpenClawSkillAdapter,
    AideNativeAdapter,
    ExtractedSkill, ExtractedHook, ExtractedCommand,
)
from core.plugins.manifest_v2 import PluginManifestV2


class TestPluginFormatDetector:
    """测试格式检测器。"""

    @pytest.fixture
    def detector(self):
        return PluginFormatDetector()

    def test_detect_claude_code(self, detector, tmp_path):
        plugin_dir = tmp_path / "cc-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(json.dumps({
            "name": "test-plugin",
            "version": "1.0.0",
        }))

        result = detector.detect(plugin_dir)
        assert result is not None
        fmt, adapter, manifest = result
        assert fmt == "claude_code"
        assert isinstance(adapter, ClaudeCodeAdapter)
        assert manifest.name == "test-plugin"

    def test_detect_openclaw_skill(self, detector, tmp_path):
        plugin_dir = tmp_path / "oc-skill"
        plugin_dir.mkdir()
        (plugin_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: A test skill\n---\n# Content",
            encoding="utf-8",
        )

        result = detector.detect(plugin_dir)
        assert result is not None
        fmt, adapter, manifest = result
        assert fmt == "openclaw_skill"
        assert isinstance(adapter, OpenClawSkillAdapter)
        assert manifest.name == "my-skill"

    def test_detect_aide_native(self, detector, tmp_path):
        plugin_dir = tmp_path / "aide-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "aide.plugin.json").write_text(json.dumps({
            "id": "native-plugin",
            "entry": "__init__.py",
        }))

        result = detector.detect(plugin_dir)
        assert result is not None
        fmt, adapter, manifest = result
        assert fmt == "aide_native"
        assert isinstance(adapter, AideNativeAdapter)
        assert manifest.name == "native-plugin"

    def test_detect_unknown(self, detector, tmp_path):
        plugin_dir = tmp_path / "empty"
        plugin_dir.mkdir()
        result = detector.detect(plugin_dir)
        assert result is None

    def test_claude_code_priority_over_openclaw(self, detector, tmp_path):
        """Claude Code 优先于 OpenClaw（当两者同时存在时）。"""
        plugin_dir = tmp_path / "dual"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(json.dumps({
            "name": "cc-first",
        }))
        (plugin_dir / "SKILL.md").write_text(
            "---\nname: oc-second\n---\n# Content",
        )

        result = detector.detect(plugin_dir)
        assert result is not None
        fmt, _, manifest = result
        assert fmt == "claude_code"
        assert manifest.name == "cc-first"


class TestClaudeCodeAdapter:
    """测试 Claude Code 适配器的提取方法。"""

    @pytest.fixture
    def cc_plugin(self, tmp_path):
        plugin_dir = tmp_path / "full-cc-plugin"
        plugin_dir.mkdir()

        # plugin.json
        (plugin_dir / ".claude-plugin").mkdir()
        manifest_json = {
            "name": "full-cc-plugin",
            "version": "2.0.0",
            "description": "A full Claude Code plugin",
            "components": {
                "skills": ["code-review"],
                "commands": ["review-cmd"],
            },
        }
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest_json))

        # skills/code-review/SKILL.md
        skills_dir = plugin_dir / "skills" / "code-review"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(
            "---\nname: code-review\ndescription: Review code for issues\n---\n# Code Review\n\nCheck for bugs.",
        )

        # commands/review-cmd.md
        cmds_dir = plugin_dir / "commands"
        cmds_dir.mkdir()
        (cmds_dir / "review-cmd.md").write_text("Review this code for issues.")

        # hooks/hooks.json
        hooks_dir = plugin_dir / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "hooks.json").write_text(json.dumps({
            "hooks": {
                "PreToolUse": [
                    {"matcher": "run_shell", "command": "echo check", "timeout": 10},
                ],
                "Stop": [
                    {"matcher": "*", "command": "echo done"},
                ],
            }
        }))

        manifest_v2 = PluginManifestV2.from_claude_plugin(plugin_dir)
        return ClaudeCodeAdapter(plugin_dir, manifest_v2)

    @pytest.mark.asyncio
    async def test_extract_skills(self, cc_plugin):
        skills = await cc_plugin.extract_skills()
        assert len(skills) == 1
        assert skills[0].name == "code-review"
        assert "bugs" in skills[0].content

    @pytest.mark.asyncio
    async def test_extract_commands(self, cc_plugin):
        commands = await cc_plugin.extract_commands()
        assert len(commands) == 1
        assert commands[0].name == "review-cmd"
        assert "Review this code" in commands[0].content

    @pytest.mark.asyncio
    async def test_extract_hooks(self, cc_plugin):
        hooks = await cc_plugin.extract_hooks()
        assert len(hooks) == 2
        events = {h.event for h in hooks}
        assert "PreToolUse" in events
        assert "Stop" in events
        pre_tool = [h for h in hooks if h.event == "PreToolUse"][0]
        assert pre_tool.matcher == "run_shell"
        assert pre_tool.command == "echo check"
        assert pre_tool.timeout == 10

    @pytest.mark.asyncio
    async def test_extract_mcp_empty_when_none(self, cc_plugin):
        servers = await cc_plugin.extract_mcp_servers()
        assert servers == []


class TestOpenClawSkillAdapter:
    """测试 OpenClaw 技能适配器。"""

    @pytest.fixture
    def oc_skill(self, tmp_path):
        plugin_dir = tmp_path / "oc-skill"
        plugin_dir.mkdir()
        (plugin_dir / "SKILL.md").write_text(
            "---\nname: pptx-skill\ndescription: Create PowerPoint presentations\n---\n"
            "# PowerPoint Skill\n\nUse python-pptx to create slides.",
        )
        manifest_v2 = PluginManifestV2.from_openclaw_skill(plugin_dir)
        return OpenClawSkillAdapter(plugin_dir, manifest_v2)

    @pytest.mark.asyncio
    async def test_extract_skills(self, oc_skill):
        skills = await oc_skill.extract_skills()
        assert len(skills) == 1
        assert skills[0].name == "pptx-skill"
        assert "python-pptx" in skills[0].content
        assert skills[0].description == "Create PowerPoint presentations"

    @pytest.mark.asyncio
    async def test_no_commands(self, oc_skill):
        commands = await oc_skill.extract_commands()
        assert commands == []

    @pytest.mark.asyncio
    async def test_no_hooks(self, oc_skill):
        hooks = await oc_skill.extract_hooks()
        assert hooks == []

    @pytest.mark.asyncio
    async def test_extract_with_references(self, oc_skill, tmp_path):
        refs_dir = oc_skill._dir / "references"
        refs_dir.mkdir()
        (refs_dir / "template.pptx").write_text("fake pptx binary")
        skills = await oc_skill.extract_skills()
        assert skills[0].references is not None
        assert "template.pptx" in skills[0].references
