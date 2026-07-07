"""Locale strings — commands domain."""

_STRINGS = {
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

}
