"""跨平台工具模块测试。"""
import sys
from pathlib import Path

import pytest

from ui.textual_app.platform import (
    IS_WINDOWS,
    IS_MACOS,
    IS_LINUX,
    CURRENT,
    platform_name,
    hide_console,
    user_download_dir,
)


def test_platform_flags_mutually_exclusive():
    """恰好一个平台标志为 True。"""
    flags = [IS_WINDOWS, IS_MACOS, IS_LINUX]
    assert sum(flags) == 1, f"平台标志异常: W={IS_WINDOWS} M={IS_MACOS} L={IS_LINUX}"


def test_current_matches_sys_platform():
    """CURRENT 等于 sys.platform。"""
    assert CURRENT == sys.platform


def test_platform_name_returns_non_empty_string():
    """platform_name() 返回非空字符串。"""
    name = platform_name()
    assert isinstance(name, str)
    assert len(name) > 0
    assert name in ("Windows", "macOS", "Linux", sys.platform)


def test_hide_console_returns_bool():
    """hide_console() 始终返回 bool，不抛异常。"""
    result = hide_console()
    assert isinstance(result, bool)


def test_user_download_dir_returns_path():
    """user_download_dir() 返回存在的目录。"""
    d = user_download_dir()
    assert isinstance(d, Path)
    assert d.is_dir(), f"{d} 不是目录"


def test_platform_constants_are_boolean():
    """平台常量是严格的 bool 类型。"""
    assert isinstance(IS_WINDOWS, bool)
    assert isinstance(IS_MACOS, bool)
    assert isinstance(IS_LINUX, bool)


def test_current_is_valid_platform():
    """CURRENT is a known platform identifier."""
    valid = CURRENT == "win32" or CURRENT == "darwin" or CURRENT.startswith("linux")
    assert valid, f"Unknown platform: {CURRENT}"


def test_user_download_dir_fallback_to_home():
    """user_download_dir() 至少回退到 ~。"""
    d = user_download_dir()
    assert str(d).startswith(str(Path.home())), f"{d} 不在 home 下"


def test_hide_console_safe_on_current_platform():
    """hide_console() 在当前平台不抛异常。"""
    # 可能会失败（如无 xdotool），但不应抛异常
    result = hide_console()
    assert result in (True, False)


def test_can_use_tray_returns_bool():
    """can_use_tray() returns bool without raising."""
    from ui.textual_app.platform import can_use_tray
    result = can_use_tray()
    assert result in (True, False)
