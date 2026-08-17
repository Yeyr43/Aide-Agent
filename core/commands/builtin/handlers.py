"""命令处理器 — 内置斜杠命令的实现。

每个 handler 签名: async (app, args: str) -> str
返回要显示在 MessageList 中的消息文本。

P4 Batch 2: 向后兼容层拆至 _compat.py；handler 保持纯净。
"""

from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path
from datetime import datetime, timezone
from core.locale import t
from core.platform import user_download_dir
from core.setup import aide_dir
from core.commands.context import CommandContext
from core.sessions.restorer import _msg_to_entry

AIDE_ROOT = aide_dir()
AGENT_ROOT = AIDE_ROOT / "agent"

logger = logging.getLogger(__name__)


# ── 命令实现 ─────────────────────────────────────────────────────────


async def handle_help(app: CommandContext, args: str) -> str:
    lines = [t("cmd.help.title")]

    # 从 CommandRegistry 读取（包含插件命令）
    cmd_registry = getattr(app, '_cmd_registry', None)
    if cmd_registry is not None:
        for cmd_def in cmd_registry.list_all():
            lines.append(f"- **{cmd_def.name}** — {cmd_def.description}")

    lines.append("")
    lines.append(t("cmd.help.hint"))
    return "\n".join(lines)


async def handle_profile(app: CommandContext, args: str) -> str:
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

    from core.memory.version import rollback_prompt
    success, message = rollback_prompt(prompt_type, n)
    if success:
        return t("cmd.profile.rollback_done", message=message)
    return t("cmd.profile.rollback_failed", reason=message)


async def handle_export(app: CommandContext, args: str) -> str:
    """打包 ~/.aide/ 关键文件为 zip。"""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    export_dir = user_download_dir()
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


async def handle_import(app: CommandContext, args: str) -> str:
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


async def handle_session(app: CommandContext, args: str) -> str:
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


async def handle_mem_auto(app: CommandContext, args: str) -> str:
    """自动记忆提取开关 — /mem-auto on|off|status。

    写入 settings.json 的 app.auto_memory（默认关）。开关即时生效：
    AutoMemoryExtractor 每次提取时实时读 settings。
    """
    from core.config import Config
    sub = args.strip().lower()
    settings = Config.load_settings()
    app_cfg = settings.setdefault("app", {})

    if sub == "on":
        app_cfg["auto_memory"] = True
        Config.save_settings(settings)
        return t("cmd.mem_auto.on")
    if sub == "off":
        app_cfg["auto_memory"] = False
        Config.save_settings(settings)
        return t("cmd.mem_auto.off")
    if sub in ("", "status"):
        if app_cfg.get("auto_memory", False):
            return t("cmd.mem_auto.status_on")
        return t("cmd.mem_auto.status_off")
    return t("cmd.mem_auto.invalid")


async def handle_memory(app: CommandContext, args: str) -> str:
    """查看记忆条目（P5 .md 格式）。"""
    lines = [t("cmd.memory.title")]

    total_entries = 0

    for fname, label_key in [
        ("preferences.md", "mem.label_preferences"),
        ("workflows.md", "mem.label_workflows"),
        ("long_term_memory.md", "mem.label_long_term_memory"),
    ]:
        label = t(label_key)
        path = AGENT_ROOT / fname
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8")
                count = sum(1 for l in content.split("\n") if l.strip().startswith("- "))
                total_entries += count
                lines.append(f"- **{label}**: {t('cmd.memory.confirmed', confirmed=count)}")
            except OSError:
                lines.append(f"- **{label}**: {t('cmd.memory.read_error')}")
        else:
            lines.append(f"- **{label}**: {t('cmd.memory.no_data')}")

    lines.append("")
    if total_entries > 0:
        lines.append(t("cmd.memory.confirmed_summary", total=total_entries))
    else:
        lines.append("")
        lines.append(t("cmd.memory.empty_hint"))

    lines.append("")
    lines.append(t("cmd.memory.hint"))

    return "\n".join(lines)


async def handle_tools(app: CommandContext, args: str) -> str:
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



async def handle_rollback(app: CommandContext, args: str) -> str:
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


def _rebuild_conversation_from_disk(app: CommandContext, session_dir: Path, target_turn: int) -> None:
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
            msgs = data.get("messages") or data.get("conversation") or []
            if msgs:
                # 保留 tool_calls / tool_call_id —— tool 消息必须有前置 assistant 配对
                for msg in msgs:
                    entry = _msg_to_entry(msg)
                    if entry is not None:
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

# handle_language / handle_api / handle_model 已拆分至 settings_handlers.py
from .settings_handlers import (  # noqa: E402, F401
    handle_language, handle_api, handle_model,
)


# ── CommandRegistry 集成入口 ────────────────────────────────────────


def register_builtin_commands(registry) -> None:
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
        name="/reflect", description=t("cmd.reflect.desc"),
        kind="maintenance",
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
        name="/plugins", description="插件管理 — 加载 + 列出状态（原 /plugin 与 /plugins 合并）",
        handler=_handle_plugins,
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
        name="/mem-auto", description=t("cmd.mem_auto.desc"),
        handler=handle_mem_auto,
    ))
    registry.register(CommandDefinition(
        name="/tools", description=t("cmd.tools.desc"),
        handler=handle_tools,
    ))
    # P5: 新增命令
    registry.register(CommandDefinition(
        name="/think", description=t("cmd.think.desc"),
        kind="think",
    ))
    registry.register(CommandDefinition(
        name="/clear", description=t("cmd.clear.desc"),
        kind="confirm",
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


from core.commands.builtin.plugin_commands import handle_plugins as _handle_plugins  # noqa: E402
