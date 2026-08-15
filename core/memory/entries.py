"""MemoryEntry — 结构化记忆条目 + 解析器。

支持带 YAML frontmatter 的记忆条目格式：

    ---
    id: pref_001
    created: 2026-07-15
    source: 20260715_120000/turn_3
    ---
    - 用户喜欢简洁回复

兼容无 frontmatter 的旧格式（bare "- " 条目）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── frontmatter 解析 ──────────────────────────────────────────────────

# 匹配 "---\n key: value\n ... \n---" 块
_FRONTMATTER_RE = re.compile(
    r'^---\s*\n(.*?)\n---\s*\n',
    re.DOTALL,
)


def split_sections(text: str) -> dict[str, list[str]]:
    """将 Markdown 按 ## 标题分割为 {标题: 原始行列表}。

    公共原语（统一 reflector / auto / overview 三处同款 section 解析）。
    保留每个标题下的原始行（含空行），由调用方决定如何提取结构。

    Returns:
        dict like {"话题": ["- ...", ""], ...}（重复标题合并到同一 key）
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip()
            if current not in sections:
                sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _parse_simple_frontmatter(text: str) -> tuple[dict, str]:
    """解析简单的 YAML-like frontmatter（无依赖，仅支持 key: value 格式）。

    Args:
        text: 以 ---...--- 开头的文本块

    Returns:
        (meta_dict, remaining_text)
    """
    meta: dict[str, str] = {}
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return meta, text

    yaml_block = m.group(1)
    remaining = text[m.end():]

    for line in yaml_block.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                meta[key] = val

    return meta, remaining


# ── MemoryEntry ────────────────────────────────────────────────────────


@dataclass
class MemoryEntry:
    """一条结构化的记忆条目。

    Attributes:
        id: 稳定标识符（如 "pref_001"），旧条目为空字符串
        content: 条目内容（去掉了 "- " 前缀和 frontmatter）
        file: 所属记忆文件（"preferences.md" / "workflows.md" / "long_term_memory.md"）
        created: 创建时间
        source: 来源会话/轮次
        weight: 权重（feedback 调整后）
        deviations: 偏离次数
    """
    id: str = ""
    content: str = ""
    file: str = ""
    created: str = ""
    source: str = ""
    weight: float = 1.0
    deviations: int = 0

    @property
    def has_meta(self) -> bool:
        """是否有结构化元数据（非旧格式）。"""
        return bool(self.id)


# ── 解析器 ────────────────────────────────────────────────────────────


def parse_memory_file(text: str, filename: str = "") -> list[MemoryEntry]:
    """解析记忆文件（.md），提取所有结构化条目。

    支持两种格式：
      - 新格式：---\\n id: ... \\n---\\n- 内容
      - 旧格式：bare "- 内容"（生成临时 id）

    Args:
        text: 记忆文件的完整 Markdown 内容
        filename: 文件名（用于填充 entry.file）

    Returns:
        MemoryEntry 列表
    """
    entries: list[MemoryEntry] = []
    lines = text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # 跳过标题行和空行
        if not line or line.startswith("#") or line.startswith("<!--"):
            i += 1
            continue

        # 检测 frontmatter 块开始
        if line == "---":
            # 收集 frontmatter + content
            block = "\n".join(lines[i:])
            meta, rest = _parse_simple_frontmatter(block)
            # 找 "- " 开头的内容行
            rest_lines = rest.split("\n")
            content_parts = []
            for rl in rest_lines:
                rl_stripped = rl.strip()
                if rl_stripped.startswith("- "):
                    content_parts.append(rl_stripped[2:].strip())
                    break  # 只取第一条
            entries.append(MemoryEntry(
                id=meta.get("id", ""),
                content=content_parts[0] if content_parts else "",
                file=filename,
                created=meta.get("created", ""),
                source=meta.get("source", ""),
                weight=float(meta.get("weight", 1.0)),
                deviations=int(meta.get("deviations", 0)),
            ))
            # 跳过已处理的 frontmatter 块
            i += block.count("\n") + 1
            continue

        # "- " 前缀条目
        if line.startswith("- "):
            content = line[2:].strip()
            if content:
                entries.append(MemoryEntry(content=content, file=filename))
        else:
            # 旧格式兼容：bare content line（无 "- " 前缀）
            entries.append(MemoryEntry(content=line, file=filename))
        i += 1

    return entries


def format_memory_entry(entry: MemoryEntry) -> str:
    """将 MemoryEntry 格式化为带 frontmatter 的 Markdown 字符串。"""
    lines = ["---"]
    if entry.id:
        lines.append(f"id: {entry.id}")
    if entry.created:
        lines.append(f"created: {entry.created}")
    if entry.source:
        lines.append(f"source: {entry.source}")
    if entry.weight != 1.0:
        lines.append(f"weight: {entry.weight}")
    if entry.deviations > 0:
        lines.append(f"deviations: {entry.deviations}")
    lines.append("---")
    lines.append(f"- {entry.content}")
    return "\n".join(lines)
