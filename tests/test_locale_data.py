"""测试 locale_data — 双语 JSON 加载 + 损坏/缺失文件容错。"""

import importlib
from pathlib import Path

import core.locale_data


class TestLocaleDataLoad:
    """正常加载：四个 JSON 文件合并进 _STRINGS。"""

    def test_all_json_files_loaded(self):
        """prompts / commands / ui / runtime 命名空间全部存在。"""
        strings = core.locale_data._STRINGS
        assert strings
        # 各文件的代表 key
        assert "soul.title" in strings                      # prompts.json
        assert "cmd.help.title" in strings                  # commands.json
        assert "ui.onboard.lang_title" in strings           # ui.json
        assert "tool_desc.read_file" in strings             # runtime.json

    def test_values_are_bilingual(self):
        """每个条目都是 {zh, en} 双语文案。"""
        strings = core.locale_data._STRINGS
        for key in ("soul.title", "ui.onboard.lang_title", "cmd.help.title"):
            entry = strings[key]
            assert "zh" in entry and "en" in entry
            assert isinstance(entry["zh"], str) and entry["zh"].strip()
            assert isinstance(entry["en"], str) and entry["en"].strip()

    def test_zh_and_en_differ(self):
        """同一 key 的中英文案应当不同。"""
        strings = core.locale_data._STRINGS
        entry = strings["soul.p1"]
        assert entry["zh"] != entry["en"]


class TestLocaleDataTolerance:
    """损坏/缺失 JSON 文件时的容错（对应 __init__ 的 except 分支）。"""

    def test_corrupt_json_is_skipped_gracefully(self):
        """某个 JSON 文件损坏 → 跳过该文件，其余正常加载。"""
        module_dir = Path(core.locale_data.__file__).parent
        target = module_dir / "ui.json"
        original = target.read_bytes()
        try:
            target.write_text("{not valid json", encoding="utf-8")
            importlib.reload(core.locale_data)
            strings = core.locale_data._STRINGS
            # ui 命名空间缺失，但 prompts / commands / runtime 仍在
            assert not any(k.startswith("ui.") for k in strings)
            assert "soul.title" in strings
            assert "cmd.help.title" in strings
        finally:
            target.write_bytes(original)
            importlib.reload(core.locale_data)

    def test_missing_file_raises_oserror_is_skipped(self):
        """读取 JSON 文件抛 OSError → 跳过，不中断加载。"""
        module_dir = Path(core.locale_data.__file__).parent
        target = module_dir / "commands.json"
        original = target.read_bytes()
        # 用同名目录代替文件 → read_text 抛 IsADirectoryError/PermissionError（OSError 子类）
        target.unlink()
        try:
            target.mkdir()
            importlib.reload(core.locale_data)
            strings = core.locale_data._STRINGS
            # commands.json 未加载（cmd.help.title 只存在于 commands.json）
            assert "cmd.help.title" not in strings
            assert "soul.title" in strings
            assert "ui.onboard.lang_title" in strings
        finally:
            target.rmdir()
            target.write_bytes(original)
            importlib.reload(core.locale_data)
