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


# ── 字符串表 ──────────────────────────────────────────────────────────────

_STRINGS: dict[str, dict[str, str]] = {

    # ═══════════════════════════════════════════════════════════════════════
    # Soul 模板
    # ═══════════════════════════════════════════════════════════════════════

    "soul.title": {
        "zh": "# Aide — Soul",
        "en": "# Aide — Soul",
    },
    "soul.line1": {
        "zh": "你是 {name}，运行在这台电脑上的本地助手。所有对话和记忆都留在本地，隐私不外泄。",
        "en": "You are {name}, a local assistant running on this computer. All conversations and memories stay local — your privacy never leaves this machine.",
    },
    "soul.principles": {
        "zh": "## 行事",
        "en": "## Principles",
    },
    "soul.p1": {
        "zh": "1. 用户的明确指令优先",
        "en": "1. The user's explicit instructions take priority",
    },
    "soul.p2": {
        "zh": "2. 简洁直接，别啰嗦",
        "en": "2. Be concise and direct — don't ramble",
    },
    "soul.p3": {
        "zh": "3. 不确定就说不知道，不编造",
        "en": "3. Say you don't know when uncertain — never fabricate",
    },
    "soul.p4": {
        "zh": "4. 涉及文件、Shell、联网时，先确认",
        "en": "4. Confirm before touching files, running shell commands, or going online",
    },
    "soul.p5": {
        "zh": "5. 被纠正就记住，不用用户重复",
        "en": "5. Remember corrections — don't make the user repeat them",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # Tools Prompt
    # ═══════════════════════════════════════════════════════════════════════

    "tools.heading": {
        "zh": "# 工具",
        "en": "# Tools",
    },
    "tools.intro": {
        "zh": "你可以调用以下工具完成任务。",
        "en": "You can call the following tools to complete tasks.",
    },
    "tools.list_title": {
        "zh": "## 工具列表",
        "en": "## Tool List",
    },
    "tools.read_file": {
        "zh": "**read_file** — 读取本地文件内容。文本文件直接返回，图片/PDF 暂不支持。限制 100KB。",
        "en": "**read_file** — Read local file contents. Returns text files directly; images/PDF not yet supported. Limit 100 KB.",
    },
    "tools.write_file": {
        "zh": "**write_file** — 创建或覆盖文件。需要用户明确同意才能使用。不用于局部修改。",
        "en": "**write_file** — Create or overwrite a file. Requires explicit user consent. Not for partial edits.",
    },
    "tools.edit_file": {
        "zh": "**edit_file** — 精确替换文件中的指定片段。优先于 write_file（避免覆盖整个文件）。需要用户明确同意。",
        "en": "**edit_file** — Precisely replace a specified snippet in a file. Prefer over write_file (avoids overwriting entire files). Requires explicit user consent.",
    },
    "tools.run_shell": {
        "zh": "**run_shell** — 执行 Shell 命令。Windows 下用 Git Bash（POSIX sh），不用 cmd.exe 或 PowerShell。需要用户明确同意。",
        "en": "**run_shell** — Execute a shell command. Use Git Bash (POSIX sh) on Windows, not cmd.exe or PowerShell. Requires explicit user consent.",
    },
    "tools.search_in_files": {
        "zh": "**search_in_files** — 在文件中用正则表达式搜索。支持 glob 过滤（如 *.py）。类似 ripgrep/grep。",
        "en": "**search_in_files** — Search files with regex. Supports glob filtering (e.g. *.py). Similar to ripgrep/grep.",
    },
    "tools.list_dir": {
        "zh": "**list_dir** — 列出目录内容。用于了解项目结构或查找文件。",
        "en": "**list_dir** — List directory contents. Use to explore project structure or locate files.",
    },
    "tools.search_memory": {
        "zh": "**search_memory** — 搜索 Aide 跨会话记忆。当用户问「之前聊过什么」或引用过去信息时使用。",
        "en": '**search_memory** — Search Aide cross-session memory. Use when the user asks "what did we discuss before" or references past information.',
    },
    "tools.web_search": {
        "zh": "**web_search** — 联网搜索。需要用户明确同意。优先用已有知识回答。",
        "en": "**web_search** — Web search. Requires explicit user consent. Prefer answering from existing knowledge.",
    },
    "tools.web_fetch": {
        "zh": "**web_fetch** — 获取网页内容并转为文本。需要用户明确同意。",
        "en": "**web_fetch** — Fetch web page content and convert to text. Requires explicit user consent.",
    },
    "tools.clipboard": {
        "zh": "**clipboard** — 读写系统剪贴板。action=\"read\" 读取，action=\"write\" 写入。不需要确认。",
        "en": '**clipboard** — Read/write system clipboard. action="read" to read, action="write" to write. No confirmation needed.',
    },
    "tools.strategy_title": {
        "zh": "## 使用策略",
        "en": "## Usage Strategy",
    },
    "tools.strategy_1": {
        "zh": "- 读文件优先用 read_file，避免用 run_shell 读文件（浪费资源）",
        "en": "- Prefer read_file over run_shell for reading files (saves resources)",
    },
    "tools.strategy_2": {
        "zh": "- 修改文件优先用 edit_file（精确替换），只用 write_file 新建文件",
        "en": "- Prefer edit_file for modifications (precise replacement); use write_file only for new files",
    },
    "tools.strategy_3": {
        "zh": "- 搜索文件内容用 search_in_files，列出目录用 list_dir",
        "en": "- Use search_in_files for content search, list_dir for directory listing",
    },
    "tools.strategy_4": {
        "zh": "- 不要为同一个参数重试超过一次",
        "en": "- Don't retry with the same parameters more than once",
    },
    "tools.strategy_5": {
        "zh": "- 工具失败时如实告知用户，不要隐藏或美化",
        "en": "- Report tool failures honestly to the user — don't hide or sugarcoat them",
    },
    "tools.error_title": {
        "zh": "## 工具失败",
        "en": "## Tool Failures",
    },
    "tools.error_body": {
        "zh": "工具返回以「错误：」开头的字符串时，告知用户具体原因，并建议替代方案。",
        "en": 'When a tool returns a string starting with "Error:", tell the user the specific reason and suggest alternatives.',
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 模板文件头
    # ═══════════════════════════════════════════════════════════════════════

    "tmpl.preferences": {
        "zh": "# 偏好\n\n<!-- 此文件由 Aide 自动维护，记录你的偏好和习惯 -->\n",
        "en": "# Preferences\n\n<!-- This file is maintained by Aide and records your preferences and habits -->\n",
    },
    "tmpl.workflows": {
        "zh": "# 工作流\n\n<!-- 此文件由 Aide 自动维护，记录你的工作流偏好 -->\n",
        "en": "# Workflows\n\n<!-- This file is maintained by Aide and records your workflow preferences -->\n",
    },
    "tmpl.long_term_memory": {
        "zh": "# 长记忆\n\n<!-- 此文件由 Aide 自动维护，记录跨会话的重要事实 -->\n",
        "en": "# Long-Term Memory\n\n<!-- This file is maintained by Aide and records important cross-session facts -->\n",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 命令: /help
    # ═══════════════════════════════════════════════════════════════════════

    "cmd.help.desc": {
        "zh": "显示所有可用命令",
        "en": "Show all available commands",
    },
    "cmd.help.title": {
        "zh": "## 可用命令",
        "en": "## Available Commands",
    },
    "cmd.help.hint": {
        "zh": "\n提示：直接输入文字即可与 Aide 对话。",
        "en": "\nTip: Just type to chat with Aide directly.",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 命令: /profile
    # ═══════════════════════════════════════════════════════════════════════

    "cmd.profile.desc": {
        "zh": "查看当前 Soul + 动态 prompt",
        "en": "View current Soul + dynamic prompts",
    },
    "cmd.profile.title": {
        "zh": "## 当前 Profile",
        "en": "## Current Profile",
    },
    "cmd.profile.soul_missing": {
        "zh": "*Soul 文件不存在*",
        "en": "*Soul file does not exist*",
    },
    "cmd.profile.label_preferences": {
        "zh": "偏好",
        "en": "Preferences",
    },
    "cmd.profile.label_workflows": {
        "zh": "工作流",
        "en": "Workflows",
    },
    "cmd.profile.label_long_term_memory": {
        "zh": "长记忆",
        "en": "Long-Term Memory",
    },
    "cmd.profile.pending": {
        "zh": "{label}: {pending} 条待整合",
        "en": "{label}: {pending} pending integration",
    },
    "cmd.profile.truncated": {
        "zh": "…（内容过长，已截断）",
        "en": "... (content truncated)",
    },
    # Rollback 子命令（P5）
    "cmd.profile.rollback_usage": {
        "zh": "用法: /profile rollback <type> [N]\n"
              "  type: preferences | workflows | long_term_memory\n"
              "  N: 备份编号（0=最新, 1=上一个...），默认 0",
        "en": "Usage: /profile rollback <type> [N]\n"
              "  type: preferences | workflows | long_term_memory\n"
              "  N: backup index (0=latest, 1=previous...), default 0",
    },
    "cmd.profile.rollback_done": {
        "zh": "✅ {message}",
        "en": "✅ {message}",
    },
    "cmd.profile.rollback_failed": {
        "zh": "❌ 回滚失败: {reason}",
        "en": "❌ Rollback failed: {reason}",
    },
    "cmd.profile.rollback_bad_type": {
        "zh": "无效的 prompt 类型 '{type}'。可用: {valid}",
        "en": "Invalid prompt type '{type}'. Valid: {valid}",
    },
    "cmd.profile.rollback_bad_n": {
        "zh": "无效的备份编号 '{arg}'，需要整数",
        "en": "Invalid backup index '{arg}', expected an integer",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 命令: /compact
    # ═══════════════════════════════════════════════════════════════════════

    "cmd.compact.desc": {
        "zh": "压缩当前会话上下文，生成会话总览",
        "en": "Compress current session context and generate session overview",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 命令: /export
    # ═══════════════════════════════════════════════════════════════════════

    "cmd.export.desc": {
        "zh": "导出关键数据为 zip 压缩包",
        "en": "Export key data as a zip archive",
    },
    "cmd.export.done": {
        "zh": "已导出到：\n\n`{path}`\n\n大小：{size:.1f} KB",
        "en": "Exported to:\n\n`{path}`\n\nSize: {size:.1f} KB",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 命令: /import
    # ═══════════════════════════════════════════════════════════════════════

    "cmd.import.desc": {
        "zh": "从 zip 压缩包恢复数据",
        "en": "Restore data from a zip archive",
    },
    "cmd.import.need_path": {
        "zh": "请指定要导入的 zip 文件路径，例如：`/import C:\\Users\\...\\aide_export.zip`",
        "en": "Please specify the zip file path, e.g.: `/import C:\\Users\\...\\aide_export.zip`",
    },
    "cmd.import.not_found": {
        "zh": "文件不存在：`{path}`",
        "en": "File not found: `{path}`",
    },
    "cmd.import.not_zip": {
        "zh": "请选择 .zip 文件",
        "en": "Please select a .zip file",
    },
    "cmd.import.unsafe": {
        "zh": "导入包包含不安全路径：{name}",
        "en": "Import package contains unsafe path: {name}",
    },
    "cmd.import.done": {
        "zh": "数据已从 `{path}` 恢复到 `{root}`",
        "en": "Data restored from `{path}` to `{root}`",
    },
    "cmd.import.invalid_zip": {
        "zh": "文件不是有效的 zip 压缩包",
        "en": "File is not a valid zip archive",
    },
    "cmd.import.failed": {
        "zh": "导入失败：{e}",
        "en": "Import failed: {e}",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 命令: /session
    # ═══════════════════════════════════════════════════════════════════════

    "cmd.session.desc": {
        "zh": "会话管理：list / delete <id>",
        "en": "Session management: list / delete <id>",
    },
    "cmd.session.no_kernel": {
        "zh": "❌ 内核未初始化",
        "en": "❌ Kernel not initialized",
    },
    "cmd.session.empty": {
        "zh": "📭 暂无保存的会话。\n\n输入消息即可自动创建新会话。",
        "en": "📭 No saved sessions.\n\nStart typing to create a new session automatically.",
    },
    "cmd.session.list_title": {
        "zh": "## 会话列表",
        "en": "## Session List",
    },
    "cmd.session.total": {
        "zh": "共 {count} 个会话。",
        "en": "{count} session(s) total.",
    },
    "cmd.session.hint": {
        "zh": "使用 `/session delete <id>` 删除指定会话。",
        "en": "Use `/session delete <id>` to delete a session.",
    },
    "cmd.session.usage_delete": {
        "zh": "⚠️ 用法：`/session delete <会话ID>`\n先用 `/session list` 查看所有会话。",
        "en": "⚠️ Usage: `/session delete <session-id>`\nRun `/session list` first to see all sessions.",
    },
    "cmd.session.deleted": {
        "zh": "✅ 会话 `{id}` 已删除。",
        "en": "✅ Session `{id}` deleted.",
    },
    "cmd.session.not_found": {
        "zh": "❌ 未找到会话 `{id}`。",
        "en": "❌ Session `{id}` not found.",
    },
    "cmd.session.unknown_sub": {
        "zh": "⚠️ 未知子命令。可用：`list`, `delete <id>`",
        "en": "⚠️ Unknown subcommand. Available: `list`, `delete <id>`",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 命令: /memory
    # ═══════════════════════════════════════════════════════════════════════

    "cmd.memory.desc": {
        "zh": "查看记忆捕获状态：pending / confirmed 条目数",
        "en": "View memory capture status: pending / confirmed entry counts",
    },
    "cmd.memory.title": {
        "zh": "## 记忆捕获状态",
        "en": "## Memory Capture Status",
    },
    "cmd.memory.read_error": {
        "zh": "读取失败",
        "en": "Read error",
    },
    "cmd.memory.no_data": {
        "zh": "尚无数据",
        "en": "No data yet",
    },
    "cmd.memory.confirmed": {
        "zh": "{confirmed} 已确认",
        "en": "{confirmed} confirmed",
    },
    "cmd.memory.pending_count": {
        "zh": " / {pending} 待整合",
        "en": " / {pending} pending",
    },
    "cmd.memory.pending_hint": {
        "zh": "📝 {total} 条待整合 — 使用 `/profile update` 整合到 prompt。",
        "en": "📝 {total} pending integration — use `/profile update` to integrate into prompts.",
    },
    "cmd.memory.no_pending": {
        "zh": "✅ 没有待整合的条目。",
        "en": "✅ No pending entries.",
    },
    "cmd.memory.confirmed_summary": {
        "zh": "📊 {total} 条已确认 — 使用 `/profile` 查看。",
        "en": "📊 {total} confirmed — use `/profile` to view.",
    },
    "cmd.memory.hint": {
        "zh": "\n提示：记忆在对话中自动截获，截获规则见 `core/memory/capture.py`。",
        "en": "\nTip: Memories are captured automatically during conversations. See `core/memory/capture.py` for capture rules.",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 命令: /tools
    # ═══════════════════════════════════════════════════════════════════════

    "cmd.tools.desc": {
        "zh": "列出所有可用工具（内置 + 插件 + MCP）",
        "en": "List all available tools (built-in + plugins + MCP)",
    },
    "cmd.tools.no_kernel": {
        "zh": "❌ 内核未初始化",
        "en": "❌ Kernel not initialized",
    },
    "cmd.tools.empty": {
        "zh": "📦 没有已注册的工具。",
        "en": "📦 No registered tools.",
    },
    "cmd.tools.title": {
        "zh": "## 可用工具（共 {count} 个）",
        "en": "## Available Tools ({count} total)",
    },
    "cmd.tools.builtin": {
        "zh": "### 内置工具",
        "en": "### Built-in Tools",
    },
    "cmd.tools.mcp": {
        "zh": "### MCP 工具",
        "en": "### MCP Tools",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 工具描述（ToolRegistry 注册用）
    # ═══════════════════════════════════════════════════════════════════════

    "tool_desc.read_file": {
        "zh": "读取本地文件内容。",
        "en": "Read local file contents.",
    },
    "tool_desc.write_file": {
        "zh": "写入/创建本地文件。",
        "en": "Write/create a local file.",
    },
    "tool_desc.run_shell": {
        "zh": "执行 Shell 命令并返回输出。超时 30 秒。",
        "en": "Execute a shell command and return output. Timeout 30s.",
    },
    "tool_desc.search_memory": {
        "zh": "搜索 Aide 的记忆数据。",
        "en": "Search Aide's memory data.",
    },
    "tool_desc.web_search": {
        "zh": "通过 DuckDuckGo 联网搜索。",
        "en": "Web search via DuckDuckGo.",
    },
    "tool_desc.list_dir": {
        "zh": "列出目录中的文件和子目录。支持 glob 过滤和递归。",
        "en": "List files and subdirectories. Supports glob filtering and recursion.",
    },
    "tool_desc.clipboard": {
        "zh": "读写系统剪贴板。action='read' 读取，action='write' 写入。",
        "en": "Read/write system clipboard. action='read' to read, action='write' to write.",
    },
    "tool_desc.web_fetch": {
        "zh": "抓取 URL 内容并提取纯文本（HTML 标签已剥离）。",
        "en": "Fetch URL content and extract plain text (HTML tags stripped).",
    },
    "tool_desc.search_in_files": {
        "zh": "在文件中搜索内容（正则表达式），类似 grep。支持 glob 过滤和递归。",
        "en": "Search file contents (regex), similar to grep. Supports glob filtering and recursion.",
    },
    "tool_desc.edit_file": {
        "zh": "精确字符串替换编辑文件。old_string 必须在文件中唯一出现。",
        "en": "Precise string-replacement file editing. old_string must appear exactly once in the file.",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 命令: /update
    # ═══════════════════════════════════════════════════════════════════════

    "cmd.update.desc": {
        "zh": "更新 profile：LLM 回溯整合 pending 条目到 prompt",
        "en": "Update profile: LLM retroactively integrates pending entries into prompts",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 命令: /clear
    # ═══════════════════════════════════════════════════════════════════════

    "cmd.clear.desc": {
        "zh": "删除当前会话（输入 /clear 后需确认）",
        "en": "Delete current session (confirmation required after /clear)",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 命令: /rollback
    # ═══════════════════════════════════════════════════════════════════════

    "cmd.rollback.desc": {
        "zh": "回滚到指定轮次：/rollback <轮数>",
        "en": "Rollback to a specific turn: /rollback <turn-number>",
    },
    "cmd.rollback.no_kernel": {
        "zh": "❌ 内核未初始化",
        "en": "❌ Kernel not initialized",
    },
    "cmd.rollback.usage": {
        "zh": "⚠️ 用法：`/rollback <轮数>`\n\n例如：`/rollback 3` 将回到第 3 轮对话，删除第 4 轮及之后的所有记录。\n先用 `/rollback`（不带参数）查看当前轮数。",
        "en": "⚠️ Usage: `/rollback <turn>`\n\nExample: `/rollback 3` returns to turn 3, deleting turn 4 and all subsequent records.\nRun `/rollback` (no args) to see the current turn number.",
    },
    "cmd.rollback.no_session": {
        "zh": "❌ 当前没有活动会话，无法回滚。",
        "en": "❌ No active session — cannot rollback.",
    },
    "cmd.rollback.no_turn": {
        "zh": "❌ 会话状态未初始化。",
        "en": "❌ Session state not initialized.",
    },
    "cmd.rollback.must_be_positive": {
        "zh": "⚠️ 轮数必须 >= 1（当前第 {current} 轮）。",
        "en": "⚠️ Turn number must be >= 1 (currently at turn {current}).",
    },
    "cmd.rollback.future": {
        "zh": "⚠️ 当前是第 {current} 轮，无法回滚到第 {target} 轮。\n\n使用 `/rollback <轮数>` 回滚到更早的轮次。",
        "en": "⚠️ Currently at turn {current}, cannot rollback to turn {target}.\n\nUse `/rollback <number>` to rollback to an earlier turn.",
    },
    "cmd.rollback.confirm": {
        "zh": "⚠️ 确定要回滚到第 **{target}** 轮吗？\n\n将删除第 {from} 轮到第 {to} 轮（共 {deleted} 轮）的对话记录。\n\n已执行的工具调用（文件写入、Shell 命令等）的副作用**不会被撤销**。\n\n输入 **确认** 或 **yes** 来确认，任意其他内容取消。",
        "en": "⚠️ Are you sure you want to rollback to turn **{target}**?\n\nThis will delete turns {from} through {to} ({deleted} turns total) of conversation records.\n\nSide effects from executed tool calls (file writes, shell commands, etc.) **will NOT be undone**.\n\nType **confirm** or **yes** to proceed, anything else to cancel.",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 命令: /mcp
    # ═══════════════════════════════════════════════════════════════════════

    "cmd.mcp.desc": {
        "zh": "管理 MCP 服务端：list/connect <name>/disconnect/reload",
        "en": "Manage MCP servers: list / connect <name> / disconnect / reload",
    },
    "cmd.mcp.no_adapter": {
        "zh": "❌ MCP 适配器未初始化",
        "en": "❌ MCP adapter not initialized",
    },
    "cmd.mcp.empty": {
        "zh": "📦 没有已注册的 MCP 服务端。\n\n将 `.json` 配置放入项目 `mcp/` 目录即可自动加载。",
        "en": "📦 No registered MCP servers.\n\nPlace `.json` config files in the project `mcp/` directory to auto-load.",
    },
    "cmd.mcp.list_title": {
        "zh": "## MCP 服务端",
        "en": "## MCP Servers",
    },
    "cmd.mcp.state_circuit_broken": {
        "zh": "已熔断",
        "en": "Circuit broken",
    },
    "cmd.mcp.state_running": {
        "zh": "运行中",
        "en": "Running",
    },
    "cmd.mcp.state_connected": {
        "zh": "已连接",
        "en": "Connected",
    },
    "cmd.mcp.state_disconnected": {
        "zh": "已断开",
        "en": "Disconnected",
    },
    "cmd.mcp.tool_count": {
        "zh": " — {n} 工具",
        "en": " — {n} tool(s)",
    },
    "cmd.mcp.failure_hint": {
        "zh": "（连续失败 ≥{n} 次，`/mcp connect {name}` 可重置）",
        "en": " (failed ≥{n} consecutive times, `/mcp connect {name}` to reset)",
    },
    "cmd.mcp.total_servers": {
        "zh": "共 {n} 个服务端。",
        "en": "{n} server(s) total.",
    },
    "cmd.mcp.hint": {
        "zh": "使用 `/mcp connect <name>` 连接，`/mcp disconnect <name>` 断开。",
        "en": "Use `/mcp connect <name>` to connect, `/mcp disconnect <name>` to disconnect.",
    },
    "cmd.mcp.usage_connect": {
        "zh": "⚠️ 用法：`/mcp connect <服务端名称>`\n先用 `/mcp list` 查看可用服务端。",
        "en": "⚠️ Usage: `/mcp connect <server-name>`\nRun `/mcp list` first to see available servers.",
    },
    "cmd.mcp.connected": {
        "zh": "✅ 已连接 `{name}`，发现 {count} 个工具。",
        "en": "✅ Connected `{name}`, found {count} tool(s).",
    },
    "cmd.mcp.not_found": {
        "zh": "❌ 服务端未注册: `{name}`\n先用 `/mcp list` 查看可用服务端。",
        "en": "❌ Server not registered: `{name}`\nRun `/mcp list` to see available servers.",
    },
    "cmd.mcp.connect_failed": {
        "zh": "❌ 连接失败: {e}",
        "en": "❌ Connection failed: {e}",
    },
    "cmd.mcp.usage_disconnect": {
        "zh": "⚠️ 用法：`/mcp disconnect <服务端名称>`",
        "en": "⚠️ Usage: `/mcp disconnect <server-name>`",
    },
    "cmd.mcp.disconnected": {
        "zh": "✅ 已断开 `{name}`。",
        "en": "✅ Disconnected `{name}`.",
    },
    "cmd.mcp.reloaded": {
        "zh": "✅ MCP 配置已重载。\n- 新增连接: {added}\n- 重连: {reconnected}\n- 断开: {disconnected}",
        "en": "✅ MCP config reloaded.\n- New connections: {added}\n- Reconnected: {reconnected}\n- Disconnected: {disconnected}",
    },
    "cmd.mcp.unknown_sub": {
        "zh": "⚠️ 未知子命令。可用：`list`, `connect <name>`, `disconnect <name>`, `reload`",
        "en": "⚠️ Unknown subcommand. Available: `list`, `connect <name>`, `disconnect <name>`, `reload`",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 命令: /language
    # ═══════════════════════════════════════════════════════════════════════

    "cmd.language.desc": {
        "zh": "切换界面语言：/language zh|en",
        "en": "Switch interface language: /language zh|en",
    },
    "cmd.language.switched": {
        "zh": "✅ 语言已切换为：{lang}",
        "en": "✅ Language switched to: {lang}",
    },
    "cmd.language.usage": {
        "zh": "⚠️ 用法：`/language zh` 或 `/language en`",
        "en": "⚠️ Usage: `/language zh` or `/language en`",
    },
    "cmd.language.unknown": {
        "zh": "⚠️ 不支持的语言：`{lang}`\n可用：`zh`（中文）、`en`（English）",
        "en": "⚠️ Unsupported language: `{lang}`\nAvailable: `zh` (Chinese), `en` (English)",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 命令: /api
    # ═══════════════════════════════════════════════════════════════════════

    "cmd.api.desc": {
        "zh": "管理 API Key：add <name> <provider> <model> <key> [url] / list / delete <name>",
        "en": "Manage API Keys: add <name> <provider> <model> <key> [url] / list / delete <name>",
    },
    "cmd.api.added": {
        "zh": "✅ 已保存 API 配置：**{name}**\n{provider} / {model}",
        "en": "✅ API config saved: **{name}**\n{provider} / {model}",
    },
    "cmd.api.add_usage": {
        "zh": "⚠️ 用法：`/api add <名称> <provider> <model> <api_key> [base_url]`\n\n例如：`/api add openai openai gpt-4o sk-xxx`\n      `/api add ollama ollama llama3.2 '' http://localhost:11434/v1`",
        "en": "⚠️ Usage: `/api add <name> <provider> <model> <api_key> [base_url]`\n\nExample: `/api add openai openai gpt-4o sk-xxx`\n         `/api add ollama ollama llama3.2 '' http://localhost:11434/v1`",
    },
    "cmd.api.delete_usage": {
        "zh": "⚠️ 用法：`/api delete <名称>`",
        "en": "⚠️ Usage: `/api delete <name>`",
    },
    "cmd.api.list_title": {
        "zh": "## 已保存的 API 配置",
        "en": "## Saved API Configurations",
    },
    "cmd.api.list_empty": {
        "zh": "📦 暂无已保存的 API 配置。\n使用 `/api add <name> ...` 添加。",
        "en": "📦 No saved API configurations.\nUse `/api add <name> ...` to add one.",
    },
    "cmd.api.deleted": {
        "zh": "✅ 已删除 API 配置：`{name}`",
        "en": "✅ API config deleted: `{name}`",
    },
    "cmd.api.not_found": {
        "zh": "❌ 未找到 API 配置：`{name}`\n使用 `/api list` 查看所有已保存的配置。",
        "en": "❌ API config not found: `{name}`\nUse `/api list` to see all saved configs.",
    },
    "cmd.api.active": {
        "zh": " [当前]",
        "en": " [active]",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 命令: /model
    # ═══════════════════════════════════════════════════════════════════════

    "cmd.model.desc": {
        "zh": "切换/管理 API：/model <name> 切换，/model <name> delete 删除",
        "en": "Switch/manage API: /model <name> to switch, /model <name> delete to remove",
    },
    "cmd.model.switched": {
        "zh": "✅ 已切换到：**{name}**（{provider}/{model}）",
        "en": "✅ Switched to: **{name}** ({provider}/{model})",
    },
    "cmd.model.usage": {
        "zh": "⚠️ 用法：`/model <名称>` 切换 API\n      `/model <名称> delete` 删除 API\n      `/model` 查看可用 API\n\n使用 `/api add <name> ...` 添加新配置。",
        "en": "⚠️ Usage: `/model <name>` to switch API\n      `/model <name> delete` to remove API\n      `/model` to list available APIs\n\nUse `/api add <name> ...` to add a new config.",
    },
    "cmd.model.none": {
        "zh": "⚠️ 没有可用的 API 配置。请先用 `/api add` 添加。",
        "en": "⚠️ No API configs available. Use `/api add` to add one first.",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 命令: /plugin
    # ═══════════════════════════════════════════════════════════════════════

    "cmd.plugin.desc": {
        "zh": "管理插件：自动加载 + 列出状态（load/unload/reload <id>）",
        "en": "Manage plugins: auto-load + list status (load/unload/reload <id>)",
    },
    "cmd.plugin.no_plugins": {
        "zh": "📦 没有发现可用插件。\n\n将插件放入 `~/.aide/plugins/<plugin-id>/` 目录，包含 `aide.plugin.json`（Python 插件）或 `SKILL.md`（知识技能）。",
        "en": "📦 No plugins found.\n\nPlace plugins in `~/.aide/plugins/<plugin-id>/` with an `aide.plugin.json` (Python plugin) or `SKILL.md` (knowledge skill).",
    },
    "cmd.plugin.title": {
        "zh": "## 插件",
        "en": "## Plugins",
    },
    "cmd.plugin.loaded": {
        "zh": "已加载",
        "en": "Loaded",
    },
    "cmd.plugin.new_loaded": {
        "zh": "已加载",
        "en": "Loaded",
    },
    "cmd.plugin.load_failed": {
        "zh": "加载失败",
        "en": "Load failed",
    },
    "cmd.plugin.failed_list": {
        "zh": "加载失败：{names}",
        "en": "Load failed: {names}",
    },
    "cmd.plugin.count_loaded": {
        "zh": "{n} 个已加载",
        "en": "{n} loaded",
    },
    "cmd.plugin.count_new": {
        "zh": "{n} 个新加载",
        "en": "{n} newly loaded",
    },
    "cmd.plugin.count_failed": {
        "zh": "{n} 个失败",
        "en": "{n} failed",
    },
    "cmd.plugin.hint": {
        "zh": "使用 `/plugin reload <id>` 重载，`/plugin unload <id>` 卸载。",
        "en": "Use `/plugin reload <id>` to reload, `/plugin unload <id>` to unload.",
    },
    "cmd.plugin.usage_load": {
        "zh": "⚠️ 用法：`/plugin load <插件ID>`",
        "en": "⚠️ Usage: `/plugin load <plugin-id>`",
    },
    "cmd.plugin.load_ok": {
        "zh": "✅ 插件已加载：**{name}** v{version}",
        "en": "✅ Plugin loaded: **{name}** v{version}",
    },
    "cmd.plugin.load_error": {
        "zh": "❌ 加载插件失败：`{id}`\n请检查 manifest 和 entry 文件是否存在。",
        "en": "❌ Failed to load plugin: `{id}`\nCheck that the manifest and entry files exist.",
    },
    "cmd.plugin.usage_unload": {
        "zh": "⚠️ 用法：`/plugin unload <插件ID>`",
        "en": "⚠️ Usage: `/plugin unload <plugin-id>`",
    },
    "cmd.plugin.unload_ok": {
        "zh": "✅ 插件已卸载：`{id}`",
        "en": "✅ Plugin unloaded: `{id}`",
    },
    "cmd.plugin.unload_error": {
        "zh": "❌ 插件 `{id}` 未加载或不存在。",
        "en": "❌ Plugin `{id}` is not loaded or does not exist.",
    },
    "cmd.plugin.usage_reload": {
        "zh": "⚠️ 用法：`/plugin reload <插件ID>`",
        "en": "⚠️ Usage: `/plugin reload <plugin-id>`",
    },
    "cmd.plugin.reload_ok": {
        "zh": "✅ 插件已重载：**{name}** v{version}",
        "en": "✅ Plugin reloaded: **{name}** v{version}",
    },
    "cmd.plugin.reload_error": {
        "zh": "❌ 重载插件失败：`{id}`",
        "en": "❌ Failed to reload plugin: `{id}`",
    },
    "cmd.plugin.unknown_sub": {
        "zh": "⚠️ 未知子命令：`{sub}`\n可用：`load`, `unload`, `reload`",
        "en": "⚠️ Unknown subcommand: `{sub}`\nAvailable: `load`, `unload`, `reload`",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # UI: Onboarding
    # ═══════════════════════════════════════════════════════════════════════

    "ui.onboard.lang_title": {
        "zh": "语言 / Language",
        "en": "语言 / Language",
    },
    "ui.onboard.lang_zh": {
        "zh": "中文",
        "en": "中文",
    },
    "ui.onboard.lang_en": {
        "zh": "English",
        "en": "English",
    },
    "ui.onboard.model_title": {
        "zh": "模型配置",
        "en": "Model Configuration",
    },
    "ui.onboard.label_provider": {
        "zh": "协议",
        "en": "Protocol",
    },
    "ui.onboard.label_model": {
        "zh": "模型",
        "en": "Model",
    },
    "ui.onboard.label_api_key": {
        "zh": "API Key",
        "en": "API Key",
    },
    "ui.onboard.label_base_url": {
        "zh": "Base URL",
        "en": "Base URL",
    },
    "ui.onboard.label_context_window": {
        "zh": "上下文窗口（token）",
        "en": "Context Window (tokens)",
    },
    "ui.onboard.ctx_placeholder": {
        "zh": "128000（0 = 不限制）",
        "en": "128000 (0 = unlimited)",
    },
    "ui.onboard.vision_on": {
        "zh": "多模态（on）",
        "en": "Vision (on)",
    },
    "ui.onboard.vision_off": {
        "zh": "多模态（off）",
        "en": "Vision (off)",
    },
    # 角色模板页（P5）
    "ui.onboard.role_title": {
        "zh": "选择角色",
        "en": "Choose Your Role",
    },
    "ui.onboard.role_desc": {
        "zh": "选择一个预设角色快速配置，或跳过手动设置：",
        "en": "Pick a preset role for quick setup, or skip to customize manually:",
    },
    "ui.onboard.role_dev_label": {
        "zh": "开发者",
        "en": "Developer",
    },
    "ui.onboard.role_dev_desc": {
        "zh": "代码导向 · 简洁实用",
        "en": "Code-focused · Concise & practical",
    },
    "ui.onboard.role_writer_label": {
        "zh": "写作者",
        "en": "Writer",
    },
    "ui.onboard.role_writer_desc": {
        "zh": "文字导向 · 注重表达",
        "en": "Content-focused · Style matters",
    },
    "ui.onboard.role_mgr_label": {
        "zh": "管理者",
        "en": "Manager",
    },
    "ui.onboard.role_mgr_desc": {
        "zh": "任务导向 · 有条理",
        "en": "Task-focused · Organized",
    },
    "ui.onboard.role_skip": {
        "zh": "跳过，手动设置",
        "en": "Skip, customize manually",
    },

    "ui.onboard.personal_title": {
        "zh": "个性化",
        "en": "Personalization",
    },
    "ui.onboard.label_name": {
        "zh": "怎么称呼我？",
        "en": "What should I call you?",
    },
    "ui.onboard.label_personality": {
        "zh": "你希望我的个性是怎样的？",
        "en": "What personality should I have?",
    },
    "ui.onboard.default_personality": {
        "zh": "简洁、友好、直接",
        "en": "Concise, friendly, direct",
    },
    "ui.onboard.label_workstyle": {
        "zh": "偏好的工作方式？",
        "en": "Preferred work style?",
    },
    "ui.onboard.default_workstyle": {
        "zh": "回复简洁，直击要点",
        "en": "Concise replies, get to the point",
    },
    "ui.onboard.label_longterm": {
        "zh": "我需要记住关于你的哪些事？（可选）",
        "en": "What should I remember about you? (optional)",
    },
    "ui.onboard.nav_prev": {
        "zh": "< 上一页",
        "en": "< Previous",
    },
    "ui.onboard.nav_next": {
        "zh": "下一页 >",
        "en": "Next >",
    },
    "ui.onboard.nav_done": {
        "zh": "✓ 完成",
        "en": "✓ Done",
    },
    "ui.onboard.hint_newline": {
        "zh": "Ctrl+J / Ctrl+Enter 换行，Enter 下一步",
        "en": "Ctrl+J / Ctrl+Enter for newline, Enter to continue",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # UI: API Config Screen
    # ═══════════════════════════════════════════════════════════════════════

    "ui.api.label_name": {
        "zh": "API 名称（用于切换）",
        "en": "API Name (for switching)",
    },
    "ui.api.btn_save": {
        "zh": "✓ 保存",
        "en": "✓ Save",
    },
    "ui.api.btn_cancel": {
        "zh": "取消",
        "en": "Cancel",
    },
    "ui.api.hint_newline": {
        "zh": "Enter 保存，Esc 取消",
        "en": "Enter to save, Esc to cancel",
    },
    "ui.api.saved": {
        "zh": "✅ API 配置已保存：**{name}**（{provider}/{model}）",
        "en": "✅ API config saved: **{name}** ({provider}/{model})",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # UI: Home
    # ═══════════════════════════════════════════════════════════════════════

    "ui.home.no_sessions": {
        "zh": "暂无历史会话",
        "en": "No session history",
    },
    "ui.home.input_placeholder": {
        "zh": "输入消息开始新会话...",
        "en": "Type a message to start a new session...",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # UI: Command Handler
    # ═══════════════════════════════════════════════════════════════════════

    "ui.cmd_handler.available": {
        "zh": "可用命令：",
        "en": "Available commands:",
    },
    "ui.cmd_handler.confirm_delete_named": {
        "zh": "⚠️ 确定要删除会话「**{name}**」吗？\n\n此操作不可撤销。输入 **确认** 或 **yes** 来确认，任意其他内容取消。",
        "en": '⚠️ Are you sure you want to delete session "**{name}**"?\n\nThis action is irreversible. Type **confirm** or **yes** to proceed, anything else to cancel.',
    },
    "ui.cmd_handler.confirm_delete": {
        "zh": "⚠️ 确定要删除当前会话吗？\n\n此操作不可撤销。输入 **确认** 或 **yes** 来确认，任意其他内容取消。",
        "en": "⚠️ Are you sure you want to delete the current session?\n\nThis action is irreversible. Type **confirm** or **yes** to proceed, anything else to cancel.",
    },
    "ui.cmd_handler.rollback_cancelled": {
        "zh": "已取消回溯。",
        "en": "Rollback cancelled.",
    },
    "ui.cmd_handler.clear_cancelled": {
        "zh": "已取消删除。",
        "en": "Delete cancelled.",
    },
    "ui.cmd_handler.cmd_failed": {
        "zh": "命令执行失败: {e}",
        "en": "Command execution failed: {e}",
    },
    "ui.cmd_handler.session_missing": {
        "zh": "❌ 会话目录不存在。",
        "en": "❌ Session directory does not exist.",
    },
    "ui.cmd_handler.rollback_failed": {
        "zh": "回滚失败：{e}",
        "en": "Rollback failed: {e}",
    },
    "ui.cmd_handler.rollback_done": {
        "zh": "✅ 已回溯到第 **{target}** 轮（删除了 {deleted} 轮对话记录）。\n\n⚠️ **注意**：已执行的工具调用（文件写入、Shell 命令等）的副作用**不会被撤销**。",
        "en": "✅ Rolled back to turn **{target}** ({deleted} turns deleted).\n\n⚠️ **Note**: Side effects from executed tool calls (file writes, shell commands, etc.) **will NOT be undone**.",
    },
    "ui.cmd_handler.session_deleted": {
        "zh": "✅ 会话已删除。",
        "en": "✅ Session deleted.",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # UI: Bridge
    # ═══════════════════════════════════════════════════════════════════════

    "ui.bridge.tool_error": {
        "zh": "工具 [{name}] 执行失败: {error}",
        "en": "Tool [{name}] execution failed: {error}",
    },
    "ui.bridge.max_turns": {
        "zh": "已达到最大工具调用轮次 (5)。任务可能需要你手动介入。",
        "en": "Maximum tool call turns reached (5). The task may need your manual intervention.",
    },
    "ui.bridge.captured": {
        "zh": "📝 **已记住：**",
        "en": "📝 **Remembered:**",
    },
    "ui.bridge.and_more": {
        "zh": "  …以及其他 {n} 条",
        "en": "  ... and {n} more",
    },
    "ui.bridge.integrate_hint": {
        "zh": "\n用 `/profile update` 整合到 prompt。",
        "en": "\nUse `/profile update` to integrate into prompts.",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # UI: Widgets
    # ═══════════════════════════════════════════════════════════════════════

    "ui.widget.no_match": {
        "zh": " 无匹配命令",
        "en": " No matching commands",
    },
    "ui.widget.input_placeholder": {
        "zh": "输入消息...",
        "en": "Type a message...",
    },
    "ui.widget.unknown_command": {
        "zh": "未知的命令：{text}",
        "en": "Unknown command: {text}",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # App 层
    # ═══════════════════════════════════════════════════════════════════════

    "app.bootstrap_failed": {
        "zh": "Bootstrap 失败: {e}",
        "en": "Bootstrap failed: {e}",
    },
    "app.provider_init_failed": {
        "zh": "冷启动后 provider 初始化失败: {e}",
        "en": "Provider initialization failed after cold start: {e}",
    },
    "app.no_provider": {
        "zh": "⚠️ 尚未配置 API。\n\n请输入 `/api` 添加 API 配置（需填写 API 名称、协议、模型和 Key），然后用 `/model <名称>` 切换。\n\n也支持 Ollama 本地模型：`/api add ollama ollama llama3.2 '' http://localhost:11434/v1`",
        "en": "⚠️ No API configured yet.\n\nType `/api` to add an API configuration (name, provider, model, key), then `/model <name>` to switch.\n\nAlso supports local Ollama: `/api add ollama ollama llama3.2 '' http://localhost:11434/v1`",
    },
    "app.no_api_configured": {
        "zh": "⚠️ 尚未配置 API，请在冷启动向导中完成模型配置，或使用 `/api` 添加。",
        "en": "⚠️ No API configured. Complete model setup in the wizard, or use `/api` to add one.",
    },
    "app.image_msg": {
        "zh": "{n} 张图片",
        "en": "{n} image(s)",
    },
    "app.image_msg_fallback": {
        "zh": "图片消息",
        "en": "Image message",
    },
    "app.files_attached": {
        "zh": "{n} 个文件",
        "en": "{n} file(s)",
    },
    "app.exec_error": {
        "zh": "执行异常: {e}",
        "en": "Execution error: {e}",
    },
    "app.profile_updated": {
        "zh": "✅ Prompt 更新完成：{names}",
        "en": "✅ Prompt update complete: {names}",
    },
    "app.profile_no_update": {
        "zh": "⚠️ 没有待更新的条目，或更新失败",
        "en": "⚠️ No pending entries to update, or update failed",
    },
    "app.profile_update_failed": {
        "zh": "Prompt 更新失败: {e}",
        "en": "Prompt update failed: {e}",
    },
    "app.no_data_to_compact": {
        "zh": "⚠️ 当前没有会话数据，无需压缩",
        "en": "⚠️ No session data to compress",
    },
    "app.compact_topics": {
        "zh": "话题",
        "en": "Topics",
    },
    "app.compact_prefs": {
        "zh": "用户偏好",
        "en": "User Preferences",
    },
    "app.compact_decisions": {
        "zh": "决策与结论",
        "en": "Decisions & Conclusions",
    },
    "app.compact_done": {
        "zh": "✅ 会话压缩完成",
        "en": "✅ Session compression complete",
    },
    "app.compact_topics_line": {
        "zh": "话题: {topics}",
        "en": "Topics: {topics}",
    },
    "app.compact_prefs_line": {
        "zh": "偏好: {n} 条",
        "en": "Preferences: {n} item(s)",
    },
    "app.compact_decisions_line": {
        "zh": "决策: {n} 条",
        "en": "Decisions: {n} item(s)",
    },
    "app.compact_failed": {
        "zh": "⚠️ 压缩失败，请查看日志",
        "en": "⚠️ Compression failed, check logs",
    },
    "app.compact_error": {
        "zh": "压缩失败: {e}",
        "en": "Compression failed: {e}",
    },
    "app.tray_hidden": {
        "zh": "已隐藏到系统托盘",
        "en": "Hidden to system tray",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 工具: read_file
    # ═══════════════════════════════════════════════════════════════════════

    "tool.read_file.empty_path": {
        "zh": "错误：file_path 不能为空",
        "en": "Error: file_path cannot be empty",
    },
    "tool.read_file.not_found": {
        "zh": "错误：文件不存在 — {path}",
        "en": "Error: file not found — {path}",
    },
    "tool.read_file.is_dir": {
        "zh": "错误：{path} 是一个目录，请指定文件路径",
        "en": "Error: {path} is a directory, please specify a file path",
    },
    "tool.read_file.no_permission": {
        "zh": "错误：没有读取权限 — {path}",
        "en": "Error: no read permission — {path}",
    },
    "tool.read_file.read_failed": {
        "zh": "错误：读取文件失败 — {e}",
        "en": "Error: failed to read file — {e}",
    },
    "tool.read_file.truncated": {
        "zh": "（文件过大，已截断显示前 ~100KB）",
        "en": " (file too large, truncated to first ~100 KB)",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 工具: write_file
    # ═══════════════════════════════════════════════════════════════════════

    "tool.write_file.empty_path": {
        "zh": "错误：file_path 不能为空",
        "en": "Error: file_path cannot be empty",
    },
    "tool.write_file.is_dir": {
        "zh": "错误：{path} 是一个目录，无法写入",
        "en": "Error: {path} is a directory, cannot write",
    },
    "tool.write_file.no_permission": {
        "zh": "错误：没有写入权限 — {path}",
        "en": "Error: no write permission — {path}",
    },
    "tool.write_file.write_failed": {
        "zh": "错误：写入文件失败 — {e}",
        "en": "Error: failed to write file — {e}",
    },
    "tool.write_file.done": {
        "zh": "已写入 {path}（{size} 字节）",
        "en": "Written {path} ({size} bytes)",
    },
    "tool.write_file.too_large": {
        "zh": "错误：内容过大 — 超过 {max_kb}KB 写入上限",
        "en": "Error: content too large — exceeds {max_kb}KB write limit",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 工具: edit_file
    # ═══════════════════════════════════════════════════════════════════════

    "tool.edit_file.empty_path": {
        "zh": "错误：file_path 不能为空",
        "en": "Error: file_path cannot be empty",
    },
    "tool.edit_file.empty_old": {
        "zh": "错误：old_string 不能为空",
        "en": "Error: old_string cannot be empty",
    },
    "tool.edit_file.not_found": {
        "zh": "错误：文件不存在 — {path}",
        "en": "Error: file not found — {path}",
    },
    "tool.edit_file.not_file": {
        "zh": "错误：路径不是文件 — {path}",
        "en": "Error: path is not a file — {path}",
    },
    "tool.edit_file.decode_error": {
        "zh": "错误：无法以 UTF-8 解码文件 — {path}",
        "en": "Error: cannot decode file as UTF-8 — {path}",
    },
    "tool.edit_file.no_read_permission": {
        "zh": "错误：无权限读取文件 — {path}",
        "en": "Error: no permission to read file — {path}",
    },
    "tool.edit_file.not_unique": {
        "zh": "错误：old_string 在文件中出现了 {count} 次，请提供更长的上下文使其唯一",
        "en": "Error: old_string appears {count} times in the file. Provide more context to make it unique.",
    },
    "tool.edit_file.no_write_permission": {
        "zh": "错误：无权限写入文件 — {path}",
        "en": "Error: no permission to write file — {path}",
    },
    "tool.edit_file.write_failed": {
        "zh": "错误：写入文件失败 — {e}",
        "en": "Error: failed to write file — {e}",
    },
    "tool.edit_file.done": {
        "zh": "已编辑 {name}\n  {old_lines} 行 → {new_lines} 行，{old_char} 字符 → {new_char} 字符",
        "en": "Edited {name}\n  {old_lines} lines → {new_lines} lines, {old_char} chars → {new_char} chars",
    },
    "tool.edit_file.too_large": {
        "zh": "错误：文件过大 — 超过 {max_kb}KB 的不可编辑",
        "en": "Error: file too large — cannot edit files over {max_kb}KB",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 工具: run_shell
    # ═══════════════════════════════════════════════════════════════════════

    "tool.run_shell.empty_command": {
        "zh": "错误：command 不能为空",
        "en": "Error: command cannot be empty",
    },
    "tool.run_shell.timeout": {
        "zh": "错误：命令超时（{timeout}s）— {command}",
        "en": "Error: command timed out ({timeout}s) — {command}",
    },
    "tool.run_shell.not_found": {
        "zh": "错误：找不到命令 — {command}",
        "en": "Error: command not found — {command}",
    },
    "tool.run_shell.failed": {
        "zh": "错误：执行命令失败 — {e}",
        "en": "Error: command execution failed — {e}",
    },
    "tool.run_shell.exit_code": {
        "zh": "（退出码: {code}）",
        "en": " (exit code: {code})",
    },
    "tool.run_shell.exit_code_no_output": {
        "zh": "（退出码: {code}，无输出）",
        "en": " (exit code: {code}, no output)",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 工具: search_in_files
    # ═══════════════════════════════════════════════════════════════════════

    "tool.search_in_files.empty_pattern": {
        "zh": "错误：pattern 不能为空",
        "en": "Error: pattern cannot be empty",
    },
    "tool.search_in_files.dir_not_found": {
        "zh": "错误：目录不存在 — {path}",
        "en": "Error: directory not found — {path}",
    },
    "tool.search_in_files.not_dir": {
        "zh": "错误：路径不是目录 — {path}",
        "en": "Error: path is not a directory — {path}",
    },
    "tool.search_in_files.invalid_regex": {
        "zh": "错误：无效的正则表达式 — {e}",
        "en": "Error: invalid regex — {e}",
    },
    "tool.search_in_files.no_permission": {
        "zh": "错误：无权限读取目录 — {e}",
        "en": "Error: no permission to read directory — {e}",
    },
    "tool.search_in_files.no_match": {
        "zh": "未找到匹配 '{pattern}' 的文件。",
        "en": "No files matched '{pattern}'.",
    },
    "tool.search_in_files.truncated": {
        "zh": "…（已达到结果上限 {max}，可能还有更多匹配）",
        "en": "... (result limit of {max} reached, there may be more matches)",
    },
    "tool.search_in_files.too_many_files": {
        "zh": "…（已达到文件扫描上限 {max}，暂停搜索）",
        "en": "... (file scan limit of {max} reached, search stopped)",
    },
    "tool.search_in_files.skipped_large": {
        "zh": "（已跳过 {n} 个超 1MB 的大文件）",
        "en": "(skipped {n} large files over 1MB)",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 工具: list_dir
    # ═══════════════════════════════════════════════════════════════════════

    "tool.list_dir.not_found": {
        "zh": "错误：路径不存在 — {path}",
        "en": "Error: path does not exist — {path}",
    },
    "tool.list_dir.not_dir": {
        "zh": "错误：{path} 不是目录，请使用 read_file 读取文件",
        "en": "Error: {path} is not a directory, use read_file to read the file",
    },
    "tool.list_dir.no_permission": {
        "zh": "错误：没有读取权限 — {path}",
        "en": "Error: no read permission — {path}",
    },
    "tool.list_dir.failed": {
        "zh": "错误：列出目录失败 — {e}",
        "en": "Error: failed to list directory — {e}",
    },
    "tool.list_dir.empty": {
        "zh": "目录为空：{path}",
        "en": "Directory is empty: {path}",
    },
    "tool.list_dir.empty_pattern": {
        "zh": "（模式: {pattern}）",
        "en": " (pattern: {pattern})",
    },
    "tool.list_dir.total": {
        "zh": "共 {n} 项",
        "en": "{n} item(s) total",
    },
    "tool.list_dir.max_items": {
        "zh": "…（已达 {max} 项上限，已截断）",
        "en": "... (limit of {max} items reached, truncated)",
    },
    "tool.list_dir.too_large": {
        "zh": "…（输出过大，已截断）",
        "en": "... (output too large, truncated)",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 工具: web_search
    # ═══════════════════════════════════════════════════════════════════════

    "tool.web_search.empty_query": {
        "zh": "错误：query 不能为空",
        "en": "Error: query cannot be empty",
    },
    "tool.web_search.failed": {
        "zh": "错误：搜索失败 — {e}",
        "en": "Error: search failed — {e}",
    },
    "tool.web_search.timeout": {
        "zh": "错误：搜索超时（{timeout} 秒）— DuckDuckGo 可能暂时不可用",
        "en": "Error: search timed out ({timeout}s) — DuckDuckGo may be temporarily unavailable",
    },
    "tool.web_search.no_results": {
        "zh": "未找到与 '{query}' 相关的结果。",
        "en": "No results found for '{query}'.",
    },
    "tool.web_search.results_for": {
        "zh": "搜索 '{query}' 的结果：",
        "en": "Results for '{query}':",
    },
    "tool.web_search.untitled": {
        "zh": "无标题",
        "en": "Untitled",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 工具: web_fetch
    # ═══════════════════════════════════════════════════════════════════════

    "tool.web_fetch.empty_url": {
        "zh": "错误：url 不能为空",
        "en": "Error: url cannot be empty",
    },
    "tool.web_fetch.invalid_url": {
        "zh": "错误：url 必须以 http:// 或 https:// 开头",
        "en": "Error: url must start with http:// or https://",
    },
    "tool.web_fetch.truncated": {
        "zh": "…（已截断，原始长度 {n} 字符）",
        "en": "... (truncated, original length {n} characters)",
    },
    "tool.web_fetch.http_error": {
        "zh": "错误：HTTP {code} — {reason}",
        "en": "Error: HTTP {code} — {reason}",
    },
    "tool.web_fetch.unreachable": {
        "zh": "错误：无法访问 URL — {reason}",
        "en": "Error: cannot access URL — {reason}",
    },
    "tool.web_fetch.ssl_error": {
        "zh": "错误：SSL 验证失败 — {e}",
        "en": "Error: SSL verification failed — {e}",
    },
    "tool.web_fetch.timeout": {
        "zh": "错误：请求超时（{timeout} 秒）",
        "en": "Error: request timed out ({timeout} seconds)",
    },
    "tool.web_fetch.failed": {
        "zh": "错误：抓取失败 — {type}: {e}",
        "en": "Error: fetch failed — {type}: {e}",
    },
    "tool.web_fetch.private_host": {
        "zh": "错误：安全限制 — 禁止访问内网地址 ({host})",
        "en": "Error: security restriction — private/internal address blocked ({host})",
    },
    "tool.web_fetch.too_large": {
        "zh": "错误：内容过大 — 超过 {max_mb}MB 下载上限",
        "en": "Error: content too large — exceeds {max_mb}MB download limit",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 工具: clipboard
    # ═══════════════════════════════════════════════════════════════════════

    "tool.clipboard.empty": {
        "zh": "（剪贴板为空）",
        "en": " (clipboard empty)",
    },
    "tool.clipboard.truncated": {
        "zh": "…（内容过长，已截断）",
        "en": "... (content too long, truncated)",
    },
    "tool.clipboard.read_failed": {
        "zh": "错误：读取剪贴板失败 — {e}",
        "en": "Error: failed to read clipboard — {e}",
    },
    "tool.clipboard.empty_text": {
        "zh": "错误：写入剪贴板时 text 参数不能为空",
        "en": "Error: text parameter cannot be empty when writing to clipboard",
    },
    "tool.clipboard.written": {
        "zh": "已写入剪贴板（{n} 字符）",
        "en": "Written to clipboard ({n} characters)",
    },
    "tool.clipboard.write_failed": {
        "zh": "错误：写入剪贴板失败 — {e}",
        "en": "Error: failed to write to clipboard — {e}",
    },
    "tool.clipboard.unknown_action": {
        "zh": "错误：未知的剪贴板操作 '{action}'。可用：read, write",
        "en": "Error: unknown clipboard action '{action}'. Available: read, write",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 工具: search_memory
    # ═══════════════════════════════════════════════════════════════════════

    "tool.search_memory.empty_query": {
        "zh": "错误：query 不能为空",
        "en": "Error: query cannot be empty",
    },
    "tool.search_memory.no_match": {
        "zh": "未找到与 '{query}' 相关的记忆记录。\n记忆系统会在你使用 Aide 的过程中自动积累。",
        "en": "No memories found matching '{query}'.\nThe memory system builds up automatically as you use Aide.",
    },
    "tool.search_memory.found": {
        "zh": "找到 {n} 条与 '{query}' 相关的记忆：",
        "en": "Found {n} memories matching '{query}':",
    },
    "tool.search_memory.more": {
        "zh": "…以及其他 {n} 条结果",
        "en": "... and {n} more result(s)",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 记忆管线
    # ═══════════════════════════════════════════════════════════════════════

    "mem.label_preferences": {
        "zh": "偏好",
        "en": "Preferences",
    },
    "mem.label_workflows": {
        "zh": "工作流",
        "en": "Workflows",
    },
    "mem.label_long_term_memory": {
        "zh": "长记忆",
        "en": "Long-Term Memory",
    },
    "mem.label_memory": {
        "zh": "记忆",
        "en": "Memory",
    },
    "mem.entry_type_preferences": {
        "zh": "偏好条目",
        "en": "Preference entry",
    },
    "mem.entry_type_workflows": {
        "zh": "工作流条目",
        "en": "Workflow entry",
    },
    "mem.entry_type_long_term_memory": {
        "zh": "长记忆条目",
        "en": "Long-term memory entry",
    },
    "mem.unknown_entry_type": {
        "zh": "未知条目类型: {type}，有效值: {valid}",
        "en": "Unknown entry type: {type}, valid values: {valid}",
    },
    "mem.new_entry": {
        "zh": "新增条目 [{type}]: {content}…",
        "en": "New entry [{type}]: {content}...",
    },
    "mem.captured": {
        "zh": "截获 {n} 条: ...",
        "en": "Captured {n} item(s): ...",
    },
    "mem.dedup_updated": {
        "zh": "去重更新 [{type}] #{i}: ...",
        "en": "Dedup update [{type}] #{i}: ...",
    },
    "mem.no_provider": {
        "zh": "Provider 未初始化，无法更新 prompt",
        "en": "Provider not initialized, cannot update prompts",
    },
    "mem.update_complete": {
        "zh": "Prompt 更新完成: {results}",
        "en": "Prompt update complete: {results}",
    },
    "mem.no_pending": {
        "zh": "[{label}] 无 pending 条目，跳过",
        "en": "[{label}] No pending entries, skipped",
    },
    "mem.orphaned": {
        "zh": "[{label}] 条目标记 orphaned: ...",
        "en": "[{label}] Entry marked orphaned: ...",
    },
    "mem.integrated": {
        "zh": "[{label}] 更新完成: {n} 条 → integrated",
        "en": "[{label}] Update complete: {n} item(s) → integrated",
    },
    "mem.session_bg": {
        "zh": "[会话 {id}] 背景: ...",
        "en": "[Session {id}] Background: ...",
    },
    "mem.entry_turn": {
        "zh": " ← 条目产生轮",
        "en": " ← Entry origin turn",
    },
    "mem.turn_user": {
        "zh": "[轮 {t}{marker}] 用户: {text}",
        "en": "[Turn {t}{marker}] User: {text}",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 上下文管线
    # ═══════════════════════════════════════════════════════════════════════

    "ctx.others": {
        "zh": "（其他： {items}）",
        "en": " (Others: {items})",
    },
    "ctx.session_overview": {
        "zh": "[会话历史总览]",
        "en": "[Session History Overview]",
    },
    "ctx.recent_chat": {
        "zh": "## 最近对话",
        "en": "## Recent Conversation",
    },
    "ctx.no_messages_to_compact": {
        "zh": "messages/ 目录不存在，无法压缩",
        "en": "messages/ directory does not exist, cannot compress",
    },
    "ctx.no_records_to_compact": {
        "zh": "无对话记录，无法压缩",
        "en": "No conversation records, cannot compress",
    },
    "ctx.turn_label": {
        "zh": "--- 第 {turn} 轮 ---",
        "en": "--- Turn {turn} ---",
    },
    "ctx.turn_user_label": {
        "zh": "用户: {text}",
        "en": "User: {text}",
    },
    "ctx.turn_ai_label": {
        "zh": "Aide: {text}",
        "en": "Aide: {text}",
    },
    "ctx.tool_call": {
        "zh": "[工具调用: {name}]",
        "en": "[Tool call: {name}]",
    },
    "ctx.omitted_prefix": {
        "zh": "…(前段省略)…",
        "en": "...(earlier content omitted)...",
    },
    "ctx.conversation_record": {
        "zh": "对话记录：",
        "en": "Conversation record:",
    },
    "ctx.existing_overview": {
        "zh": "已有的会话总览：",
        "en": "Existing session overview:",
    },
    "ctx.generate_overview": {
        "zh": "请基于以上信息生成更新后的总览。",
        "en": "Please generate an updated overview based on the above information.",
    },
    "ctx.compact_done": {
        "zh": "压缩完成: {turns} 轮 → overview.md + 检查点 (to_turn={turn})",
        "en": "Compression complete: {turns} turns → overview.md + checkpoint (to_turn={turn})",
    },
    "ctx.compact_llm_stream_error": {
        "zh": "压缩 LLM 流处理类型错误",
        "en": "Compression LLM stream type error",
    },
    "ctx.compact_llm_error": {
        "zh": "压缩 LLM 调用失败",
        "en": "Compression LLM call failed",
    },
    "ctx.ingest_session_start": {
        "zh": "会话开始: {id}",
        "en": "Session started: {id}",
    },
    "ctx.ingest_turn": {
        "zh": "turn {turn} 已摄取: {summary}",
        "en": "turn {turn} ingested: {summary}",
    },
    "ctx.ingest_tool_call": {
        "zh": "[工具调用] {tools} → {preview}",
        "en": "[Tool call] {tools} → {preview}",
    },
    "ctx.history_prefix": {
        "zh": "[历史] {first}...",
        "en": "[History] {first}...",
    },
    "ctx.discussed": {
        "zh": "此前讨论了{topics}",
        "en": "Previously discussed: {topics}",
    },
    "ctx.decided": {
        "zh": "期间确定：{decisions}",
        "en": "Decided: {decisions}",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # Compactor System Prompt
    # ═══════════════════════════════════════════════════════════════════════

    "ctx.compact_system_prompt": {
        "zh": """你是会话压缩器。将完整的对话记录压缩为 Markdown 结构化总览。

用「## 话题」概括对话中讨论的所有主题，每条用 "-" 开头。
用「## 用户偏好」记录用户表达的任何偏好、习惯、工作流程。
用「## 纠正记录」记录用户纠正你行为的时刻。
用「## 决策与结论」记录对话中作出的决策和结论。

只输出结构化 Markdown，不要添加解释或前言。用中文输出。""",
        "en": """You are a session compressor. Compress the full conversation record into a structured Markdown overview.

Use "## Topics" to summarize all topics discussed, each starting with "-".
Use "## User Preferences" to record any preferences, habits, or workflows the user expressed.
Use "## Corrections" to record moments where the user corrected your behavior.
Use "## Decisions & Conclusions" to record decisions and conclusions made during the conversation.

Output only structured Markdown. Do not add explanations or preamble. Output in English.""",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # Updater System Prompts
    # ═══════════════════════════════════════════════════════════════════════

    "mem.updater_prefs_system": {
        "zh": """你是 Prompt 演化引擎，负责维护 Aide 的「偏好」prompt。

你的职责：
1. 维护一个简洁、清晰的 Markdown 文件，描述用户的偏好要求
2. 将新的用户信号整合进来，去掉过时的信息
3. 用中文输出
4. 只输出 prompt 内容，不要解释你做了什么""",
        "en": """You are the Prompt Evolution Engine, responsible for maintaining Aide's "Preferences" prompt.

Your responsibilities:
1. Maintain a concise, clear Markdown file describing the user's preferences
2. Integrate new user signals and remove outdated information
3. Output in English
4. Output only the prompt content — do not explain what you did""",
    },
    "mem.updater_workflows_system": {
        "zh": """你是 Prompt 演化引擎，负责维护 Aide 的「工作流」prompt。

你的职责：
1. 维护一个简洁、清晰的 Markdown 文件，描述用户的工作流偏好
2. 将新的用户信号整合进来，去掉过时的信息
3. 用中文输出
4. 只输出 prompt 内容，不要解释你做了什么""",
        "en": """You are the Prompt Evolution Engine, responsible for maintaining Aide's "Workflows" prompt.

Your responsibilities:
1. Maintain a concise, clear Markdown file describing the user's workflow preferences
2. Integrate new user signals and remove outdated information
3. Output in English
4. Output only the prompt content — do not explain what you did""",
    },
    "mem.updater_longterm_system": {
        "zh": """你是 Prompt 演化引擎，负责维护 Aide 的「长记忆」prompt。

你的职责：
1. 维护一个简洁、清晰的 Markdown 文件，描述跨会话的重要事实
2. 将新的用户信号整合进来，去掉过时的信息
3. 用中文输出
4. 只输出 prompt 内容，不要解释你做了什么""",
        "en": """You are the Prompt Evolution Engine, responsible for maintaining Aide's "Long-Term Memory" prompt.

Your responsibilities:
1. Maintain a concise, clear Markdown file describing important cross-session facts
2. Integrate new user signals and remove outdated information
3. Output in English
4. Output only the prompt content — do not explain what you did""",
    },
    "mem.updater_user_template": {
        "zh": """当前 prompt：
{current_prompt}

来自源会话的新用户信号：
{signals}

待整合的条目：
{pending_entries}

请生成更新后的完整 prompt。""",
        "en": """Current prompt:
{current_prompt}

New user signals from source session:
{signals}

Pending entries to integrate:
{pending_entries}

Please generate the updated complete prompt.""",
    },
    # ── MCP 适配器错误 ──
    "mcp.not_connected": {
        "zh": "错误：MCP 服务端未连接: {server}",
        "en": "Error: MCP server not connected: {server}",
    },
    "mcp.reconnect_failed": {
        "zh": "错误：MCP 工具调用失败（重连后）: {e}",
        "en": "Error: MCP tool call failed (after reconnect): {e}",
    },
    "mcp.disconnected_reconnect_failed": {
        "zh": "错误：MCP 服务端 {server} 已断开且重连失败",
        "en": "Error: MCP server {server} disconnected and reconnect failed",
    },
    "mcp.timeout": {
        "zh": "错误：MCP 工具 {tool} 执行超时 ({timeout}s)",
        "en": "Error: MCP tool {tool} timed out ({timeout}s)",
    },
    "mcp.call_failed": {
        "zh": "错误：MCP 工具调用失败: {e}",
        "en": "Error: MCP tool call failed: {e}",
    },
    "mcp.error_response": {
        "zh": "错误：MCP 工具返回错误: {msg}",
        "en": "Error: MCP tool returned error: {msg}",
    },
    "mcp.invalid_tool_name": {
        "zh": "错误：无效的 MCP 工具名: {name}",
        "en": "Error: Invalid MCP tool name: {name}",
    },
    # ── 工具注册/重试 ──
    "tool.registry.not_found": {
        "zh": "错误：未找到工具 '{name}'。可用工具：{tools}",
        "en": "Error: Tool '{name}' not found. Available: {tools}",
    },
    "tool.registry.no_execute": {
        "zh": "错误：工具 '{name}' 已注册但无可执行体。",
        "en": "Error: Tool '{name}' is registered but has no executor.",
    },
    "tool.retry.error": {
        "zh": "错误：{msg}",
        "en": "Error: {msg}",
    },
    "tool.retry.exhausted": {
        "zh": "错误：{msg}（已重试 {n} 次）",
        "en": "Error: {msg} (retried {n} time(s))",
    },
    "tool.edit_file.not_found_in_file": {
        "zh": "错误：文件中未找到指定的 old_string",
        "en": "Error: specified old_string not found in file",
    },
    # ── LLM Gateway ──
    "llm.unsupported_provider": {
        "zh": "不支持的 LLM provider: {provider}\n支持: {supported}",
        "en": "Unsupported LLM provider: {provider}\nSupported: {supported}",
    },
}
