"""Plugin Adapters — 三种格式的跨生态导入适配器。

每种适配器负责：
  1. 识别插件格式（FINGERPRINT）
  2. 提取 skills / commands / hooks / MCP 配置 / 设置
  3. 转换为 Aide 内部表示

用法:
    detector = PluginFormatDetector()
    fmt, adapter = detector.detect(plugin_dir)
    skills = await adapter.extract_skills()
    hooks = await adapter.extract_hooks()
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .manifest_v2 import PluginManifestV2, _parse_frontmatter

logger = logging.getLogger(__name__)


# ── Skill 提取后的 Aide 内部表示 ──────────────────────────────────────────

@dataclass
class ExtractedSkill:
    """适配器提取的技能 — Aide 内部表示。"""
    name: str
    description: str
    content: str          # SKILL.md 完整正文（去除 frontmatter）
    references: dict[str, str] = None  # 附加参考文件 {fname: content}


@dataclass
class ExtractedHook:
    """适配器提取的 hook 配置 — Aide 内部表示。"""
    event: str
    matcher: str
    type: str             # "command"
    command: str
    timeout: int = 60


@dataclass
class ExtractedCommand:
    """适配器提取的命令 — Aide 内部表示。"""
    name: str
    description: str
    content: str           # .md 文件完整内容


# ── Claude Code 适配器 ────────────────────────────────────────────────────


class ClaudeCodeAdapter:
    """Claude Code 插件适配器。

    目录结构:
      .claude-plugin/plugin.json    ← 识别标志
      skills/<name>/SKILL.md        ← 技能定义
      commands/<name>.md            ← 命令定义
      hooks/hooks.json              ← Hook 配置
      .mcp.json                     ← MCP server 配置
      settings.json                 ← 默认设置
    """

    FINGERPRINT = ".claude-plugin/plugin.json"

    def __init__(self, plugin_dir: Path, manifest: PluginManifestV2):
        self._dir = plugin_dir
        self._manifest = manifest

    async def extract_skills(self) -> list[ExtractedSkill]:
        skills: list[ExtractedSkill] = []
        skills_dir = self._dir / "skills"
        if not skills_dir.exists():
            return skills

        for ref in self._manifest.skills:
            skill_path = self._dir / ref.path if ref.path else None
            if skill_path is None or not skill_path.exists():
                # 尝试 skills/<name>/SKILL.md
                skill_path = skills_dir / ref.name / "SKILL.md"
            if not skill_path.exists():
                continue

            try:
                text = skill_path.read_text(encoding="utf-8")
                meta = _parse_frontmatter(text)
                body = text[text.rfind("---\n") + 4:] if text.startswith("---") else text
                body = body.strip()
            except OSError:
                continue

            # 收集 references/ 附加文件
            refs_dir = skill_path.parent / "references"
            references: dict[str, str] = {}
            if refs_dir.exists():
                for ref_file in refs_dir.glob("*"):
                    try:
                        references[ref_file.name] = ref_file.read_text(encoding="utf-8")
                    except OSError:
                        pass

            skills.append(ExtractedSkill(
                name=ref.name,
                description=meta.get("description", self._manifest.description),
                content=body,
                references=references if references else None,
            ))

        return skills

    async def extract_commands(self) -> list[ExtractedCommand]:
        commands: list[ExtractedCommand] = []
        commands_dir = self._dir / "commands"
        if not commands_dir.exists():
            return commands

        for ref in self._manifest.commands:
            cmd_path = self._dir / ref.path if ref.path else commands_dir / f"{ref.name}.md"
            if not cmd_path.exists():
                continue
            try:
                text = cmd_path.read_text(encoding="utf-8")
                meta = _parse_frontmatter(text)
                body = text[text.rfind("---\n") + 4:] if text.startswith("---") else text
            except OSError:
                continue

            commands.append(ExtractedCommand(
                name=ref.name,
                description=meta.get("description", ""),
                content=body.strip(),
            ))

        return commands

    async def extract_hooks(self) -> list[ExtractedHook]:
        hooks: list[ExtractedHook] = []
        hooks_dir = self._dir / "hooks"
        if not hooks_dir.exists():
            return hooks

        # 优先从 hooks/hooks.json 读取
        hooks_json = hooks_dir / "hooks.json"
        if not hooks_json.exists():
            return hooks

        try:
            raw = json.loads(hooks_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return hooks

        hooks_data = raw.get("hooks", raw)  # 兼容 {"hooks": {...}} 和 {...} 两种格式
        for event, hook_list in hooks_data.items():
            if not isinstance(hook_list, list):
                continue
            for entry in hook_list:
                if not isinstance(entry, dict):
                    continue
                hooks.append(ExtractedHook(
                    event=event,
                    matcher=entry.get("matcher", "*"),
                    type=entry.get("type", "command"),
                    command=entry.get("command", ""),
                    timeout=entry.get("timeout", 60),
                ))

        return hooks

    async def extract_mcp_servers(self) -> list[dict]:
        mcp_json = self._dir / ".mcp.json"
        if not mcp_json.exists():
            return []
        try:
            raw = json.loads(mcp_json.read_text(encoding="utf-8"))
            return raw.get("mcpServers", raw.get("servers", []))
        except (json.JSONDecodeError, OSError):
            return []

    async def extract_settings(self) -> dict:
        settings_json = self._dir / "settings.json"
        if not settings_json.exists():
            return {}
        try:
            return json.loads(settings_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}


# ── OpenClaw 技能适配器 ──────────────────────────────────────────────────


class OpenClawSkillAdapter:
    """OpenClaw 技能适配器。

    目录结构:
      SKILL.md              ← 识别标志（含 YAML frontmatter）
      references/           ← 可选附加文件
    """

    FINGERPRINT = "SKILL.md"

    def __init__(self, plugin_dir: Path, manifest: PluginManifestV2):
        self._dir = plugin_dir
        self._manifest = manifest

    async def extract_skills(self) -> list[ExtractedSkill]:
        skill_md = self._dir / "SKILL.md"
        if not skill_md.exists():
            return []

        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            return []

        meta = _parse_frontmatter(text)
        body = text[text.rfind("---\n") + 4:] if text.startswith("---") else text

        # references/
        refs_dir = self._dir / "references"
        references: dict[str, str] = {}
        if refs_dir.exists():
            for ref_file in refs_dir.glob("*"):
                try:
                    references[ref_file.name] = ref_file.read_text(encoding="utf-8")
                except OSError:
                    pass

        return [ExtractedSkill(
            name=self._manifest.name,
            description=meta.get("description", self._manifest.description),
            content=body.strip(),
            references=references if references else None,
        )]

    async def extract_commands(self) -> list[ExtractedCommand]:
        return []  # OpenClaw skills 没有独立的 commands/

    async def extract_hooks(self) -> list[ExtractedHook]:
        return []  # OpenClaw skills 没有 hooks 系统

    async def extract_mcp_servers(self) -> list[dict]:
        return []

    async def extract_settings(self) -> dict:
        return {}


# ── Aide 原生适配器 ──────────────────────────────────────────────────────


class AideNativeAdapter:
    """Aide 原生 Python 插件适配器。

    目录结构:
      aide.plugin.json      ← 识别标志
      __init__.py           ← Python 入口（@define_plugin）
    """

    FINGERPRINT = "aide.plugin.json"

    def __init__(self, plugin_dir: Path, manifest: PluginManifestV2):
        self._dir = plugin_dir
        self._manifest = manifest

    async def extract_skills(self) -> list[ExtractedSkill]:
        return []  # Python 插件通过 register() 提供

    async def extract_commands(self) -> list[ExtractedCommand]:
        return []  # Python 插件通过 register() 提供

    async def extract_hooks(self) -> list[ExtractedHook]:
        return []  # Python 插件通过 register_hook() 提供

    async def extract_mcp_servers(self) -> list[dict]:
        return []

    async def extract_settings(self) -> dict:
        return {}


# ── 格式检测器 ────────────────────────────────────────────────────────────


class PluginFormatDetector:
    """检测插件格式并返回对应的适配器。

    优先级: Claude Code > OpenClaw > Aide Native
    """

    ADAPTERS = [
        ("claude_code", ClaudeCodeAdapter),
        ("openclaw_skill", OpenClawSkillAdapter),
        ("aide_native", AideNativeAdapter),
    ]

    def detect(self, plugin_dir: Path) -> tuple[str, object, PluginManifestV2] | None:
        """检测插件格式。

        Returns:
            (format_name, adapter_instance, manifest) 或 None
        """
        from .manifest_v2 import detect_plugin_format

        fmt, manifest = detect_plugin_format(plugin_dir)
        if fmt == "unknown" or manifest is None:
            return None

        adapter_cls = dict(self.ADAPTERS).get(fmt)
        if adapter_cls is None:
            return None

        return (fmt, adapter_cls(plugin_dir, manifest), manifest)
