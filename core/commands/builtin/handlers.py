"""命令处理器 — 内置斜杠命令的实现。

每个 handler 签名: async (app, args: str) -> str
返回要显示在 MessageList 中的消息文本。

P4 Batch 2: 向后兼容层拆至 _compat.py；handler 保持纯净。
"""

from __future__ import annotations

import json
import logging
import os
import zipfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from core.locale import t

from ._compat import (
    AIDE_ROOT, AGENT_ROOT, COMMANDS, _register_to_commands, _cmd,
)

logger = logging.getLogger(__name__)


def _export_dir() -> Path:
    """返回平台默认下载/桌面目录（用于 /export）。

    优先级：XDG_DOWNLOAD_DIR > ~/Downloads > ~/Desktop > ~
    """
    xdg = os.environ.get("XDG_DOWNLOAD_DIR", "")
    if xdg:
        return Path(xdg)

    for candidate in ["Downloads", "Desktop"]:
        p = Path.home() / candidate
        if p.is_dir():
            return p

    return Path.home()


# ── 命令实现 ─────────────────────────────────────────────────────────


@_cmd("help", t("cmd.help.desc"))
async def handle_help(app: Any, args: str) -> str:
    lines = [t("cmd.help.title")]

    # 优先从 CommandRegistry 读取（包含插件命令）
    cmd_registry = getattr(app, '_cmd_registry', None)
    if cmd_registry is not None:
        for cmd_def in cmd_registry.list_all():
            lines.append(f"- **{cmd_def.name}** — {cmd_def.description}")
    else:
        # 回退到模块级 COMMANDS（无 App 环境时）
        for cmd, (_, desc) in COMMANDS.items():
            lines.append(f"- **{cmd}** — {desc}")

    lines.append("")
    lines.append(t("cmd.help.hint"))
    return "\n".join(lines)


@_cmd("profile", t("cmd.profile.desc"))
async def handle_profile(app: Any, args: str) -> str:
    # P5: 子命令分发
    parts = args.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else ""

    if sub == "rollback":
        return await _handle_profile_rollback(parts[1] if len(parts) > 1 else "")

    lines = [t("cmd.profile.title")]

    # 显示 Soul
    soul_path = AGENT_ROOT / "soul.md"
    if soul_path.exists():
        lines.append("### Soul")
        lines.append(soul_path.read_text(encoding="utf-8"))
        lines.append("")
    else:
        lines.append(t("cmd.profile.soul_missing"))

    # 显示动态 prompt
    for fname, label_key in [
        ("preferences.md", "cmd.profile.label_preferences"),
        ("workflows.md", "cmd.profile.label_workflows"),
        ("long_term_memory.md", "cmd.profile.label_long_term_memory"),
    ]:
        label = t(label_key)
        path = AGENT_ROOT / fname
        if path.exists():
            content = path.read_text(encoding="utf-8")
            lines.append(f"### {label}")
            lines.append(content)
            lines.append("")

    # 显示 pending 条目数
    data_dir = AGENT_ROOT / "data"
    for fname, label_key in [
        ("preferences.json", "cmd.profile.label_preferences"),
        ("workflows.json", "cmd.profile.label_workflows"),
        ("long_term_memory.json", "cmd.profile.label_long_term_memory"),
    ]:
        label = t(label_key)
        path = data_dir / fname
        if path.exists():
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
                pending = sum(1 for e in entries if e.get("status") == "pending")
                if pending:
                    lines.append(t("cmd.profile.pending", label=label, pending=pending))
            except (json.JSONDecodeError, OSError):
                pass

    result = "\n".join(lines)
    if len(result) > 8000:
        result = result[:8000] + "\n\n" + t("cmd.profile.truncated")
    return result


async def _handle_profile_rollback(args: str) -> str:
    """处理 /profile rollback <type> [N] 子命令。"""
    if not args:
        return t("cmd.profile.rollback_usage")

    parts = args.strip().split()
    prompt_type = parts[0].lower()
    n = 0
    if len(parts) > 1:
        try:
            n = int(parts[1])
        except ValueError:
            return t("cmd.profile.rollback_bad_n", arg=parts[1])

    valid_types = {"preferences", "workflows", "long_term_memory"}
    if prompt_type not in valid_types:
        return t("cmd.profile.rollback_bad_type", type=prompt_type,
                 valid=", ".join(valid_types))

    from core.memory.updater import rollback_prompt
    success, message = rollback_prompt(prompt_type, n)
    if success:
        return t("cmd.profile.rollback_done", message=message)
    return t("cmd.profile.rollback_failed", reason=message)


@_cmd("compact", t("cmd.compact.desc"))
async def handle_compress(app: Any, args: str) -> str:
    return "__COMPRESS__"


@_cmd("export", t("cmd.export.desc"))
async def handle_export(app: Any, args: str) -> str:
    """打包 ~/.aide/ 关键文件为 zip。"""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    export_dir = _export_dir()
    export_path = export_dir / f"aide_export_{timestamp}.zip"

    with zipfile.ZipFile(export_path, "w", zipfile.ZIP_DEFLATED) as zf:
        agent_dir = AGENT_ROOT
        for f in agent_dir.glob("**/*"):
            if f.is_file() and ".tmp_" not in f.name:
                arcname = str(f.relative_to(AIDE_ROOT))
                zf.write(f, arcname)

        sessions_dir = AIDE_ROOT / "sessions"
        if sessions_dir.exists():
            for f in sessions_dir.glob("**/*"):
                if f.is_file() and ".tmp_" not in f.name:
                    arcname = str(f.relative_to(AIDE_ROOT))
                    zf.write(f, arcname)

    size_kb = export_path.stat().st_size / 1024
    return t("cmd.export.done", path=str(export_path), size=size_kb)


@_cmd("import", t("cmd.import.desc"))
async def handle_import(app: Any, args: str) -> str:
    """从 zip 包导入恢复数据。"""
    if not args:
        return t("cmd.import.need_path")

    import_path = Path(args.strip().strip('"'))
    if not import_path.exists():
        return t("cmd.import.not_found", path=str(import_path))

    if not import_path.suffix.lower() == ".zip":
        return t("cmd.import.not_zip")

    try:
        with zipfile.ZipFile(import_path, "r") as zf:
            for name in zf.namelist():
                full_path = (AIDE_ROOT / name).resolve()
                if not str(full_path).startswith(str(AIDE_ROOT.resolve())):
                    return t("cmd.import.unsafe", name=name)

            zf.extractall(AIDE_ROOT)

        return t("cmd.import.done", path=str(import_path), root=str(AIDE_ROOT))
    except zipfile.BadZipFile:
        return t("cmd.import.invalid_zip")
    except Exception as e:
        return t("cmd.import.failed", e=str(e))


# ── P4 Batch 2: 新增命令 ──────────────────────────────────────────────


@_cmd("session", t("cmd.session.desc"))
async def handle_session(app: Any, args: str) -> str:
    """会话管理命令。

    子命令:
      list       — 列出所有会话
      delete <id> — 删除指定会话
    """
    kernel = getattr(app, '_kernel', None)
    if kernel is None:
        return t("cmd.session.no_kernel")

    parts = args.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else "list"
    rest = parts[1] if len(parts) > 1 else ""

    if sub == "list":
        sessions = await kernel.list_sessions()
        if not sessions:
            return t("cmd.session.empty")

        lines = [t("cmd.session.list_title")]
        for s in sessions:
            lines.append(f"- **{s.id}** — {s.name}")
        lines.append(t("cmd.session.total", count=len(sessions)))
        lines.append(t("cmd.session.hint"))
        return "\n".join(lines)

    elif sub == "delete":
        if not rest:
            return t("cmd.session.usage_delete")
        success = await kernel.delete_session(rest)
        if success:
            return t("cmd.session.deleted", id=rest)
        return t("cmd.session.not_found", id=rest)

    else:
        return t("cmd.session.unknown_sub")


@_cmd("memory", t("cmd.memory.desc"))
async def handle_memory(app: Any, args: str) -> str:
    """查看记忆条目的捕获状态。"""
    data_dir = AGENT_ROOT / "data"
    lines = [t("cmd.memory.title")]

    total_pending = 0
    total_confirmed = 0

    for fname, label_key in [
        ("preferences.json", "mem.label_preferences"),
        ("workflows.json", "mem.label_workflows"),
        ("long_term_memory.json", "mem.label_long_term_memory"),
    ]:
        label = t(label_key)
        path = data_dir / fname
        if path.exists():
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
                pending = sum(1 for e in entries if e.get("status") == "pending")
                confirmed = sum(1 for e in entries if e.get("status") == "confirmed")
                total_pending += pending
                total_confirmed += confirmed
                parts = [f"- **{label}**: {t('cmd.memory.confirmed', confirmed=confirmed)}"]
                if pending:
                    parts.append(f" / {t('cmd.memory.pending_count', pending=pending)}")
                lines.append("".join(parts))
            except (json.JSONDecodeError, OSError):
                lines.append(f"- **{label}**: {t('cmd.memory.read_error')}")
        else:
            lines.append(f"- **{label}**: {t('cmd.memory.no_data')}")

    lines.append("")
    if total_pending > 0:
        lines.append(t("cmd.memory.pending_hint", total=total_pending))
    else:
        lines.append(t("cmd.memory.no_pending"))

    lines.append(t("cmd.memory.confirmed_summary", total=total_confirmed))
    lines.append("")
    lines.append(t("cmd.memory.hint"))

    return "\n".join(lines)


@_cmd("tools", t("cmd.tools.desc"))
async def handle_tools(app: Any, args: str) -> str:
    """列出所有已注册的工具。"""
    kernel = getattr(app, '_kernel', None)
    if kernel is None:
        return t("cmd.tools.no_kernel")

    tool_names = kernel.tool_registry.list_names()
    if not tool_names:
        return t("cmd.tools.empty")

    lines = [t("cmd.tools.title", count=len(tool_names))]

    builtin = [n for n in tool_names if not n.startswith("mcp_")]
    mcp_tools = [n for n in tool_names if n.startswith("mcp_")]

    if builtin:
        lines.append(t("cmd.tools.builtin"))
        for name in sorted(builtin):
            tool = kernel.tool_registry.get(name)
            if tool:
                lines.append(f"- **{name}** — {tool.description}")
            else:
                lines.append(f"- **{name}**")
        lines.append("")

    if mcp_tools:
        lines.append(t("cmd.tools.mcp"))
        for name in sorted(mcp_tools):
            tool = kernel.tool_registry.get(name)
            if tool:
                lines.append(f"- **{name}** — {tool.description}")
        lines.append("")

    return "\n".join(lines)


@_cmd("update", t("cmd.update.desc"))
async def handle_update(app: Any, args: str) -> str:
    """触发 prompt 更新（整合 pending 条目）。实际逻辑在 app 层处理。"""
    return "__PROFILE_UPDATE__"


@_cmd("clear", t("cmd.clear.desc"))
async def handle_clear(app: Any, args: str) -> str:
    """触发会话删除确认流程。"""
    return "__CLEAR_CONFIRM__"


@_cmd("rollback", t("cmd.rollback.desc"))
async def handle_rollback(app: Any, args: str) -> str:
    """回滚会话到指定轮次（两步确认）。

    第一步：验证参数 + 设置 pending 状态 → 返回确认提示。
    第二步：用户输入 yes/确认 → app.py 执行实际回滚。
    """
    kernel = getattr(app, '_kernel', None)
    if kernel is None:
        return t("cmd.rollback.no_kernel")

    try:
        target_turn = int(args.strip())
    except ValueError:
        return t("cmd.rollback.usage")

    ingester = getattr(app, '_ingester', None)
    if ingester is None or ingester._session_dir is None:
        return t("cmd.rollback.no_session")

    session = getattr(app, '_session', None)
    if session is None:
        return t("cmd.rollback.no_turn")

    current_turn = session.turn

    if target_turn < 1:
        return t("cmd.rollback.must_be_positive", current=current_turn)
    if target_turn >= current_turn:
        return t("cmd.rollback.future", current=current_turn, target=target_turn)

    # 验证通过 → 设置 pending 状态，等待用户确认
    session.pending_rollback = True
    session.pending_rollback_turn = target_turn

    deleted = current_turn - target_turn
    return t("cmd.rollback.confirm",
             target=target_turn,
             **{"from": target_turn + 1, "to": current_turn, "deleted": deleted})


def _rebuild_conversation_from_disk(app: Any, session_dir: Path, target_turn: int) -> None:
    """从 turn 文件重建 session.conversation（回滚后调用）。"""
    session = app._session
    session.turn = target_turn
    session.conversation = []

    messages_dir = session_dir / "messages"
    if not messages_dir.exists():
        return

    for tf in sorted(messages_dir.glob("turn_*.json")):
        # 只加载 <= target_turn 的轮次
        try:
            turn_num = int(tf.stem.split("_", 1)[1])
            if turn_num > target_turn:
                continue
        except (ValueError, IndexError):
            continue

        try:
            data = json.loads(tf.read_text(encoding="utf-8"))
            conv = data.get("conversation", [])
            if conv:
                for msg in conv:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if role in ("user", "assistant") and content:
                        entry = {"role": role, "content": content}
                        # 保留 _image_paths 以支持文件附件显示
                        if "_image_paths" in msg:
                            entry["_image_paths"] = msg["_image_paths"]
                        session.conversation.append(entry)
            else:
                # 回退：旧格式（仅 user/assistant 字符串）
                user = data.get("user", "")
                if user:
                    session.conversation.append({"role": "user", "content": user})
                assistant = data.get("assistant", "")
                if assistant:
                    session.conversation.append({"role": "assistant", "content": assistant})
        except (json.JSONDecodeError, OSError):
            pass


# handle_mcp 已拆分至 mcp_handlers.py
from .mcp_handlers import handle_mcp  # noqa: E402, F401

# handle_language / handle_api / handle_model / _save_settings 已拆分至 settings_handlers.py
from .settings_handlers import (  # noqa: E402, F401
    handle_language, handle_api, handle_model, _save_settings_dict as _save_settings,
)


# ── CommandRegistry 集成入口 ────────────────────────────────────────


def register_builtin_commands(registry: Any) -> None:
    """注册所有内置命令到 CommandRegistry。

    P4 Batch 2: 12 个命令。
    """
    from core.commands import CommandDefinition

    registry.register(CommandDefinition(
        name="/help", description=t("cmd.help.desc"),
        handler=handle_help,
    ))
    registry.register(CommandDefinition(
        name="/profile", description=t("cmd.profile.desc"),
        handler=handle_profile,
    ))
    registry.register(CommandDefinition(
        name="/compact", description=t("cmd.compact.desc"),
        handler=handle_compress, kind="maintenance",
    ))
    registry.register(CommandDefinition(
        name="/export", description=t("cmd.export.desc"),
        handler=handle_export,
    ))
    registry.register(CommandDefinition(
        name="/import", description=t("cmd.import.desc"),
        handler=handle_import,
    ))
    registry.register(CommandDefinition(
        name="/plugin", description=t("cmd.plugin.desc"),
        handler=_handle_plugin,
    ))
    # P4 Batch 2: 新增命令
    registry.register(CommandDefinition(
        name="/session", description=t("cmd.session.desc"),
        handler=handle_session,
    ))
    registry.register(CommandDefinition(
        name="/memory", description=t("cmd.memory.desc"),
        handler=handle_memory,
    ))
    registry.register(CommandDefinition(
        name="/tools", description=t("cmd.tools.desc"),
        handler=handle_tools,
    ))
    registry.register(CommandDefinition(
        name="/update", description=t("cmd.update.desc"),
        handler=handle_update,
    ))
    registry.register(CommandDefinition(
        name="/clear", description=t("cmd.clear.desc"),
        handler=handle_clear, kind="confirm",
    ))
    registry.register(CommandDefinition(
        name="/rollback", description=t("cmd.rollback.desc"),
        handler=handle_rollback,
    ))
    registry.register(CommandDefinition(
        name="/mcp", description=t("cmd.mcp.desc"),
        handler=handle_mcp,
    ))
    # P5: 新增命令
    registry.register(CommandDefinition(
        name="/language", description=t("cmd.language.desc"),
        handler=handle_language,
    ))
    registry.register(CommandDefinition(
        name="/api", description=t("cmd.api.desc"),
        handler=handle_api,
    ))
    registry.register(CommandDefinition(
        name="/model", description=t("cmd.model.desc"),
        handler=handle_model,
    ))


# ── /plugin 模块级注册（handle_help 遍历 COMMANDS 字典）─────────

from core.commands.builtin.plugin_commands import handle_plugin as _handle_plugin  # noqa: E402
_register_to_commands("plugin", _handle_plugin, t("cmd.plugin.desc"))
