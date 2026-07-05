"""Aide 双语支持 — 集中字符串表 + t() 访问函数。

用法:
    from core.locale import t, set_locale, build_soul, build_tools_prompt
    set_locale("en")
    print(t("cmd.help.title"))

零外部依赖。所有 UI 文本通过 t(key, **kwargs) 获取。
"""

from __future__ import annotations

# ── 全局语言状态 ─────────────────────────────────────────────────────────

_current_locale: str = "zh"


def set_locale(locale: str) -> None:
    """切换当前语言。"""
    global _current_locale
    if locale in ("zh", "en"):
        _current_locale = locale


def t(key: str, **kwargs) -> str:
    """获取 key 在当前语言下的文本，支持 {name} 等格式化。

    Args:
        key: 字符串键，如 "soul.line1"
        **kwargs: 格式化参数，如 name="Aide"

    Returns:
        当前语言文本。key 不存在时返回 key 本身（方便开发时发现遗漏）。
    """
    entry = _STRINGS.get(key)
    if entry is None:
        return f"[[{key}]]"
    text = entry.get(_current_locale, entry.get("zh", f"[[{key}]]"))
    if kwargs:
        return text.format(**kwargs)
    return text


# ── Soul / Tools 构建函数 ─────────────────────────────────────────────────


def build_soul(name: str) -> str:
    """构建 Soul 模板（当前语言）。"""
    return f"""{t("soul.title")}

{t("soul.line1", name=name)}

{t("soul.principles")}

{t("soul.p1")}
{t("soul.p2")}
{t("soul.p3")}
{t("soul.p4")}
{t("soul.p5")}
"""


def build_tools_prompt() -> str:
    """构建 Tools Prompt（当前语言）。"""
    return f"""{t("tools.heading")}

{t("tools.intro")}

{t("tools.list_title")}

{t("tools.read_file")}

{t("tools.write_file")}

{t("tools.edit_file")}

{t("tools.run_shell")}

{t("tools.search_in_files")}

{t("tools.list_dir")}

{t("tools.search_memory")}

{t("tools.web_search")}

{t("tools.web_fetch")}

{t("tools.clipboard")}

{t("tools.strategy_title")}

{t("tools.strategy_1")}
{t("tools.strategy_2")}
{t("tools.strategy_3")}
{t("tools.strategy_4")}
{t("tools.strategy_5")}

{t("tools.error_title")}

{t("tools.error_body")}
"""



# ── 字符串数据 ──────────────────────────────────────────────────────────
# 纯数据层已拆至 locale_data.py（约 1800 行），通过延迟导入引入。

from .locale_data import _STRINGS  # noqa: E402
