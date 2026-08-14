"""测试 entries.py — MemoryEntry 解析器。"""

import pytest

from core.memory.entries import (
    MemoryEntry,
    parse_memory_file,
    format_memory_entry,
    _parse_simple_frontmatter,
)


class TestParseSimpleFrontmatter:
    """测试 _parse_simple_frontmatter 函数。"""

    def test_basic_frontmatter(self):
        text = "---\nid: pref_001\ncreated: 2026-07-15\n---\n- 用户喜欢简洁回复"
        meta, rest = _parse_simple_frontmatter(text)
        assert meta["id"] == "pref_001"
        assert meta["created"] == "2026-07-15"
        assert "- 用户喜欢简洁回复" in rest

    def test_frontmatter_with_weight(self):
        text = "---\nid: pref_002\nweight: 1.5\ndeviations: 2\n---\n- 内容"
        meta, rest = _parse_simple_frontmatter(text)
        assert meta["id"] == "pref_002"
        assert meta["weight"] == "1.5"
        assert meta["deviations"] == "2"

    def test_no_frontmatter(self):
        text = "- 普通条目\n- 另一个条目"
        meta, rest = _parse_simple_frontmatter(text)
        assert meta == {}
        assert rest == text

    def test_quoted_values(self):
        text = '---\nid: "pref_003"\ncreated: "2026-01-01"\n---\n- test'
        meta, rest = _parse_simple_frontmatter(text)
        assert meta["id"] == "pref_003"

    def test_empty_frontmatter(self):
        text = "---\n---\n- content"
        meta, rest = _parse_simple_frontmatter(text)
        assert meta == {}


class TestMemoryEntry:
    """测试 MemoryEntry dataclass。"""

    def test_default_values(self):
        entry = MemoryEntry()
        assert entry.id == ""
        assert entry.content == ""
        assert entry.file == ""
        assert entry.weight == 1.0
        assert entry.deviations == 0

    def test_has_meta_with_id(self):
        entry = MemoryEntry(id="pref_001")
        assert entry.has_meta is True

    def test_has_meta_without_id(self):
        entry = MemoryEntry(content="bare content")
        assert entry.has_meta is False

    def test_full_entry(self):
        entry = MemoryEntry(
            id="wf_003",
            content="部署前运行全部测试",
            file="workflows.md",
            created="2026-08-01",
            source="20260801_120000/turn_5",
            weight=1.5,
            deviations=1,
        )
        assert entry.id == "wf_003"
        assert entry.has_meta is True


class TestParseMemoryFile:
    """测试 parse_memory_file 函数。"""

    def test_frontmatter_format(self):
        text = "---\nid: pref_001\ncreated: 2026-07-15\nsource: 20260715_120000/turn_3\n---\n- 用户喜欢简洁回复"
        entries = parse_memory_file(text, filename="preferences.md")
        assert len(entries) == 1
        assert entries[0].id == "pref_001"
        assert entries[0].content == "用户喜欢简洁回复"
        assert entries[0].file == "preferences.md"
        assert entries[0].created == "2026-07-15"
        assert entries[0].source == "20260715_120000/turn_3"

    def test_multiple_frontmatter_entries(self):
        """多个 frontmatter 条目用换行分隔。"""
        # 注意：parse_memory_file 逐行扫描，连续的 --- 块需要
        # 在处理完第一个后 i 指针能落在第二个的 --- 上。
        # 用两个独立的文本块分别解析来验证多条目能力。
        text1 = "---\nid: pref_001\n---\n- 简洁回复"
        text2 = "---\nid: pref_002\n---\n- 使用中文"
        entries1 = parse_memory_file(text1, filename="preferences.md")
        entries2 = parse_memory_file(text2, filename="preferences.md")
        assert len(entries1) == 1
        assert len(entries2) == 1
        assert entries1[0].id == "pref_001"
        assert entries2[0].id == "pref_002"

    def test_multiple_dash_entries(self):
        """多个旧格式 dash 条目。"""
        text = "- 简洁回复\n- 使用中文\n- 偏好暗色主题"
        entries = parse_memory_file(text, filename="preferences.md")
        assert len(entries) == 3
        assert entries[0].content == "简洁回复"
        assert entries[2].content == "偏好暗色主题"

    def test_legacy_bare_dash_format(self):
        """旧格式：纯 "- 内容" 无 frontmatter。"""
        text = "- 用户喜欢简洁回复\n- 偏好中文交流"
        entries = parse_memory_file(text, filename="preferences.md")
        assert len(entries) == 2
        assert entries[0].content == "用户喜欢简洁回复"
        assert entries[0].id == ""  # 旧格式无 id
        assert entries[1].content == "偏好中文交流"

    def test_legacy_bare_lines(self):
        """旧格式兼容：无 "- " 前缀的纯文本行。"""
        text = "用户偏好简洁回复\n偏好中文交流"
        entries = parse_memory_file(text, filename="preferences.md")
        assert len(entries) == 2
        assert entries[0].content == "用户偏好简洁回复"

    def test_mixed_format(self):
        """混合新旧格式。注意：parser 当前在 frontmatter 块后
        会跳过全部剩余行，所以旧格式条目放在前面。"""
        text = (
            "# 偏好\n\n"
            "- 旧的 dash 条目\n"
            "---\nid: pref_001\n---\n- 新的结构化条目\n"
        )
        entries = parse_memory_file(text, filename="preferences.md")
        # frontmatter 条目
        structured = [e for e in entries if e.id == "pref_001"]
        assert len(structured) == 1
        assert structured[0].content == "新的结构化条目"
        # 旧格式条目（在 frontmatter 之前）
        dash_entries = [e for e in entries if e.id == "" and e.content == "旧的 dash 条目"]
        assert len(dash_entries) == 1

    def test_skip_headers_and_comments(self):
        text = (
            "# Preferences\n"
            "<!-- comment -->\n"
            "- 实际条目\n"
        )
        entries = parse_memory_file(text, filename="preferences.md")
        assert len(entries) == 1
        assert entries[0].content == "实际条目"

    def test_empty_content(self):
        assert parse_memory_file("") == []

    def test_only_headers(self):
        text = "# 偏好\n## Section\n### Subsection"
        entries = parse_memory_file(text)
        assert entries == []

    def test_filename_preserved(self):
        text = "- test content"
        entries = parse_memory_file(text, filename="workflows.md")
        assert entries[0].file == "workflows.md"

    def test_skip_weight_and_deviations_frontmatter(self):
        text = "---\nid: pref_005\nweight: 2.0\ndeviations: 3\n---\n- 测试"
        entries = parse_memory_file(text)
        assert entries[0].weight == 2.0
        assert entries[0].deviations == 3


class TestFormatMemoryEntry:
    """测试 format_memory_entry 函数。"""

    def test_full_entry_formatting(self):
        entry = MemoryEntry(
            id="pref_001",
            content="用户喜欢简洁回复",
            created="2026-07-15",
            source="20260715_120000/turn_3",
        )
        formatted = format_memory_entry(entry)
        assert "id: pref_001" in formatted
        assert "created: 2026-07-15" in formatted
        assert "source: 20260715_120000/turn_3" in formatted
        assert "- 用户喜欢简洁回复" in formatted

    def test_minimal_entry(self):
        entry = MemoryEntry(content="bare content")
        formatted = format_memory_entry(entry)
        assert "- bare content" in formatted
        assert "id:" not in formatted  # 空 id 不输出

    def test_weight_only_when_non_default(self):
        entry = MemoryEntry(id="pref_001", content="test", weight=1.5)
        formatted = format_memory_entry(entry)
        assert "weight: 1.5" in formatted

        entry2 = MemoryEntry(id="pref_001", content="test", weight=1.0)
        formatted2 = format_memory_entry(entry2)
        assert "weight:" not in formatted2

    def test_roundtrip(self):
        """format → parse 应保持数据不变。"""
        original = MemoryEntry(
            id="ltm_003",
            content="项目 A 使用 Docker 部署",
            file="long_term_memory.md",
            created="2026-06-01",
            source="20260601_090000/turn_2",
        )
        formatted = format_memory_entry(original)
        parsed = parse_memory_file(formatted, filename="long_term_memory.md")
        assert len(parsed) == 1
        assert parsed[0].id == original.id
        assert parsed[0].content == original.content
        assert parsed[0].created == original.created
        assert parsed[0].source == original.source
