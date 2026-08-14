"""PluginManifestV2 — Claude Code / OpenClaw 兼容 manifest。

支持三种来源格式：
  1. .claude-plugin/plugin.json → Claude Code 插件
  2. SKILL.md（YAML frontmatter + Markdown body）→ OpenClaw 技能
  3. aide.plugin.json + __init__.py → Aide 原生 Python 插件

所有三种格式解析为统一的 PluginManifestV2，后续 Pipeline 一视同仁。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


# ── YAML frontmatter 解析（复用 entries.py 的简化版）──────────────────

_FRONTMATTER_RE = re.compile(
    r'^---\s*\n(.*?)\n---',
    re.DOTALL,
)


def _parse_frontmatter(text: str) -> dict[str, str]:
    """解析 YAML frontmatter — 返回 key: value 字典。

    委托给 core.memory.entries._parse_simple_frontmatter，统一解析逻辑。
    """
    from core.memory.entries import _parse_simple_frontmatter
    meta, _ = _parse_simple_frontmatter(text)
    return meta


# ── 子结构 ────────────────────────────────────────────────────────────────


@dataclass
class SkillRef:
    """技能引用 — 指向 skills/<name>/SKILL.md。"""
    name: str
    description: str = ""
    path: str = ""   # 相对路径，如 skills/code-review/SKILL.md


@dataclass
class CommandRef:
    """命令引用 — 指向 commands/<name>.md。"""
    name: str
    description: str = ""
    path: str = ""


@dataclass
class HookRef:
    """Hook 引用 — 指向 hooks/hooks.json 中的一条配置。"""
    event: str           # "PreToolUse" | "PostToolUse" | ...
    matcher: str = "*"   # 工具名匹配模式
    type: str = "command"
    command: str = ""
    timeout: int = 60


# ── PluginManifestV2 ───────────────────────────────────────────────────────


@dataclass
class PluginManifestV2:
    """与 Claude Code plugin.json 兼容的插件 manifest。

    Claude Code 标准字段 + aide 扩展字段。
    Claude Code 解析时忽略未知字段，Aide 解析时提取 aide.* 扩展。
    """

    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    license: str = ""
    homepage: str = ""

    # Claude Code components
    skills: list[SkillRef] = field(default_factory=list)
    commands: list[CommandRef] = field(default_factory=list)
    hooks: list[HookRef] = field(default_factory=list)

    # Aide 扩展（来自 plugin.json 的 aide 字段）
    aide_min_api_version: str = ""
    aide_python_entry: str = ""        # __init__.py 路径
    aide_permissions: list[str] = field(default_factory=list)
    aide_requires: dict = field(default_factory=dict)

    # 内部
    root_dir: Path = field(default_factory=Path)
    format: str = ""  # "claude_code" | "openclaw_skill" | "aide_native"

    # ── 工厂方法 ────────────────────────────────────────────────────────

    @classmethod
    def from_claude_plugin(cls, plugin_dir: Path) -> "PluginManifestV2 | None":
        """从 .claude-plugin/plugin.json 解析 Claude Code 插件。"""
        manifest_path = plugin_dir / ".claude-plugin" / "plugin.json"
        if not manifest_path.exists():
            return None

        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        # 解析 components
        components = raw.get("components", {})
        skills = cls._parse_skills(plugin_dir, components.get("skills", []))
        commands = cls._parse_commands(plugin_dir, components.get("commands", []))
        hooks = cls._parse_hooks(components.get("hooks", []))

        # Aide 扩展
        aide = raw.get("aide", {})

        return cls(
            name=raw.get("name", plugin_dir.name),
            version=raw.get("version", "0.0.0"),
            description=raw.get("description", ""),
            author=raw.get("author", ""),
            license=raw.get("license", ""),
            homepage=raw.get("homepage", ""),
            skills=skills,
            commands=commands,
            hooks=hooks,
            aide_min_api_version=aide.get("minApiVersion", ""),
            aide_python_entry=aide.get("pythonPlugin", ""),
            aide_permissions=aide.get("permissions", []),
            aide_requires=aide.get("requires", {}),
            root_dir=plugin_dir,
            format="claude_code",
        )

    @classmethod
    def from_openclaw_skill(cls, plugin_dir: Path) -> "PluginManifestV2 | None":
        """从 SKILL.md 解析 OpenClaw 技能。"""
        skill_md = plugin_dir / "SKILL.md"
        if not skill_md.exists():
            return None

        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            return None

        meta = _parse_frontmatter(text)
        if "name" not in meta:
            return None

        # 单个 skill — SKILL.md 本身就是技能定义
        skill = SkillRef(
            name=meta["name"],
            description=meta.get("description", ""),
            path="SKILL.md",
        )

        # OpenClaw metadata（ClawHub compat / Nix 等）
        metadata_raw = meta.get("metadata", "")
        requires: dict = {}
        if metadata_raw:
            try:
                # metadata 可能是 JSON 字符串或 YAML 内联
                requires = json.loads(metadata_raw) if metadata_raw.startswith("{") else {}
            except json.JSONDecodeError:
                pass

        return cls(
            name=meta["name"],
            version=meta.get("version", "1.0.0"),
            description=meta.get("description", ""),
            skills=[skill],
            aide_requires=requires,
            root_dir=plugin_dir,
            format="openclaw_skill",
        )

    @classmethod
    def from_aide_native(cls, plugin_dir: Path) -> "PluginManifestV2 | None":
        """从 aide.plugin.json + __init__.py 解析 Aide 原生插件。"""
        manifest_path = plugin_dir / "aide.plugin.json"
        if not manifest_path.exists():
            return None

        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        return cls(
            name=raw.get("id", plugin_dir.name),
            version=raw.get("version", "0.1.0"),
            description=raw.get("description", ""),
            author=raw.get("author", ""),
            aide_python_entry=raw.get("entry", "__init__.py"),
            aide_permissions=raw.get("permissions", []),
            aide_requires=raw.get("requires", {}),
            root_dir=plugin_dir,
            format="aide_native",
        )

    # ── 辅助 ────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_skills(plugin_dir: Path, skill_names: list[str]) -> list[SkillRef]:
        """扫描 skills/ 子目录，读取每个 SKILL.md 的 frontmatter。"""
        refs: list[SkillRef] = []
        skills_dir = plugin_dir / "skills"
        if not skills_dir.exists():
            return refs

        for name in skill_names:
            skill_dir = skills_dir / name
            skill_md = skill_dir / "SKILL.md" if skill_dir.is_dir() else None
            if skill_md is None:
                # name 可能直接是 SKILL.md 路径
                skill_md = plugin_dir / name

            if not skill_md or not skill_md.exists():
                refs.append(SkillRef(name=name, path=f"skills/{name}/SKILL.md"))
                continue

            try:
                meta = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
                refs.append(SkillRef(
                    name=name,
                    description=meta.get("description", ""),
                    path=str(skill_md.relative_to(plugin_dir)),
                ))
            except OSError:
                refs.append(SkillRef(name=name, path=f"skills/{name}/SKILL.md"))

        return refs

    @staticmethod
    def _parse_commands(plugin_dir: Path, cmd_names: list[str]) -> list[CommandRef]:
        """扫描 commands/ 目录。"""
        refs: list[CommandRef] = []
        commands_dir = plugin_dir / "commands"
        for name in cmd_names:
            # name 可以是 "review" 或 "review.md"
            cmd_name = name.replace(".md", "")
            md_path = commands_dir / f"{cmd_name}.md"
            actual_path = str(md_path.relative_to(plugin_dir)) if md_path.exists() else f"commands/{cmd_name}.md"
            refs.append(CommandRef(name=cmd_name, path=actual_path))
        return refs

    @staticmethod
    def _parse_hooks(hook_paths: list[str]) -> list[HookRef]:
        """解析 hooks/hooks.json → HookRef 列表。"""
        refs: list[HookRef] = []
        for path_str in hook_paths:
            # path_str 是相对路径如 "hooks/hooks.json"
            # 实际解析在 adapter 中完成（需要 root_dir）
            refs.append(HookRef(event="", matcher="*", command=path_str))
        return refs


# ── 格式检测 ──────────────────────────────────────────────────────────────


def detect_plugin_format(plugin_dir: Path) -> tuple[str, PluginManifestV2 | None]:
    """检测插件格式并返回对应的 manifest。

    优先级：
      1. .claude-plugin/plugin.json → Claude Code
      2. SKILL.md（含 name: frontmatter）→ OpenClaw skill
      3. aide.plugin.json + __init__.py → Aide native

    Returns:
        (format_name, manifest) 或 ("unknown", None)
    """
    # 1. Claude Code
    manifest = PluginManifestV2.from_claude_plugin(plugin_dir)
    if manifest is not None:
        return ("claude_code", manifest)

    # 2. OpenClaw skill
    manifest = PluginManifestV2.from_openclaw_skill(plugin_dir)
    if manifest is not None:
        return ("openclaw_skill", manifest)

    # 3. Aide native
    manifest = PluginManifestV2.from_aide_native(plugin_dir)
    if manifest is not None:
        return ("aide_native", manifest)

    return ("unknown", None)
