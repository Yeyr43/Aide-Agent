# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

Aide Agent — 本地个人智能管家。核心不是"能做多少事"而是"越用越懂你"。演化不靠能力积累，靠动态 prompt。

**关键原则**：用户可控、本地隐私、边界清晰、渐进演化。所有数据本地存储，备份即复制文件夹。

## 常用命令

```bash
# 运行应用
uv run python shell/main.py

# 运行全部测试（659 个）
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
# 完整安装（含 pystray PyObjC 后端）
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

```bash
# 系统包 (Debian/Ubuntu 示例)
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-appindicator3-0.1 xclip

# Python 依赖
uv sync

# Wayland 用户额外需要 wl-clipboard (替代 xclip)
sudo apt install wl-clipboard
```

`pyperclip`（剪贴板工具）在 Linux 上需要 `xclip`（X11）或 `wl-clipboard`（Wayland）。
`PIL.ImageGrab`（剪贴板图片）在 Linux 上需要 X11 session，headless 环境自动降级返回 None。

### 平台验证

安装后运行对应平台的验证脚本确认环境正确：

```bash
# Linux
bash scripts/verify_linux.sh

# macOS
bash scripts/verify_macos.sh
```

## 技术栈

Python 3.13+ + Textual 0.80+ + pystray + asyncio + JSON 文件系统

- **Textual**：全栈纯 Python TUI，对标 Claude Code / Hermes 终端体验
- **pystray**：系统托盘图标和后台常驻
- **asyncio**：统一并发模型
- **JSON 文件系统**：零依赖存储，Write-Actor 并发模型（tempfile + os.replace）
- **Pygments**：代码语法高亮（P3 加入）

## 核心架构

```
core/
├── setup.py           # ~/.aide/ 目录初始化 + 冷启动判断 + 旧配置迁移
├── config.py            # Config dataclass — 分层加载 (cli > env > settings.json > defaults)
├── storage.py           # JSON 读写 + Write-Actor
├── resources.py         # is_bundled() / get_resource_path() — dev/bundle 双模式路径解析
├── kernel/              # Agent 内核（零 UI 依赖）
│   ├── bootstrap.py     # AppBootstrap — 应用组合根，创建所有组件并注入
│   ├── context.py        # KernelContext — 依赖聚合 dataclass（替代多参数构造函数）
│   ├── agent.py         # AgentKernel 门面 — 编排子组件
│   ├── fc_loop.py       # Function Calling 循环（max_turns=5，XML fallback）
│   ├── protocols.py     # ExecutorUI Protocol + ChatResult + TokenUsage
│   └── state.py         # ExecutorState 状态机
├── llm_gateway/         # OpenAI + Ollama 适配器 + 图片工具
│   ├── provider.py      # 流解析器 — SSE + tool_calls
│   ├── openai_provider.py  # OpenAI Chat Completions API
│   ├── ollama_provider.py  # Ollama 本地模型
│   ├── image_utils.py   # 剪贴板图片、base64 编码、save_images_to_session
│   └── content_builder.py  # 多模态 content 构建（文本+图片→OpenAI 数组）
├── context/             # 上下文管线
│   ├── pipeline.py      # ContextPipeline — 五层上下文组装
│   ├── ingester.py      # ContextIngester — 写入 cache.json / timeline.json
│   ├── compactor.py     # ContextCompactor — /compress LLM 摘要
│   ├── relevance.py     # bigram Jaccard 相关性过滤
│   └── token_counter.py # 上下文 token 估算 + compute_context_usage
├── memory/              # 记忆管线（原 prompt_manager）
│   ├── capture.py       # CaptureEngine — 规则引擎截获
│   ├── entries.py       # EntryManager — 条目目录（pending/confirmed）
│   ├── updater.py       # PromptUpdater — /profile update LLM 回溯整合
│   ├── tracker.py       # TopicFrequencyTracker — 话题频率统计
│   └── recall.py        # 记忆召回 — bigram Jaccard 相关性匹配
├── commands/            # 命令系统
│   ├── __init__.py        # CommandRegistry — 统一路由
│   └── builtin/
│       ├── handlers.py  # 11 个核心命令（help/profile/compact/export/import/session/memory/tools/update/clear/rollback）
│       ├── settings_handlers.py  # 3 个配置命令（api/model/language）
│       ├── mcp_handlers.py       # /mcp 指令
│       ├── plugin_commands.py    # /plugin 指令
│       └── _compat.py   # 向后兼容 COMMANDS dict + route_command()
├── plugins/             # 插件系统
│   ├── contract.py      # PluginContract + ToolDefinition 协议
│   ├── host.py          # PluginHost — 加载/卸载/热重载
│   ├── sdk.py           # PluginSDK — 插件开发 API
│   ├── slots.py         # SlotRegistry — 插槽注册（生命周期回调）
│   └── templates/       # 示例插件模板
├── sessions/            # 会话管理
│   ├── manager.py       # SessionManager — 创建/列表/删除
│   └── restorer.py      # 从磁盘恢复 conversation（新旧格式兼容）
├── tools/               # 工具层
│   ├── __init__.py      # ToolDefinition + ToolRegistry（含重试）
│   ├── retry.py         # RetryConfig + ErrorClass + async_retry
│   ├── discovery.py     # register_builtin_tools + register_plugin_tools
│   ├── builtin/         # 10 个内置工具
│   │   ├── read_file.py
│   │   ├── write_file.py
│   │   ├── edit_file.py
│   │   ├── run_shell.py
│   │   ├── search_memory.py
│   │   ├── web_search.py
│   │   ├── web_fetch.py
│   │   ├── list_dir.py
│   │   ├── search_in_files.py
│   │   └── clipboard.py
│   └── mcp/             # MCP 适配
│       ├── adapter.py   # MCPAdapter + MCPServerConfig — 工具发现/执行
│       ├── protocol.py  # JSON-RPC 2.0 消息类型 + MCP 握手
│       ├── transport.py # StdioTransport + HTTPTransport + create_transport 工厂
│       ├── fault.py     # MCP 故障注入 + 重连策略
│       └── watcher.py   # MCP 服务端进程监控

ui/
├── textual_app/
│   ├── app.py           # AideApp — 主应用
│   ├── bridge.py        # UIBridge — Kernel ↔ Textual 桥接
│   ├── screens/
│   │   ├── home.py      # 首页 — 大标题 + 会话列表 + 输入框
│   │   └── onboarding.py # 冷启动向导
│   ├── widgets/
│   │   ├── message_list.py # 消息流 — 等宽边框 Panel、Markdown + Pygments
│   │   ├── input_box.py    # 输入框 — Enter 发送、/ 弹命令面板
│   │   ├── command_palette.py # 命令建议面板
│   │   └── status_bar.py   # Token 可视化条 + 模型名
│   ├── command_handler.py  # 命令执行 + 确认流处理器
│   └── tray/            # pystray 系统托盘

shell/main.py            # 入口
```

**配置路径**：`~/.aide/config/settings.json`（原 `~/.aide/agent/config.json` 已迁移）

**资源路径**：`core/resources.py` 提供 `get_resource_path()` 统一解析 dev/bundle 两种模式的路径。新代码定位打包资源文件时必须用它，不要用 `Path(__file__).parent`。

**已明确砍掉**：Planner（FC 循环替代）、MessageHub、硬约束（纯 Soul 软引导）、Safe Mode（/export /import 替代）、Idle Detection。

## UI 布局

```
┌──────────────────────────────────────────────┐
│  Python 项目                                 │  ← 会话名（左上角）
├──────────────────────────────────────────────┤
│  ┌──────────────────────────────────────┐    │
│  │             帮我写个脚本 （右对齐）    │    │  ← 用户消息，等宽边框
│  └──────────────────────────────────────┘    │
│  ┌──────────────────────────────────────┐    │
│  │  好的，这是...（无名字）              │    │  ← AI 回复，等宽边框无标题
│  └──────────────────────────────────────┘    │
├──────────────────────────────────────────────┤
│  [输入框]   / → 弹出命令面板                  │
├──────────────────────────────────────────────┤
│  [████░░░░] 52%              Model: gpt-4o   │  ← 状态栏
└──────────────────────────────────────────────┘
```

- **纯暗主题**：`#0c0c0c`（PowerShell 黑），不做亮色切换
- **全宽对话区**：无右侧栏
- **不显示工具调用**：`on_tool_start/done` 为 `pass`，但 `on_tool_error` 会显示错误
- **Esc**：对话页 ↔ 首页双向切换
- **首页**：`█` 块字符拼成 AIDE AGENT 大标题，输入框直接发消息（自动命名）

## 会话存储结构

```
sessions/{YYYYMMDD_HHMMSS}/
├── meta.json           # 会话名称（智能标题自动生成）
├── timeline.json       # 轮次级索引 + 一句话事件概览
├── overview.md         # /compress 手动压缩的 LLM 摘要（注入上下文）
├── overview.json       # 压缩检查点日志（回滚时还原 overview.md）
├── cache.json          # 窗口上下文（每轮增量）
└── messages/           # 完整原文存档（turn_NNN.json）
```

## 智能标题

不调用 LLM。规则引擎三步提取：
1. 截断到第一个句尾标点（`。！？\n`）
2. 去掉 17 种常见前缀（帮我、请、怎么、我想…）
3. 限制 20 字符

标题在会话创建时写入 `meta.json`，首页加载时优先读取。

## 六层上下文

1. **Soul**（agent/soul.md）— 人设 + 行为准则（用户可编辑，模板含 {name} 占位符）
2. **Tools Prompt**（不可变常量 `core/setup.py` 的 TOOLS_PROMPT）— 工具列表 + 使用策略 + 失败降级
3. **技能上下文**（插件 SkillProvider）— 技能 Markdown 内容按需注入
4. **动态 prompt**（agent/*.md）— 偏好/工作流/长记忆，bigram Jaccard 相关性过滤
5. **会话总览**（overview.md，`/compress` 生成）— 注入 LLM 上下文
6. **窗口上下文**（cache.json 摘要 + 近 8 轮原文）— 最近信息 + 早期轮次压缩总览

## Prompt 演化

两条管线互不阻塞：
- **上下文管线**（实时）：每轮 → cache.json + timeline.json → `/compress` → overview.json
- **Prompt 管线**（用户主导）：每轮 → 规则引擎截获 → 条目目录（pending）→ `/profile update` → LLM 回溯整合

系统绝不自动调用 LLM 更新 prompt。三个维度不扩展：偏好 / 工作流 / 长记忆。

## Kernel/UI 分离

- **AgentKernel**（`core/kernel/agent.py`）— 零 UI 依赖，编排 LLM/session/context/plugins。通过 `ExecutorUI` Protocol 回调 UI 层
- **UIBridge**（`ui/textual_app/bridge.py`）— 实现 `ExecutorUI` Protocol，桥接 Kernel → Textual 部件

## 插件系统

- **PluginContract**（`core/plugins/contract.py`）— 插件接口协议
- **PluginHost**（`core/plugins/host.py`）— 启动时自动 `discover()` + `load()` 全部插件/技能，无需手动 `/plugin load`
- **SlotRegistry**（`core/plugins/slots.py`）— 生命周期插槽（on_session_start 等）
- **技能加载**：SkillProvider → 注册 `//` 命令 + `skill_<id>` 工具 + 上下文注入
- **/plugin list** — 列出已加载插件
- **示例插件**：`core/plugins/templates/hello-plugin/`（工具 + 命令 + 上下文提供者）

## 命令系统

- **CommandRegistry**（`core/commands/__init__.py`）— 统一路由：精确匹配 → 前缀匹配 → 模糊匹配
- **16 个内置命令**：`/help` `/profile` `/compact` `/export` `/import` `/plugin` `/session` `/memory` `/tools` `/update` `/clear` `/rollback` `/mcp` `/api` `/model` `/language`
- **技能命令**（`//` 前缀）：技能加载后自动注册，如 `//pptx` `//docx`
- **CommandPalette** — 输入 `/` 只显示内置命令，输入 `//` 只显示技能命令，井水不犯河水
- **插件命令注册**：`PluginAPI.register_command()` → `PluginHost.load()` 自动注册到 CommandRegistry，`unload()` 自动移除
- **路由优先级**：CLI args > AIDE_* env > settings.json > defaults（仅 config 相关）
- **向后兼容**：`COMMANDS` dict + `route_command()` 保留，委托给 CommandRegistry

## MCP 适配

- **MCPAdapter**（`core/tools/mcp/adapter.py`）— MCP 服务端工具 → Aide ToolDefinition 映射
- **MCPServerConfig** — 服务端连接配置（stdio: command+args / HTTP: url）
- **StdioTransport** — 子进程 + JSON-RPC 行协议（一行一消息）
- **HTTPTransport** — HTTP POST + 可选 SSE（Streamable HTTP）
- **JSON-RPC 2.0**（`protocol.py`）— 请求/响应/通知消息类型 + MCP 握手函数
- **工具命名规则**：`mcp_{server}_{tool}` 避免跨服务端冲突
- **工具缓存**：`discover_tools()` 首次发现后缓存，`refresh_tools()` 强制刷新
- 配置文件：`~/.aide/config/mcp_servers.json`（预留）

## 多模态消息格式

消息 `content` 支持两种格式（向后兼容纯文本）：

```python
# 纯文本（传统格式）
{"role": "user", "content": "hello"}

# 多模态（图片输入时自动升级）
{"role": "user", "content": [
    {"type": "text", "text": "这张图里有什么？"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
]}
```

- `build_user_content()` 在 `core/llm_gateway/content_builder.py` 中根据是否有图片决定格式
- 非视觉模型调用前 `_sanitize_messages()` 将 list 转为纯文本（图片→`[图片]` 占位）
- 非图片文件不进入 content 数组 — 完整路径直接嵌入 text（发送前 `_post_submit()` 还原）

## 工具可靠性

- **RetryConfig**（`core/tools/retry.py`）— 可配置的重试：max_retries、指数退避、backoff_factor
- **ErrorClass** — 三类错误：TRANSIENT（网络/超时→重试）、PERMANENT（权限/不存在→立即返回）、UNKNOWN（保守重试 1 次）
- **ToolRegistry.execute()** 内置重试，fc_loop 不再直接阻塞——工具错误返回错误字符串让 LLM 知晓，只在实际异常时 BLOCKED
- 每个 ToolDefinition 可设置 `retry` 字段覆盖默认重试配置

## CI/CD

[`.github/workflows/build.yml`](.github/workflows/build.yml) — tag `v*` 触发的三平台构建流水线：

| Job | 触发条件 | 内容 |
|-----|---------|------|
| `test` | tag + workflow_dispatch | 三平台 `uv run pytest tests/ -q --tb=short` |
| `build` | tag 且 test 通过 | PyInstaller 构建 → 上传 artifact |
| `release` | build 完成 | 打包 zip/tar.gz → 创建 GitHub Release |

- `PYTHONIOENCODING=utf-8` 解决 Windows CI 中文编码问题
- `actions/checkout@v5` + `astral-sh/setup-uv@v5`
- `fail-fast: false` — 一个平台失败不取消其他平台
- `uv.lock` 已提交仓库，确保 CI 依赖确定性

## 工程阶段

| Phase | 状态 | 关键交付 |
|-------|------|---------|
| **P0** | ✓ | Textual UI + LLM Gateway |
| **P1** | ✓ | 五工具 + Function Calling 循环 |
| **P2** | ✓ | 五层上下文 + 条目截获 + prompt 更新 + 冷启动 |
| **P3** | ✓ | 首页 + 暗主题 + 状态栏 + 托盘 + Markdown 高亮 + 命令面板 |
| **P4 Batch 1** | ✓ | Kernel/UI 分离、插件系统、Config 迁移、目录重组 |
| **P4 Batch 2** | ✓ | 自动加载插件、命令面板 / // 分离、Prompt 模板优化、TOOLS_PROMPT 不可变 |
| **P4 Batch 3** | ✓ | 全量审计（53 问题修复）、开源准备（README/MIT/CI）、PyInstaller 打包、`uv.lock` 提交、三平台 CI |
| **P5** | 🔧 维护 | bug 修复、跨平台适配、性能优化、社区反馈 |

659 测试全部通过。

## Prompt 体系

- **SOUL_TEMPLATE**（`core/setup.py`）— 极简 soul 模板（11 行），`{name}` 占位符由冷启动向导替换
- **TOOLS_PROMPT**（`core/setup.py`）— 不可变常量，含 10 个工具完整描述 + 使用策略 + 错误恢复
- **冷启动**：`OnboardingScreen._finish()` 使用 `SOUL_TEMPLATE.replace("{name}", name)` 生成 soul.md
- **Pipeline 注入**：Soul → TOOLS_PROMPT → 技能上下文 → 动态 prompt → 会话上下文

## Textual 要点

- `Static._render()` 是框架方法，不可覆盖。用自定义方法名（如 `_build_display()`）
- `@on(MessageClass)` 装饰器处理非 widget 嵌套的 Message；命名惯例 `on_xxx` 对独立 Message 不可靠
- `push_screen` / `pop_screen` 管理屏幕栈；`BINDINGS` 定义全局快捷键
- `@work(exclusive=True, thread=False)` 用于异步 worker
- `call_later(callback, *args)` 延迟调度
- pystray 在 daemon 线程运行，跨线程用 `call_from_thread()`
- Pydantic 配置用属性访问（`settings.llm.model`）不是 dict（`settings.llm.get("model")`）
- CSS 中 `width: 1fr` 让子元素撑满容器，比固定像素值可靠
- `content-align: center middle` 居中内容块，`text-align: center` 只在 widget 宽于内容时生效

## 踩坑记录

- **`query_one` 第二个参数必须是类，不能是字符串**。`bridge.py` 中 `MessageList` 必须运行时导入（不能只在 `TYPE_CHECKING`），否则 `isinstance(node, "MessageList")` 崩溃。
- **部分模型不支持原生 function calling**（如 DeepSeek v4 flash），会在 content 中输出 `<invoke>` XML。`fc_loop.py` 的 `_extract_xml_tool_calls()` 提供了 fallback 解析，`message_list.py` 的 `finish_ai_message()` 有兜底转义。
- **System 消息不应存入对话历史**。`agent.py` 合并 conversation 时需过滤 `role != "system"`。
- **`/help` 展示的命令来自模块级 `COMMANDS` dict**（`@_cmd` 装饰器填充），新增命令时需同时加到 `register_builtin_commands()` 和 `COMMANDS` dict。
- **捕获引擎在对话后静默运行**，条目写入 `agent/data/*.json`（status=pending），用户通过 `/profile update` 整合。不在对话中注入或弹通知。
- **DeepSeek 严格校验**：tool 消息必须有 `tool_call_id`，assistant 消息有 `tool_calls` 时必须保留 `tool_calls` 字段。会话恢复时所有字段必须原样保留，否则 API 返回 400。
- **非视觉模型消息清洗**：`_sanitize_messages()` 将多模态 content（`list[dict]`）转为纯文本。图片部分替换为 `[图片]` 占位符。原始 conversation 保留多模态格式用于存储和 UI。
- **InputBox 文件附件**：粘贴文件路径 → `_detect_dropped_files()` 提取路径并替换为 `[filename]` chip 渲染。发送时 `_post_submit()` 将 chip 还原为完整路径发给 LLM。光标自动跳过 token 内部，Backspace/Delete 整体删除 token。
- **Textual TextArea Ctrl+Z/Ctrl+Y 已禁用**：聊天输入框不需要 undo/redo，且 Textual 内部在内容大幅变化后光标位置与文档行数不同步会导致崩溃。
