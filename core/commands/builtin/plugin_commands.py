"""/plugin /plugins 指令 — 插件管理 + 状态面板。"""

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

    elif sub == "enable":
        if not rest:
            return "/plugin enable <id> — 启用已禁用的插件"
        kernel._plugins.enable_plugin(rest)
        return f"✅ {rest} 已启用"

    elif sub == "disable":
        if not rest:
            return "/plugin disable <id> — 禁用插件（不卸载）"
        kernel._plugins.disable_plugin(rest)
        return f"🚫 {rest} 已禁用"

    else:
        return t("cmd.plugin.unknown_sub", sub=sub)


# ── P7: /plugins 状态面板 ────────────────────────────────────────────────


async def handle_plugins_status(app, args: str) -> str:
    """显示所有插件状态面板（三态：Ready / Needs Setup / Disabled）。

    用法: /plugins [enable|disable <id>]
    """
    parts = args.strip().split(maxsplit=1)
    sub = parts[0] if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    kernel = app._kernel
    state_mgr = kernel._plugins.state_manager

    if sub == "enable" and rest:
        kernel._plugins.enable_plugin(rest)
        return f"✅ {rest} 已启用"
    elif sub == "disable" and rest:
        kernel._plugins.disable_plugin(rest)
        return f"🚫 {rest} 已禁用"

    # ── 加载所有已发现插件（未加载的） ──
    manifests = kernel._plugins.discover()
    loaded_ids = {info.id for info in kernel._plugins.list_loaded()}
    for m in manifests:
        if m.id not in loaded_ids:
            try:
                await kernel.load_plugin(m.id)
            except Exception:
                pass

    # ── 构建状态面板 ──
    entries = state_mgr.list_all()
    counts = state_mgr.count_by_status()

    if not entries:
        return "📦 无已安装插件。\n\n将插件放入 `~/.aide/plugins/` 目录后自动发现。"

    lines = [
        "## 📦 插件状态",
        "",
        f"| Ready: **{counts['ready']}** | Needs Setup: **{counts['needs_setup']}** | Disabled: **{counts['disabled']}** |",
        "",
        "---",
        "",
    ]

    # 按状态排序
    order = {"ready": 0, "needs_setup": 1, "disabled": 2}
    entries.sort(key=lambda e: (order.get(e.status.value, 9), e.plugin_id))

    status_icons = {
        "ready": "✅",
        "needs_setup": "⚠️",
        "disabled": "🚫",
    }

    for e in entries:
        icon = status_icons.get(e.status.value, "❓")
        version_str = f" v{e.version}" if e.version and e.version != "0.0.0" else ""
        lines.append(f"### {icon} {e.plugin_id}{version_str}")

        if e.status.value == "needs_setup" and e.missing_requirements:
            for req in e.missing_requirements:
                lines.append(f"  - 缺少: `{req}`")
        elif e.status.value == "disabled":
            lines.append(f"  - 状态: 已禁用（`/plugins enable {e.plugin_id}` 启用）")

        if e.usage_count > 0:
            lines.append(f"  - 使用次数: {e.usage_count}")
        lines.append("")

    # 用法提示
    lines.append("---")
    lines.append("`/plugins` — 刷新状态 | `/plugin load <id>` — 加载插件 | `/plugin unload <id>` — 卸载插件")

    return "\n".join(lines)
