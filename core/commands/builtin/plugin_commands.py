"""/plugins 指令 — 插件管理统一入口（合并原 /plugin + /plugins）。"""

from __future__ import annotations

import logging

from core.commands.context import CommandContext
from core.locale import t
from core.setup import aide_dir

logger = logging.getLogger(__name__)


async def _plugin_subcommand(kernel, sub: str, rest: str) -> str:
    """执行插件子命令（load/unload/reload/enable/disable）。"""
    if sub == "load":
        if not rest:
            return t("cmd.plugin.usage_load")
        info = await kernel.load_plugin(rest)
        if info:
            return t("cmd.plugin.load_ok", name=info.name, version=info.manifest.version)
        return t("cmd.plugin.load_error", id=rest)

    if sub == "unload":
        if not rest:
            return t("cmd.plugin.usage_unload")
        if await kernel.unload_plugin(rest):
            return t("cmd.plugin.unload_ok", id=rest)
        return t("cmd.plugin.unload_error", id=rest)

    if sub == "reload":
        if not rest:
            return t("cmd.plugin.usage_reload")
        info = await kernel._plugins.reload(rest)
        if info:
            return t("cmd.plugin.reload_ok", name=info.name, version=info.manifest.version)
        return t("cmd.plugin.reload_error", id=rest)

    if sub == "enable":
        if not rest:
            return "/plugins enable <id> — 启用已禁用的插件"
        await kernel._plugins.enable_plugin(rest)
        return f"✅ {rest} 已启用"

    if sub == "disable":
        if not rest:
            return "/plugins disable <id> — 禁用插件（卸载其工具/命令）"
        await kernel._plugins.disable_plugin(rest)
        return f"🚫 {rest} 已禁用"

    return t("cmd.plugin.unknown_sub", sub=sub)


async def handle_plugins(app: CommandContext, args: str) -> str:
    """插件管理统一入口。

    无参数: 自动加载所有发现的插件 + 列出状态面板
    子命令:
      /plugins load <id>      — 加载插件
      /plugins unload <id>    — 卸载插件
      /plugins reload <id>    — 重载插件
      /plugins enable|disable <id> — 启用 / 禁用
    """
    parts = args.strip().split(maxsplit=1)
    sub = parts[0] if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    kernel = app.kernel
    state_mgr = kernel._plugins.state_manager

    # ── 显式子命令 ──
    if sub in ("load", "unload", "reload", "enable", "disable"):
        return await _plugin_subcommand(kernel, sub, rest)

    plugins_dir = aide_dir() / "plugins"

    # ── 无参数：加载所有已发现插件（未加载的） ──
    manifests = kernel._plugins.discover()
    if not manifests:
        return f"📦 无已安装插件。\n\n将插件放入 `{plugins_dir}` 目录后自动发现。"

    loaded_ids = {info.id for info in kernel._plugins.list_loaded()}
    newly: list[str] = []
    failed: list[str] = []
    for m in manifests:
        if m.id not in loaded_ids:
            try:
                info = await kernel.load_plugin(m.id)
                if info:
                    newly.append(m.id)
                else:
                    failed.append(m.id)
            except Exception:
                failed.append(m.id)

    # ── 构建状态面板 ──
    entries = state_mgr.list_all()
    counts = state_mgr.count_by_status()

    lines = ["## 📦 插件状态", f"插件目录：`{plugins_dir}`", ""]
    if newly:
        lines.append(f"🆙 新加载: **{', '.join(newly)}**")
    if failed:
        lines.append(f"❌ 加载失败: **{', '.join(failed)}**")
    if newly or failed:
        lines.append("")

    lines.append(
        f"| Ready: **{counts['ready']}** | Needs Setup: **{counts['needs_setup']}** | "
        f"Disabled: **{counts['disabled']}** |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 按状态排序
    order = {"ready": 0, "needs_setup": 1, "disabled": 2}
    entries.sort(key=lambda e: (order.get(e.status.value, 9), e.plugin_id))

    status_icons = {"ready": "✅", "needs_setup": "⚠️", "disabled": "🚫"}

    for e in entries:
        icon = status_icons.get(e.status.value, "❓")
        version_str = f" v{e.version}" if e.version and e.version != "0.0.0" else ""
        lines.append(f"### {icon} {e.plugin_id}{version_str}")

        if e.status.value == "needs_setup" and e.missing_requirements:
            for req in e.missing_requirements:
                lines.append(f"  - 缺少: `{req}`")
        elif e.status.value == "disabled":
            lines.append(f"  - 状态: 已禁用（`/plugins enable {e.plugin_id}` 启用）")

        lines.append("")

    # 用法提示
    lines.append("---")
    lines.append("`/plugins` 刷新 | `/plugins load|unload|reload <id>` 管理加载 | `/plugins enable|disable <id>` 开关")

    return "\n".join(lines)
