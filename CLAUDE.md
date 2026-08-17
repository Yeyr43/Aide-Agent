# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

Aide Agent — 本地个人智能管家。核心不是"能做多少事"而是"越用越懂你"。演化不靠能力积累，靠动态 prompt。

**关键原则**：用户可控、本地隐私、边界清晰、渐进演化。所有数据本地存储，备份即复制文件夹。

## 常用命令

```bash
# 运行应用
uv run python core/main.py

# 运行全部测试（1765 个）
uv run pytest tests/ -q

# 运行单个测试文件
uv run pytest tests/test_config.py -q

# 运行单个测试函数
uv run pytest tests/test_commands.py::test_route_command -q

# 依赖安装
uv sync

# 构建独立分发包
uv run python core/build.py
```

## 平台特定依赖

### Windows
无需额外系统依赖。`uv sync` 即可。

### macOS
```bash
uv sync --extra macos
# 或手动安装
uv pip install pyobjc-framework-Quartz
```

### Linux
pystray 依赖系统 GTK/AppIndicator 包：

| 发行版 | 系统包 |
|--------|--------|
| Debian/Ubuntu | `python3-gi gir1.2-gtk-3.0 gir1.2-appindicator3-0.1` |
| Fedora | `python3-gobject gtk3 libappindicator-gtk3` |
| Arch | `python-gobject gtk3 libappindicator-gtk3` |

`pyperclip` 需要 `xclip`（X11）或 `wl-clipboard`（Wayland）。`PIL.ImageGrab` 在 headless 环境自动降级返回 None。

平台验证：
```bash
bash core/verify_linux.sh   # Linux
bash core/verify_macos.sh   # macOS
```

## 技术栈

Python 3.13+ + Textual 0.80+ + pystray + asyncio + JSON 文件系统

- **Textual**：全栈纯 Python TUI
- **pystray**：系统托盘后台常驻
- **asyncio**：统一并发模型
- **JSON 文件系统**：零依赖存储，Write-Actor 并发模型（tempfile + os.replace）
- **Pygments**：代码语法高亮

## 核心架构

```
core/
├── setup.py             # ~/.aide/ 目录初始化 + 冷启动判断 + 旧配置迁移
├── config.py            # Config dataclass — 分层加载 (cli > env > settings.json > defaults)
├── storage.py           # JSON 读写 + Write-Actor + JSONL 工具函数
├── resources.py         # is_bundled() / get_resource_path() — dev/bundle 双模式路径解析
├── errors.py            # 统一错误类型（AideError / ProviderError / ToolError / ConfigError / SessionError）
├── launcher.py          # 应用启动工具 — 单实例锁（Windows 校验 PID 映像名防复用）、窗口激活、控制台装饰、托盘守护进程拉起
├── main.py              # 应用入口 + 烟雾测试（uv run python core/main.py；`--no-daemon` 跳过托盘便于调试）
├── tray_daemon.py       # 系统托盘后台守护进程（强杀 TUI 后清理 aide.pid 实例锁）
├── build.py             # 独立分发包构建（PyInstaller 打包 + 验证 + 安装脚本）
├── kernel/              # Agent 内核（零 UI 依赖）
│   ├── bootstrap.py     # AppBootstrap — 5-phase 组合根（_init_provider / _init_tooling / _init_storage_and_context / _init_plugins / _init_kernel）
│   ├── context.py       # KernelContext — 依赖聚合（Memory/Tooling/Session 三个子 context）
│   ├── agent.py         # AgentKernel 门面 — chat() 通过 MiddlewareRunner 编排 6 步管线
│   ├── fc_loop.py       # Function Calling 循环（max_turns=10，XML fallback，smart continuation）
│   ├── tool_executor.py # ToolExecutor — 只读并行/写串行/abort 兄弟 + 超时 + 截断 + 安全（从 fc_loop 提取）
│   ├── middleware.py    # ChatMiddleware Protocol + ChatContext + MiddlewareRunner
│   ├── safety.py        # check_tool_safety() — 高危命令拦截（从 fc_loop 提取）
│   ├── xml_tool_parser.py  # extract_xml_tool_calls() — XML fallback 解析（从 fc_loop 提取）
│   └── protocols.py     # ExecutorUI Protocol + NullUI + ChatResult + TokenUsage
├── llm_gateway/         # LLM 适配层：1 兼容基类 + 3 具体 Provider（OpenAI/Ollama/Anthropic）+ 图片/内容/tool_call 构建
│   ├── provider.py      # AbstractProvider Protocol + StreamEvent 类型（TextDelta/ThinkingDelta/StreamEnd）
│   ├── openai_compatible_provider.py  # OpenAI 兼容协议基类
│   ├── anthropic_provider.py   # Anthropic Messages API（原生协议适配）
│   ├── tool_call_builder.py    # SSE delta 累积
│   ├── image_utils.py   # 剪贴板图片、base64 编码
│   └── content_builder.py  # 多模态 content 构建
├── context/             # 上下文管线 — 优先级队列模型
│   ├── pipeline.py      # ContextPipeline — 收集 → 评分 → token 预算填充（async I/O）
│   ├── ingester.py      # ContextIngester — 写入 messages/ + timeline.json（索引唯一源）
│   ├── tokenizer.py     # 分词器 — TF-IDF / Jaccard / 时间衰减 / 同义词扩展 / _detect_language
│   ├── overview.py      # 会话总览 + read_current_overview + restore_overview_from_checkpoint
│   ├── relevance.py     # tokenizer + overview 的公开 API 重导出层
│   └── token_counter.py # 上下文 token 估算 + compute_context_usage + trim_conversation_to_window（爆满兜底）
├── search/              # 全局会话搜索
│   └── index.py         # SearchIndex — 纯内存索引，启动从 timeline rebuild（timeline 唯一源）
├── memory/              # 记忆管线 — ReflectEngine + 反馈闭环 + 自动提取
│   ├── reflector.py     # ReflectEngine — /reflect 入口，LLM 生成结构化记忆（含 frontmatter）
│   ├── auto.py          # AutoMemoryExtractor — /mem-auto 开关，每轮静默提取新条目（默认关）
│   ├── version.py       # VersionManager — 备份/版本日志/回滚（从 reflector 提取）
│   ├── entries.py       # MemoryEntry dataclass + parse_memory_file() + format_memory_entry() — 结构化记忆解析/回写
│   ├── recall.py        # 记忆召回 — 搜索 agent/*.md + 会话目录
│   └── feedback.py      # FeedbackStore（stable id 匹配）+ FeedbackVerifier（L1语言/L2长度）
├── commands/            # 命令系统 — 17 个内置命令
│   ├── __init__.py      # CommandRegistry + CommandDefinition + route_command()
│   └── builtin/         # handlers.py / settings_handlers.py / mcp_handlers.py / plugin_commands.py
├── plugins/             # 插件系统 v2 — Claude Code / OpenClaw / Aide 三格式兼容
│   ├── contract.py      # PluginManifest + PluginAPI + ContextProvider Protocol
│   ├── host.py          # PluginHost — 发现 + 三种加载路径 + 卸载 + 状态管理
│   ├── sdk.py           # define_plugin() 装饰器
│   ├── slots.py         # SlotRegistry — 插槽系统
│   ├── manifest_v2.py   # PluginManifestV2 — Claude Code plugin.json 解析 + 三格式检测
│   ├── adapter.py       # 三格式适配器 (ClaudeCode/OpenClaw/Aide) + PluginFormatDetector
│   ├── hook_runner.py   # HookRunner + MatcherCompiler (7 种匹配语法) + HookContext
│   ├── state.py         # PluginStateManager (READY/NEEDS_SETUP/DISABLED) + RequirementsChecker
│   ├── security.py      # PluginPreflightCheck — ClawScan 级安全预检 (5 项检查)
│   ├── watcher.py       # PluginWatcher — watchfiles 优先 + polling fallback + 500ms 防抖
│   └── templates/       # hello-plugin 模板
├── sessions/            # 会话管理 — manager.py（CRUD + 回滚 + 智能标题）+ restorer.py（从磁盘恢复）
├── tools/               # 9 个内置工具 + 声明式清单 + ToolContext DI
│   ├── definition.py    # ToolDefinition + ToolContext（叶子模块，避免循环导入）
│   ├── __init__.py      # ToolRegistry（含 ToolContext 注入 + 重试）
│   ├── discovery.py     # BUILTIN_TOOLS 声明式清单（收集各模块 definition）
│   ├── retry.py         # RetryConfig + ErrorClass + async_retry
│   ├── truncation.py    # 输出截断工具
│   ├── delegate.py      # 子 agent 委托工具（一次性、用完即删）
│   └── [read_file|write_file|run_shell|search_memory|web|search_in_files|search_chat|plugin_manager].py
├── mcp/                 # MCP 协议适配 — adapter/protocol/transport/fault/lifecycle/watcher
├── locale_data/         # 双语字符串（zh/en JSON）
├── locale.py            # t() 国际化 + build_soul + build_tools_prompt
└── platform.py          # OS 检测（IS_WINDOWS / IS_MACOS / IS_LINUX）

ui/
├── textual_app/
│   ├── app.py           # AideApp — 主应用
│   ├── bridge.py        # UIBridge — Kernel ↔ Textual 桥接（ExecutorUI 实现）
│   ├── command_handler.py  # 命令执行 + 确认流处理器
│   ├── session_context.py  # SessionContext — 当前会话运行时状态（dataclass）
│   ├── app.tcss         # 布局样式（暗色主题）
│   ├── platform.py      # UI 侧平台工具
│   ├── screens/         # home / onboarding / api_config
│   └── widgets/         # message_list（回合树管理器）/ tree_nodes（树节点组件）/ input_box / command_palette / status_bar

平台验证脚本：`core/verify_linux.sh` / `core/verify_macos.sh`
```

**配置路径**：`~/.aide/config/settings.json`

**资源路径**：`core/resources.py` 的 `get_resource_path()` 统一解析 dev/bundle 两种模式。不要用 `Path(__file__).parent`。

**已明确砍掉**：Planner、MessageHub、硬约束、Safe Mode、Idle Detection、正则截获引擎、显式/隐式信号区分、embedding 向量搜索。

## 关键架构模式

### Bootstrap 5-Phase 组合根

`AppBootstrap.init()` 拆为 5 个 private static method，每 phase 可独立测试：

```
Phase 1: _init_provider(config) → provider, model_name, errors
Phase 2: _init_tooling(config) → tool_registry, mcp_adapter, cmd_registry
Phase 3: _init_storage_and_context(config) → store, search_index, ingester, pipeline, feedback, reflector, session_mgr
Phase 4: _init_plugins(config, tool_registry, cmd_registry) → plugin_host + HookRunner (聚合所有插件 hooks)
Phase 5: _init_kernel(...) → kernel (注入 hook_runner 到 KernelContext + FC Loop)
```

Phase 4 内：`PluginHost.load()` → 安全预检（PluginPreflightCheck）→ 格式检测 → 适配器提取 → 注册 hooks/技能/命令。`ToolContext` 在 Phase 3 后注入（search_index/sessions_root/agent_root/provider/tool_registry），`hook_runner` 在 Phase 4 后补入 `tool_registry.tool_context`。

新增组件只需修改对应 phase，不影响其他。`reload_provider()` 保留为独立的轻量热重载路径。

### 声明式工具清单

每个工具模块自包含一份 `definition = ToolDefinition(name, description, parameters, execute)` 声明（含名称/功能/调用方式），`discovery.py` 的 `BUILTIN_TOOLS` 只负责收集注册。这是工具的**单一事实来源**——注册、`get_schemas()`、注入 LLM 上下文都从它派生。

- `ToolDefinition` / `ToolContext` 定义在 `definition.py`（叶子模块，避免工具模块 import 时与 `__init__.py` 循环）
- **新增工具** = 新模块里写 `execute` + `schema` + `definition`，再在 `BUILTIN_TOOLS` 加一行

### 工具层 DI（ToolContext）

工具不再通过模块级单例获取共享服务。`ToolRegistry.tool_context` 持有 `ToolContext`（search_index / sessions_root / agent_root / current_session_id / provider / tool_registry / hook_runner / plugin_host），`execute()` 时自动注入：

```python
# 工具签名兼容新旧：
async def execute(arguments: dict, ctx=None) -> str:
    index = ctx.search_index if ctx else None  # 优先注入，fallback 降级
```

`search_chat` 和 `search_memory` 通过 ctx 获取 SearchIndex。`recall()` 接受可选的 `search_index` 参数。`get_search_index()` 单例已移除。

### 子 agent 委托（delegate 工具）

`delegate`（`core/tools/delegate.py`）是第 8 个内置工具，实现"一次性子 agent"：主 agent 把子任务派给一个上下文独立的子 agent 跑受限 FC 循环，只返回压缩后的结论。

- **复用** `FunctionCallingLoop`：换一个空 `messages` 列表 + 过滤后的 `ToolRegistry` 跑一次，无需新框架
- **用完即删**：不建会话、不摄入 timeline、不进搜索索引（`messages` 是局部变量）
- **像工具、不展示**：子 agent 传 `NullUI`（`protocols.py`），全程静默
- **递归保护**：子 agent 的 `ToolRegistry` 根本不含 `delegate`（白名单注册天然禁止），无需深度标记
- **默认只读白名单**：`read_file / search_in_files / search_chat / search_memory / web`，主 agent 可参数 `tools` 覆盖
- **运行时依赖**：通过 `ToolContext.provider / tool_registry / hook_runner` 获取（`bootstrap.py` 注入；`set_provider()` 热重载时同步更新 provider）
- **超时**：`DELEGATE_TOOL_TIMEOUT = 180s`（`tool_executor.py`，比普通工具 30s 长）
- **并发限流**：`MAX_CONCURRENT_SUBAGENTS = 3` + 计数限流（`_active_subagents`），同时最多跑 3 个子 agent，超出的直接拒绝（返回错误，主 agent 自行决定重试或合并）
- **队列查询**：`action=status` 返回当前队列情况（上限/运行中/可用配额），主 agent 编排前先查、再派发（二次确认）
- **编排判据在 `tools.strategy_6`**（Tools Prompt 使用策略段落，`prompts.json`），而非 delegate 工具描述——工具描述是被动的，模型不会主动看；策略段落才是主 agent 的决策上下文。判据：可拆多独立子任务/需大量扫描→委派；单一小任务/强依赖/需看中间结果→直接做
- **SubagentStop hook**：子 agent 结束时触发，补全 9 事件里最后一个埋点

### plugin 管理工具

`plugin`（`core/tools/plugin_manager.py`）是第 9 个内置工具，让 LLM 在对话中自行管理插件：
- `action=list` — 列出已装插件 + 加载状态 + 插件目录
- `action=install` — 从**用户提供的本地目录或 zip** 复制到 `~/.aide/plugins` → 自动加载；zip 解压做路径逃逸校验；识别无效插件目录并报错
- `action=load|unload` — 管理加载状态
- 依赖 `ToolContext.plugin_host`（bootstrap Phase 4 后注入）。**不主动拉取任何来源**（install 的 path 由用户提供）；有副作用（写文件），按并发规则归入串行组，不加入子 agent 只读白名单

### Chat 中间件框架

`AgentKernel.chat()` 的 6 步管线（上下文组装 / FC 循环 / 摄入保存 / Token 计数 / 反馈验证）**固化在 chat() 内**，经 `MiddlewareRunner` 暴露 4 个 hook 点供插件扩展：

```
before_context → [上下文组装] → after_context →
before_fc_loop → [FC 循环] → after_fc_loop →
[摄入保存 / Token 计数 / 反馈验证 — 固化步骤，不走中间件]
```

`ChatMiddleware` Protocol 有 4 个可选 hook 方法（均为 no-op 可选）。插件通过 `kernel._runner.add(my_mw)` 注册行为扩展，`ChatContext.metadata` 在中间件间自由传递数据。**不内置框架级中间件**——框架义务直接内联在 chat()，中间件仅作扩展点。

### 记忆结构化（MemoryEntry）+ 统一 frontmatter 解析

记忆文件支持 YAML frontmatter 格式：

```markdown
---
id: pref_001
created: 2026-08-03
source: 20260803_120000/turn_5
---
- 用户喜欢简洁回复
```

- `parse_memory_file()` 解析 frontmatter + 内容，兼容无 frontmatter 的旧格式
- FeedbackStore 用 stable `id` 匹配（优先）或 content hash（fallback）
- ReflectEngine 的 LLM prompt 指导生成带 id/created/source 的结构化条目
- `/reflect` 时保留已有条目的 id，新增条目分配新 id
- **所有 frontmatter 解析统一委托给 `entries._parse_simple_frontmatter()`**（manifest_v2、contract、host.py 的 SkillProvider 均通过它解析）

### Provider 增强

`StreamEnd` 新增 `native_stop_reason`（保留 provider 原生停止原因）和 `usage`（token 用量）字段。`ThinkingDelta` 新增 `kind` 字段（"thinking"/"reasoning"/"chain_of_thought"）。

FC Loop 利用 `native_stop_reason == "max_tokens"` 实现智能续写：模型被截断时自动发 `(continue)` 让其继续输出，不消耗额外 max_turns。

## UI 布局

纯暗主题（`#0c0c0c`），全宽对话区无右侧栏。Esc 切换首页↔对话页。

**回合树展示**：每个 assistant 回合 = 一棵 `TurnTree`（`ui/textual_app/widgets/tree_nodes.py`），节点用 `●` 标记、颜色随类型变化：
think（灰，流式展开→结束折叠）/ tool（绿，精简单行，双击展开结果；工具错误 ● 变红）/ body（白，正文，流式实时 Markdown → 完成态一致）/ error（红）/ system（黄）。色板统一在 `tree_nodes.py` 顶部常量（`CONNECTOR_STYLE` / `BULLET_*`）。

- 树连接符统一"栈顶"规则：新节点一律 `└`，加入后自动降级为 `├`，无首节点特例；相邻节点类型不同时插入一行 `│` 引导线
- 正文实时 Markdown：首行与节点同行（`│ ● 正文…`）做**行内 Markdown**（`_render_inline_markdown()` 转加粗/代码/斜体/删除线/链接，复用 `markdown.*` 主题样式与正文一致，未闭合定界保持字面量）；换行后的正文 RichMarkdown 流式渲染，`append_chunk` 节流重解析（未换行逐 token；换行后 80ms，>32K 250ms，>128K 500ms），`finish()`/连接符变更强制立即渲染，后续行缩进到文本列（col 4）
- 节点交互：左键双击折叠/展开（可折叠节点），右键复制内容
- 列布局：`│`(col 0) → `●`(col 2) → 文本(col 4)；CSS 用 `.tree-node` / `.tree-guide` margin `0 0 0 9` 对齐
- **长单行竖线**：无显式换行的长正文在终端视觉 wrap 时，续行也要带 `│`（`_PrefixedLines(skip_first=True)`：首行保留 `├ ●`，wrap 续行补前缀），否则树左框断开
- 用户消息保留气泡框（`MessageWidget`），与树之间空一行

**sticky 钉顶锚点**（`message_list.py`）：长对话滚动时，**上方最近的消息顶滑出窗口顶即钉住**（固定头 `dock:top` 显示消息副本，流内消息 `display:none`），作为上下文锚点——**不依赖树几何**（多回合短树滚动到树间隙也能钉住）。滚动使下一消息顶滑出时锚点**跟随切换**；消息顶回到视口或消息 ≥ 一屏时释放。钉住的几何判定用 `virtual_region`，被钉消息 `display:none` 后其坐标会异常（勿在读它判定）。

## 会话存储结构

```
sessions/{YYYYMMDD_HHMMSS}/
├── meta.json           # 会话名称 + last_active_at + last_reflected_turn + reflected_at
├── timeline.json       # JSONL 轮次级索引（每行 {turn, timestamp, summary}）— 全局索引唯一源
├── overview.json       # 会话总览检查点 [{to_turn, compressed_at, overview_md}] — 当前生效版 = 最后一条
└── messages/           # 完整原文存档（turn_NNN.json）
```

`turn_NNN.json`：`{turn, timestamp, thinking, messages}`。
`messages` 是当轮增量消息（含带 `tool_calls` 的 assistant 与 `tool` 结果），
`thinking` 是本轮 LLM 思考内容（退出重进后恢复 think 节点用，不进 LLM 上下文）。
顶层不再冗余 `user`/`assistant`（旧格式读侧兼容）。

**索引/总览派生关系**（避免冗余写入与不一致）：
- `timeline.json` = 唯一索引源。`SearchIndex`（全局搜索）是纯内存缓存，
  启动时 `await rebuild()` 从所有 timeline 重建，运行时 add/remove 只改内存。
- `overview.json` = 会话总览唯一文件。读取统一走 `read_current_overview()`，
  旧 `overview.md` 仅作迁移兼容回退。回滚截断检查点即还原。

恢复链路：
- `restore_session_full()` → 一次读盘返回 `(conversation, turn_count, turns)`。
  app 层 `_restore_session` 用它，避免同一批 turn 文件读两遍。
- `restore_session()` → 扁平 conversation（LLM 上下文），保持原签名包装复用。
  **assistant 空 content 但带 `tool_calls` 必须保留**——否则其后 `tool` 消息失配，DeepSeek 会拒调。
- `restore_turns()` → 按轮结构化记录 `[{turn, thinking, messages}]`，供 UI 重建完整树（think/工具/正文）。

## 智能标题

不调用 LLM。规则引擎：截断到句尾标点 → 去 17 种前缀 → 限 20 字符。写入 `meta.json`。

## 上下文管线（优先级队列）

**收集 → 评分 → 打包**三阶段：

1. **收集**：Soul + Tools Prompt（pinned）+ 技能注入 + 记忆条目（结构化解析）+ overview + timeline + **相关早期轮次完整回填**（`type="history"`，最近 10 轮，评分/预算再筛）
2. **评分**（内容相关性为主序）：`relevance = content_corr × type_weight`（memory 1.0 / overview 0.8 / skill 0.7 / history 0.7 / timeline 0.5）决定**是否注入**；`score = relevance × recency × feedback` 决定注入顺序。overview/timeline 有基础注入分（`FRAGMENT_BASE_RELEVANCE`，会话背景不严格过滤）。记忆衰减按条目 `created`（frontmatter）而非文件 mtime，`long_term_memory.md` 不衰减。feedback 权重用 stable id 匹配
3. **打包**：pinned 置顶，按 score 降序（同分短片段优先）填满 token 预算（~40% context_window），低于相关性阈值的跳过
   - **memory 单来源上限**：`MEMORY_BUDGET_RATIO = 0.4`——相关记忆过多时不挤占工具/技能上下文（free-code 有界注入）
   - **新鲜度标注**：带 `created`（frontmatter）的记忆条目注入时前置 `（记忆日期：YYYY-MM-DD）`，让模型判断新旧；`content` 保持原始值（不破坏 FeedbackVerifier 的 content-hash 匹配），前缀在打包时拼

**上下文爆满兜底**（`token_counter.trim_conversation_to_window`）：估算 system+conv(+schema) 超 `context_window × 0.9` 时，从头部**逐轮丢弃最老轮次**；只剩 1 轮仍超 → 截断 assistant 正文（user 保留）。纯机械降级、不调 LLM（摘要归 /reflect 管），避免 400 prompt-too-long。在 `agent._assemble_context` 组装后调用，预算充足时幂等不动。

词汇索引（`_build_vocabulary`）从 agent/*.md **+ 会话 timeline 摘要**构建（缓解冷启动分词退化）。所有文件 I/O 通过 `asyncio.to_thread()` 执行，不阻塞事件循环。

## 反馈闭环

chat() 每轮末尾运行 FeedbackVerifier（纯规则，<10ms，不调 LLM）：

1. **提取约束**：从注入的记忆片段识别可验证规则
2. **L1 语言检测**：CJK 字符占比 > 30% → 期望中文，否则英文
3. **L2 长度/风格**："简洁"检查 ≤300 tokens，"详细"检查 ≥100 tokens
4. **权重调节**：偏离 weight *= 1.5，遵循 weight *= 0.95
5. **持久化**：`agent/.feedback.json`。Pipeline 评分时按 stable id 匹配叠加。

FeedbackStore 优先用 `entry_id`（来自 frontmatter）做 key，fallback 到 sha1 content hash。

## Prompt 演化

单条管线：对话自然积累（不截获）→ 用户手动 `/reflect` → LLM 更新结构化记忆 + 会话总览 → 用户审查 diff → 确认写入。

系统绝不自动调用 LLM 更新 prompt。三个维度不扩展：偏好 / 工作流 / 长记忆。

### 自动记忆提取（`AutoMemoryExtractor`，默认关）

"绝不自动调 LLM"的**显式豁免开关**：`/mem-auto on|off|status`（settings.json `app.auto_memory`，默认 False）。开启后每轮 `chat()` 末尾 **fire-and-forget** 后台提取：

- **写入范围只限三个可变 prompt**（preferences/workflows/long_term_memory），不写 overview（摘要归 /reflect 管）、不建新维度
- **直接追加**：LLM 只输出新增条目（`## section` + `- 内容`），解析后生成 `MemoryEntry`（id 递增不冲突、created=今天、source=`{session_id}/turn_N`），`format_memory_entry` 格式化后原子追加；写入前 `_backup_prompt` 备份，模块级 `asyncio.Lock` 保护读-改-写
- **去重**：注入现有记忆清单，跳过已有 content；**/reflect 互斥**：`meta.json last_reflected_turn >= turn` 跳过
- **静默失败**：LLM 异常/开关 off/无回复 都返回 False 不阻塞；成功写后更新 `last_auto_memory_turn` marker
- 挂载点：bootstrap Phase 3 构造 → KernelContext `MemoryContext.auto_memory` → `agent.chat()` 末尾 `asyncio.create_task(...)`；`set_provider()` 同步 provider

## Kernel/UI 分离

- **AgentKernel**（`core/kernel/agent.py`）— 零 UI 依赖，通过 `ExecutorUI` Protocol 回调 UI
- **UIBridge**（`ui/textual_app/bridge.py`）— 实现 `ExecutorUI` Protocol

## 插件系统 v2

**三格式兼容**：Claude Code (`.claude-plugin/plugin.json`) / OpenClaw (`SKILL.md`) / Aide native (`aide.plugin.json`)。社区插件放入 `~/.aide/plugins/` 即可自动识别加载。

**FormatDetector 优先级**：`.claude-plugin/plugin.json` > `SKILL.md`（含 name: frontmatter）> `aide.plugin.json`

**三种加载路径**（`host.py:load()`）：
1. **外部技能**（Claude Code / OpenClaw）→ `_load_external_skill()` — 适配器提取 skills → 注册 `//plugin:skill` 命令 + `skill_{plugin}_{skill}` 工具 + ExternalSkillProvider
2. **Aide native** → `_load_python_plugin()` — exec_module + register(api) + 注册 tools/commands/slots
3. **Fallback**（旧格式、根目录 SKILL.md）→ Python 路径（`_load_python_plugin()`）

**安全预检**（`security.py`）：`load()` 时强制运行 `PluginPreflightCheck`（5 项检查：install 脚本白名单、HTTPS-only URL、POSIX 世界可写文件、JVM/glibc/.NET 注入检测、敏感路径访问）。blocked=True 拒绝加载。

**状态管理**（`state.py`）：READY / NEEDS_SETUP / DISABLED 三态，持久化到 `~/.aide/config/plugin_states.json`。DISABLED 插件**加载前即拦截**（工具/命令不注册），`disable` 会真正卸载；`enable` 重新加载。

**统一命令 `/plugins`**（原 `/plugin` 与 `/plugins` 合并，`plugin_commands.py`）：无参数 = 加载全部 + 三态状态面板（含插件目录）；`/plugins load|unload|reload <id>` 管理加载；`/plugins enable|disable <id>` 开关。非子命令参数（如 `list`/`discover`）按刷新处理。

**`command` 字段**（SKILL.md frontmatter 或 `aide.plugin.json`）：CLI 型技能声明对应可执行命令（如 `agent-browser`）。声明后 `//plugin:skill` 与 skill 工具输出附"可执行命令：`<command>`（用 run_shell 调用）"提示——**提示型不直通执行**，由 LLM 用 `run_shell` 实际运行。详见 `docs/plugins.md`。

**热重载**（`watcher.py`）：watchfiles 优先 + 2s polling fallback，500ms 防抖。按变更文件类型精确重载。

**命名空间隔离**：
- 命令：`//plugin-id:skill-name`（Python 插件 `//plugin-id:cmd-name`）
- 工具：`skill_{plugin_id}_{skill_name}`
- ContextProvider 键：原生技能 `plugin_id`，外部技能 `ns_skill_name`

### 生命周期 Hook 系统（9 事件全部接线）

`HookRunner`（`hook_runner.py`）支持 9 种事件 + 7 种匹配语法 + 环境变量注入 + JSON 输出：

| 事件 | 触发位置 | 说明 |
|------|---------|------|
| SessionStart | `bootstrap.py` | 所有插件加载完毕 |
| UserPromptSubmit | `agent.py` → chat() 入口 | 用户提交消息 |
| **PermissionRequest** | `tool_executor.py` → `_should_block()` | 高危操作审批（exit 2=阻止） |
| PreToolUse | `tools/__init__.py` → `execute()` | 工具执行前（exit 2=阻止，返回钩子被封锁） |
| PostToolUse | `tools/__init__.py` → `execute()` | 工具执行后 |
| **PreCompact** | `agent.py` → 上下文组装前 | 插件可预处理压缩 |
| Stop | `agent.py` → chat() 返回前 | 对话结束 |
| **Notification** | `agent.py` → chat() 返回前 | 通用系统通知 |
| SubagentStop | `delegate.py` → 子 agent 结束时 | 子 agent 完成委派任务 |

**7 种 matcher 语法**：Exact / Any / Prefix / Regex / ExtMatcher（文件扩展名）/ KeyValMatcher（参数匹配 `run_shell(command=rm *)`）/ OrMatcher（管道）

**环境变量注入**（对标 Claude Code）：`TOOL_NAME`/`CLAUDE_TOOL_NAME`、`TOOL_ARGS`/`CLAUDE_TOOL_ARGS`、`FILE_PATH`、`SESSION_ID`、`TURN`、`USER_PROMPT`/`CLAUDE_USER_PROMPT`、`PLUGIN_NAME`、`PROJECT_DIR`

### PluginAPI

Python 插件可注册：工具、命令、生命周期钩子（`register_hook()`）、依赖声明（`requires()`）、ChatMiddleware

## MCP 适配

- 工具命名规则：`mcp_{server}_{tool}`
- 配置：`~/.aide/mcp/servers.json`
- 传输：StdioTransport + HTTPTransport
- 故障恢复：CircuitBreaker（阈值 3）+ 自动重连

## 多模态消息格式

```python
# 纯文本
{"role": "user", "content": "hello"}
# 多模态（图片输入时自动升级）
{"role": "user", "content": [{"type": "text", "text": "..."}, {"type": "image_url", ...}]}
```

非视觉模型调用前 `_sanitize_messages()` 将图片替换为 `[图片]` 占位。

## 工具可靠性

- **RetryConfig**：max_retries、指数退避、backoff_factor
- **ErrorClass**：TRANSIENT（重试）/ PERMANENT（立即返回）/ UNKNOWN（保守重试 1 次）
- ToolRegistry.execute() 内置重试 + ToolContext 自动注入

### 工具并发分级（`tool_executor.py` ToolExecutor.execute_tools）

同轮工具调用分两组执行（free-code `isConcurrencySafe` 分片的简化版）：
- **串行组**：有副作用工具（`_uncacheable_tools` = write_file/run_shell）+ MCP 工具——依次执行，消除同轮多写竞态
- **并发组**：其余（只读类 + 插件工具）——并行；**任一失败 → 取消其余兄弟**（超时/异常/高危阻止/网络限流算失败，`ToolExecutor._run_one` 返回 `(ok, result)`），被取消者标记"已取消"喂回 LLM
- 结果按原 tool_calls 顺序重组（`zip(final.tool_calls, tool_results)` 依赖顺序）
- 注意：`ToolRegistry.execute` 经 `async_retry` 把工具异常**吞成错误字符串**返回（不向上抛），所以"失败"指超时/高危阻止等 `ToolExecutor._run_one` 层判定，非 execute 抛异常

### run_shell 超时（进程树 kill）

`run_shell` 用 `asyncio.create_subprocess_shell`（原生异步子进程，非 `to_thread`），超时**kill 进程树**：
- Windows：`taskkill /T /F` + `CREATE_NEW_PROCESS_GROUP`；POSIX：`killpg` + `start_new_session`
- `timeout` 参数（1~60s）真正生效——外层 `ToolExecutor` 对 run_shell 的超时 = 内部 timeout + 2s 缓冲（`_run_shell_tool_timeout`），保证内部先超时 kill，外层只兜底（避免 wait_for 取消掐掉 kill 逻辑导致进程后台残留）

## CI/CD

[`.github/workflows/build.yml`](.github/workflows/build.yml) — tag `v*` 触发三平台构建：
`test`（pytest）→ `build`（PyInstaller）→ `release`（GitHub Release）

## 工程阶段

| Phase | 关键交付 |
|-------|---------|
| **P0-P4** | Textual UI + LLM Gateway + 内置工具 + FC 循环 + 上下文 + 插件系统 + PyInstaller |
| **P5** | 记忆系统重构（ReflectEngine + 反馈闭环）、优先级队列上下文、BLOCKED 拦截 |
| **P6** | 架构优化：ToolContext DI / Chat 中间件 / 记忆结构化 / Provider 增强 / Bootstrap 拆分 |
| **P7** | 插件系统 v2：三格式兼容 / 9 事件 Hook 系统 / 安全预检 / 状态管理 / 热重载 / 死代码清理 |
| **P8** | 子 agent delegate 工具 / 声明式工具清单（definition.py）/ 编排判据（strategy_6 + subagent_system 完整性） |
| **P8+ 优化批次** | 工具并发分级（只读并行/写串行/abort 兄弟）、记忆注入边界+新鲜度、自动记忆提取（/mem-auto）、上下文爆满兜底（trim_conversation_to_window） |

1765 测试全部通过。

## Prompt 体系

- **Soul**（`core/locale.py:build_soul()`）— locale JSON 驱动，`{name}` 由冷启动向导替换
- **Tools Prompt**（`core/locale.py:build_tools_prompt()`）— 从 ToolRegistry 动态生成
- **Pipeline 注入**：Soul（pinned）→ Tools Prompt（pinned）→ 技能上下文 → 记忆片段（scored）→ overview

## Textual 要点

- `Static._render()` 是框架方法不可覆盖。用自定义方法名
- `@on(MessageClass)` 处理非 widget 嵌套 Message
- `push_screen` / `pop_screen` 管理屏幕栈
- `@work(exclusive=True, thread=False)` 用于异步 worker
- pystray 跨线程用 `call_from_thread()`
- CSS：`width: 1fr` 撑满容器；`content-align: center middle` 居中内容块
- Pydantic 配置用属性访问（`settings.llm.model`）不是 dict

## 已知陷阱

- **`query_one` 第二个参数必须是类，不能是字符串**。`MessageList` 需运行时导入
- **XML fallback**：DeepSeek 等模型在 content 中输出 `<invoke>`，`xml_tool_parser.py` 提供解析
- **System 消息不存对话历史**。`agent.py` 合并时过滤 `role != "system"`
- **DeepSeek 严格校验**：tool 消息必须有 `tool_call_id`，assistant 消息保留 `tool_calls` 字段
- **非视觉模型消息清洗**：`_sanitize_messages()` 将多模态 content 转纯文本
- **Textual TextArea Ctrl+Z/Y 已禁用**：光标位置与文档行数不同步会崩溃
- **Anthropic Provider**：内部完成 content blocks ↔ OpenAI 格式转换，FC 循环无感知
- **JSONL 读写**：`storage.py` 提供 `read_jsonl()` / `append_jsonl()` / `overwrite_jsonl()`
- **`tests/ui/__init__.py`**：不要创建此文件——会遮蔽顶层 `ui` 包
- **旧路径**：`_tokenizer.py` → `tokenizer.py`，`_overview.py` → `overview.py`，`ensure_session` → `set_session`
- **`_search_embeddings.npy`**：已移除，搜索改用 bigram Jaccard
- **`has_embeddings`**：已删除（死代码）
- **`get_search_index()`**：已移除，统一通过 ToolContext DI 注入
- **frontmatter 解析**：统一入口 `entries._parse_simple_frontmatter()`，不要新建解析器
- **`current_session_id`**：在 `agent.py` chat() 每轮开始时同步到 `ToolContext`（hook 环境变量用）
- **`ContextIngester._sessions_root`**：使用 `aide_dir()` 而非 `Path.home()/.aide`（兼容 AIDE_HOME）
- **`plugin_states.json`**：插件状态持久化文件，DISABLED 状态在 reload 时保留
- **Claude Code 插件**：命令/MCP/settings 已提取但 MCP/settings 仅记录日志，需手动配置
- **Textual Vertical 默认 `height: 1fr` + `overflow: hidden`**：滚动容器（VerticalScroll）内的树/卡片容器必须设 `height: auto`，否则内容超出视口即被裁剪、滚动条永远不可滚（`.turn-tree` 即因此加 `height: auto`）
