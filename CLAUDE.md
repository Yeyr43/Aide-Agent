# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

Aide Agent — 本地个人智能管家。核心不是"能做多少事"而是"越用越懂你"。演化不靠能力积累，靠动态 prompt。

**关键原则**：用户可控、本地隐私、边界清晰、渐进演化。所有数据本地存储，备份即复制文件夹。

设计背景和完整推导见 [CONTEXT.md](CONTEXT.md) — 包含设计演变、批判性收敛记录、已移除功能及原因。

## 常用命令

```bash
# 运行应用
uv run python shell/main.py

# 运行全部测试（1045 个）
uv run pytest tests/ -q

# 运行单个测试文件
uv run pytest tests/test_config.py -q

# 运行单个测试函数
uv run pytest tests/test_commands.py::test_route_command -q

# 依赖安装
uv sync

# 构建独立分发包
uv run python scripts/build.py       # 完整构建（下载模型 + PyInstaller）
uv run python scripts/build.py --no-model  # 跳过模型下载
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
bash scripts/verify_linux.sh   # Linux
bash scripts/verify_macos.sh   # macOS
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
├── kernel/              # Agent 内核（零 UI 依赖）
│   ├── bootstrap.py     # AppBootstrap — 5-phase 组合根（_init_provider / _init_tooling / _init_storage_and_context / _init_plugins / _init_kernel）
│   ├── context.py       # KernelContext — 依赖聚合（Memory/Tooling/Session 三个子 context）
│   ├── agent.py         # AgentKernel 门面 — chat() 通过 MiddlewareRunner 编排 6 步管线
│   ├── fc_loop.py       # Function Calling 循环（max_turns=10，XML fallback，smart continuation）
│   ├── middleware.py    # ChatMiddleware Protocol + ChatContext + MiddlewareRunner
│   ├── safety.py        # check_tool_safety() — 高危命令拦截（从 fc_loop 提取）
│   ├── xml_tool_parser.py  # extract_xml_tool_calls() — XML fallback 解析（从 fc_loop 提取）
│   ├── protocols.py     # ExecutorUI Protocol + NullUI + ChatResult + TokenUsage
│   └── state.py         # ExecutorState 状态机（READY / BLOCKED）
├── llm_gateway/         # 4 个 LLM Provider
│   ├── provider.py      # AbstractProvider Protocol + StreamEvent 类型（TextDelta/ThinkingDelta/StreamEnd）
│   ├── openai_compatible_provider.py  # OpenAI 兼容协议基类
│   ├── anthropic_provider.py   # Anthropic Messages API（原生协议适配）
│   ├── tool_call_builder.py    # SSE delta 累积
│   ├── image_utils.py   # 剪贴板图片、base64 编码
│   └── content_builder.py  # 多模态 content 构建
├── context/             # 上下文管线 — 优先级队列模型
│   ├── pipeline.py      # ContextPipeline — 收集 → 评分 → token 预算填充（async I/O）
│   ├── ingester.py      # ContextIngester — 写入 messages/ + timeline.json + search index
│   ├── tokenizer.py     # 分词器 — TF-IDF / Jaccard / 时间衰减 / 同义词扩展 / _detect_language
│   ├── overview.py      # 会话总览 + parse_overview_md + restore_overview_from_checkpoint
│   ├── relevance.py     # tokenizer + overview 的公开 API 重导出层
│   └── token_counter.py # 上下文 token 估算 + compute_context_usage
├── search/              # 全局会话搜索
│   └── index.py         # SearchIndex — bigram Jaccard 关键词匹配（INDEX 格式）
├── memory/              # 记忆管线 — ReflectEngine + 反馈闭环
│   ├── reflector.py     # ReflectEngine — /reflect 入口，LLM 生成结构化记忆（含 frontmatter）
│   ├── version.py       # VersionManager — 备份/版本日志/回滚（从 reflector 提取）
│   ├── entries.py       # MemoryEntry dataclass + parse_memory_file() — 结构化记忆解析
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
├── sessions/            # 会话管理 — manager.py（CRUD + 回滚 + 智能标题）
├── tools/               # 8 个内置工具 + 声明式清单 + ToolContext DI
│   ├── definition.py    # ToolDefinition + ToolContext（叶子模块，避免循环导入）
│   ├── __init__.py      # ToolRegistry（含 ToolContext 注入 + 重试）
│   ├── discovery.py     # BUILTIN_TOOLS 声明式清单（收集各模块 definition）
│   ├── retry.py         # RetryConfig + ErrorClass + async_retry
│   ├── truncation.py    # 输出截断工具
│   ├── delegate.py      # 子 agent 委托工具（一次性、用完即删）
│   └── [read_file|write_file|run_shell|search_memory|web|search_in_files|search_chat].py
├── mcp/                 # MCP 协议适配 — adapter/protocol/transport/fault/lifecycle/watcher
├── locale_data/         # 双语字符串（zh/en JSON）
├── locale.py            # t() 国际化 + build_soul + build_tools_prompt
└── platform.py          # OS 检测（IS_WINDOWS / IS_MACOS / IS_LINUX）

ui/
├── textual_app/
│   ├── app.py           # AideApp — 主应用
│   ├── bridge.py        # UIBridge — Kernel ↔ Textual 桥接
│   ├── command_handler.py  # 命令执行 + 确认流处理器
│   ├── screens/         # home / onboarding / api_config
│   └── widgets/         # message_list / input_box / command_palette / status_bar

shell/
├── main.py              # 应用入口 + 烟雾测试
└── tray_daemon.py       # 系统托盘后台守护进程
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

工具不再通过模块级单例获取共享服务。`ToolRegistry.tool_context` 持有 `ToolContext`（search_index / sessions_root / agent_root / current_session_id / provider / tool_registry / hook_runner），`execute()` 时自动注入：

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
- **超时**：`DELEGATE_TOOL_TIMEOUT = 180s`（`fc_loop.py`，比普通工具 30s 长）
- **并发限流**：`MAX_CONCURRENT_SUBAGENTS = 3` + 计数限流（`_active_subagents`），同时最多跑 3 个子 agent，超出的直接拒绝（返回错误，主 agent 自行决定重试或合并）
- **队列查询**：`action=status` 返回当前队列情况（上限/运行中/可用配额），主 agent 编排前先查、再派发（二次确认）
- **编排判据在 `tools.strategy_6`**（Tools Prompt 使用策略段落，`prompts.json`），而非 delegate 工具描述——工具描述是被动的，模型不会主动看；策略段落才是主 agent 的决策上下文。判据：可拆多独立子任务/需大量扫描→委派；单一小任务/强依赖/需看中间结果→直接做
- **SubagentStop hook**：子 agent 结束时触发，补全 9 事件里最后一个埋点

### Chat 中间件框架

`AgentKernel.chat()` 不再硬编码 6 步流程。通过 `MiddlewareRunner` 编排 4 个 hook 点：

```
before_context → [上下文组装] → after_context →
before_fc_loop → [FC 循环] → after_fc_loop →
[摄入保存 / Token 计数 / 反馈验证 — 固化步骤，不走中间件]
```

`ChatMiddleware` Protocol 有 4 个可选 hook 方法。插件可通过 `kernel._runner.add(my_mw)` 注册行为扩展。`ChatContext.metadata` 在中间件间自由传递数据。

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

纯暗主题（`#0c0c0c`），全宽对话区无右侧栏。Esc 切换首页↔对话页。不显示工具调用过程（`on_tool_start/done` 为 `pass`），仅 `on_tool_error` 显示错误。

## 会话存储结构

```
sessions/{YYYYMMDD_HHMMSS}/
├── meta.json           # 会话名称 + last_active_at + last_reflected_turn + reflected_at
├── timeline.json       # JSONL 轮次级索引（每行一个 JSON 对象）
├── overview.md         # /reflect 生成的 LLM 会话总览（注入上下文）
├── overview.json       # 反思检查点日志（去重，回滚时还原 overview.md）
├── _search_index.json  # 全局搜索索引（JSONL 格式，bigram Jaccard 匹配）
└── messages/           # 完整原文存档（turn_NNN.json）
```

## 智能标题

不调用 LLM。规则引擎：截断到句尾标点 → 去 17 种前缀 → 限 20 字符。写入 `meta.json`。

## 上下文管线（优先级队列）

**收集 → 评分 → 打包**三阶段：

1. **收集**：Soul + Tools Prompt（pinned）+ 技能注入 + 记忆条目（结构化解析）+ overview + timeline
2. **评分**：TF-IDF/Jaccard + 时间衰减（30 天半衰期）。pinned 项 score=1.0。记忆条目叠加 feedback 权重（用 stable id 匹配）
3. **打包**：pinned 置顶，scored 按分数降序，填满 token 预算（~40% context_window）后截断

所有文件 I/O 通过 `asyncio.to_thread()` 执行，不阻塞事件循环。

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

## Kernel/UI 分离

- **AgentKernel**（`core/kernel/agent.py`）— 零 UI 依赖，通过 `ExecutorUI` Protocol 回调 UI
- **UIBridge**（`ui/textual_app/bridge.py`）— 实现 `ExecutorUI` Protocol

## 插件系统 v2

**三格式兼容**：Claude Code (`.claude-plugin/plugin.json`) / OpenClaw (`SKILL.md`) / Aide native (`aide.plugin.json`)。社区插件放入 `~/.aide/plugins/` 即可自动识别加载。

**FormatDetector 优先级**：`.claude-plugin/plugin.json` > `SKILL.md`（含 name: frontmatter）> `aide.plugin.json`

**三种加载路径**（`host.py:load()`）：
1. **外部技能**（Claude Code / OpenClaw）→ `_load_external_skill()` — 适配器提取 skills → 注册 `//plugin:skill` 命令 + `skill_{plugin}_{skill}` 工具 + ContextProvider
2. **Aide native** → `_load_python_plugin()` — exec_module + register(api) + 注册 tools/commands/slots
3. **Fallback**（旧格式、根目录 SKILL.md）→ `_load_skill()` 或 Python 路径

**安全预检**（`security.py`）：`load()` 时强制运行 `PluginPreflightCheck`（5 项检查：install 脚本白名单、HTTPS-only URL、POSIX 世界可写文件、JVM/glibc/.NET 注入检测、敏感路径访问）。blocked=True 拒绝加载。

**状态管理**（`state.py`）：READY / NEEDS_SETUP / DISABLED 三态，持久化到 `~/.aide/config/plugin_states.json`。DISABLED 状态在 reload 时保留不覆盖。`/plugins` 命令显示状态面板。

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
| **PermissionRequest** | `fc_loop.py` → `_should_block()` | 高危操作审批（exit 2=阻止） |
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

1045 测试全部通过。

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
