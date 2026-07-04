"""/plugin 指令 — 插件管理。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.locale import t

logger = logging.getLogger(__name__)


async def handle_plugin(app, args: str) -> str:
    """插件管理入口。

    无参数: 自动加载所有发现的插件 + 列出状态
    子命令:
      load <id>    — 加载插件
      unload <id>  — 卸载插件
      reload <id>  — 重载插件
    """
    parts = args.strip().split(maxsplit=1)
    sub = parts[0] if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    kernel = app._kernel

    # ── 无参数：自动加载所有发现插件 + 列出 ──
    if not sub:
        manifests = kernel._plugins.discover()
        if not manifests:
            return t("cmd.plugin.no_plugins")

        lines = [t("cmd.plugin.title") + "\n"]
        loaded_count = 0
        new_count = 0
        failed: list[str] = []

        for m in manifests:
            if kernel._plugins.is_loaded(m.id):
                loaded_count += 1
                lines.append(f"- ✅ **{m.name or m.id}** v{m.version}")
            else:
                info = await kernel.load_plugin(m.id)
                if info:
                    new_count += 1
                    lines.append(f"- 🆙 **{info.name}** v{info.manifest.version}（{t('cmd.plugin.loaded')}）")
                else:
                    failed.append(m.id)
                    lines.append(f"- ❌ **{m.id}** v{m.version} — {t('cmd.plugin.load_failed')}")

            if m.description:
                lines.append(f"  {m.description}")

        lines.append("")
        summary_parts = []
        if loaded_count:
            summary_parts.append(t("cmd.plugin.count_loaded", n=loaded_count))
        if new_count:
            summary_parts.append(t("cmd.plugin.count_new", n=new_count))
        if failed:
            summary_parts.append(t("cmd.plugin.count_failed", n=len(failed)))
        lines.append("、".join(summary_parts))

        if failed:
            lines.append(f"\n{t('cmd.plugin.failed_list', names=', '.join(failed))}")
        lines.append("\n" + t("cmd.plugin.hint"))
        return "\n".join(lines)

    # ── 显式子命令 ──
    if sub == "load":
        if not rest:
            return t("cmd.plugin.usage_load")
        info = await kernel.load_plugin(rest)
        if info:
            return t("cmd.plugin.load_ok", name=info.name, version=info.manifest.version)
        return t("cmd.plugin.load_error", id=rest)

    elif sub == "unload":
        if not rest:
            return t("cmd.plugin.usage_unload")
        if await kernel.unload_plugin(rest):
            return t("cmd.plugin.unload_ok", id=rest)
        return t("cmd.plugin.unload_error", id=rest)

    elif sub == "reload":
        if not rest:
            return t("cmd.plugin.usage_reload")
        info = await kernel._plugins.reload(rest)
        if info:
            return t("cmd.plugin.reload_ok", name=info.name, version=info.manifest.version)
        return t("cmd.plugin.reload_error", id=rest)

    else:
        return t("cmd.plugin.unknown_sub", sub=sub)
