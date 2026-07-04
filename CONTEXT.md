# Aide Agent 完整开发方案 v1.1

> **版本**：v1.1  
> **最后更新**：2026-06-29  
> **定位**：本地优先的个人智能管家，以动态 prompt 为核心演化机制，以条目目录为长期记忆载体，以 JSON 文件存储为数据基石。不追求全能，只追求“越用越懂你”。  
> **核心原则**：用户可控、本地隐私、边界清晰、渐进演化。

---

## 一、项目背景与设计演变

### 1.1 项目定位

Aide Agent 是一个本地运行的个人智能管家。它不追求成为一个可编程的万能工具，而是专注于在长期陪伴中越来越理解用户。这种理解体现在动态调整的系统提示词（prompt）上——随着使用，Aide 会记住你的偏好、工作流和重要事实，并在合适的时机运用这些知识。

### 1.2 设计演变概述

Aide Agent 最初继承了 SCFrame 的完整能力集——OODA 引擎、补丁式进化、知识图谱、多渠道接入、主动调度引擎等。经过多轮批判性收敛，核心设计发生了根本性转变：

- **从“能力积累”转向“理解深化”**：删除了整个技能系统（Skill IR、Compiler、L0-L3 分级、冷热管理），进化不再意味着“系统学会做新的事”，而是“系统越来越理解用户”。动态 prompt 成为唯一的演化载体。
- **从“重型基础设施”转向“轻量工程实现”**：移除了 ChromaDB 向量数据库、知识图谱引擎、工作流引擎、内置浏览器、定时任务引擎。所有存储回归 JSON 文件系统，备份即复制文件夹。
- **从”防御性安全”转向”信任性行为”**：移除了沙箱执行环境、审批流程和硬约束校验。仅以 Soul 中的行为准则进行软引导，用户指令优先级最高，安全责任交还用户。
- **从“可插拔技能市场”转向“插件生态适配”**：插件系统对标 OpenClaw 的 ClawHub 技能生态和 Claude Code 的 MCP 工具规范，可直接沿用社区技能，避免从零构建生态。

### 1.3 v1.1 设计微调（2026-06-29）

基于工程可行性和主流 Agent 实践，对 v1.0 方案做了以下关键调整：

- **前端从 Web 切换到终端 TUI**：砍掉 PySide6 + QWebEngineView + React 19，换用 Textual（Python 原生 TUI 框架）+ pystray 系统托盘。全栈统一为纯 Python，打包体积从 500MB+ 降至 ~50MB。
- **砍掉 Planner 模块**：主流 Agent 应用（Claude Code、Cursor、Copilot）无一使用独立规划步骤。改用 LLM function calling 循环——LLM 自行决定“调工具还是回复”，边看边做，更灵活且闲聊场景省 50% LLM 调用。
- **移除硬约束安全层**：砍掉 Harness 模块及所有工具层校验（路径白名单、命令模式过滤、频率限制、磁盘检查），安全模型纯依赖 Soul 软引导。用户指令优先级最高。
- **砍掉 MessageHub**：职责分给 ContextManager（消息存储）和 Storage（会话元信息），减少概念层级。
- **FastAPI 降为 Phase 2+ 薄适配层**：Phase 1 纯 Textual 单进程，无 HTTP 通信需求。Phase 2+ 开发 TypeScript 独立壳时补一层 FastAPI 路由，对 Core 零侵入。
- **插件加载器推迟**：Phase 1 只在 `tools/` 下留 Plugin Protocol 定义，不建独立加载器模块。

### 1.4 关键设计约束

1. **动态 prompt 是背景信号，用户指令优先**：有明确需求时不使用默认。动态 prompt 仅在用户指令模糊时辅助决策。
2. **条目目录与 prompt 更新解耦**：截获信号时即时写入条目目录，更新时由用户主动触发，回溯源会话独立完成。两者互不阻塞。
3. **非必要不修改 prompt**：仅在用户主动声明或复杂任务后，结合上下文感知更新。触发条件量化：①用户显式声明偏好时立即截获；②用户纠正行为发生时立即截获工作流信号；③长记忆仅在多会话（≥3 次）且时间跨度≥7 天时触发截获。更新由用户手动执行。
4. **长记忆是故事总线，会话总览是事件摘要**：长记忆记录跨会话的重要事实和事件，会话总览记录当前会话内的关键节点。两者职责清晰。
5. **提示词工程原则**：系统 prompt（Soul）定义人设、能力边界、工具描述，是基础层；动态 prompt（偏好/工作流/长记忆）是演化层。所有演化最终收敛为系统提示词的语义增量，避免能力膨胀带来的维护债务。
6. **信息截获实时化、规则化**：信息截获在对话中实时完成，基于规则引擎和轻量语义匹配，不依赖 LLM，响应时间 <100ms。
7. **更新期间暂停对话**：Prompt 更新由用户主动触发，更新期间 Aide 进入维护模式，暂停对话功能，确保数据一致性。
8. **单 Agent 原则**：Aide 永远只维护一个 Agent 实例。不支持创建、切换或多 Agent 协作。
9. **冷启动引导的强制性**：当 agent/data/ 下的所有条目目录文件均为空或不存在时，系统必须进入冷启动引导流程。在引导完成前，对话功能受限。
10. **Agent Soul 不可动态切换**：系统提示词在启动时加载，运行期间不可更改。
11. **更新期间严格串行**：更新过程中不可进行对话等操作。
12. **用户自由开关动态 prompt 功能**：用户自由开关 prompt 的动态调整功能，关闭后仍使用上述 prompt，且继续捕获信息，但不再进行调整。
13. **系统绝不自动调用 LLM 进行 prompt 更新**：系统绝不自动调用 LLM 进行 prompt 更新。这些工作全权交给用户。
14. **上下文压缩与 prompt 更新不同步**：上下文是会话级的，压缩时可以同时进行另一会话的对话；但 prompt 更新期间系统锁定。
15. **会话总览是记忆的核心**：由于输入时仅有近 N 轮对话的完整内容，会话窗口上下文的持续性依靠总览；会话总览由上下文压缩而来。

---

## 二、技术栈及设计注解

### 2.1 技术栈总览

| 层次 | 选型 | 设计思路与实现逻辑 |
|------|------|-------------------|
| **核心语言** | Python 3.13+ | asyncio 性能显著提升，预留 JIT 编译优化接口。选择 Python 是出于快速迭代和丰富 AI 生态的考虑。 |
| **终端 UI** | Textual + pystray | 纯 Python TUI 框架，对标 Claude Code / Hermes 的终端体验。CSS-like 布局、Rich 生态（Markdown + Pygments 代码高亮）、内建流式支持。pystray 提供系统托盘图标和后台常驻。全栈统一为 Python，打包体积 ~50MB。Phase 2+ 可选开发 TypeScript 独立窗口壳。 |
| **异步运行时** | asyncio | Python 3.13 深度优化，保持统一并发模型。所有核心模块基于 asyncio 构建。 |
| **数据存储** | 纯 JSON 文件系统 | 零依赖，完全可读，备份即复制目录。采用 Write-Actor 模型保证并发安全。 |
| **LLM 调用** | LLM Gateway 抽象层 | 适配 OpenAI、Ollama 等主流 API 格式，统一重试和流式适配。Ollama 兼容 OpenAI API 格式，两个 Provider 共享同一套 HTTP 调用逻辑，适配器只负责拼 base_url 和 headers。模型选择完全由用户在配置中决定，Aide 直接使用。 |
| **配置管理** | JSON + Pydantic Settings | 自动类型校验和提示。 |
| **插件加载** | importlib + 协议适配 | Phase 1 只在 tools/ 下定义 Plugin Protocol，不建独立加载器模块。Phase 2+ 启动加载 + watchdog 文件变更检测触发热加载。适配 OpenClaw 技能包格式和 Claude Code MCP 工具声明格式（仅兼容声明格式和工具描述规范，不兼容二进制运行时）。 |
| **性能热点** | 纯 Python | Token 计数和命令校验 Python 原生足够快。 |
| **分发** | PyInstaller | 稳定优先，Textual 纯 Python 依赖打包体积可控。 |
| **进程模型** | 单进程异步架构 | Textual App + Core 模块同进程运行。Phase 2+ 增加 TypeScript 壳时为双进程（独立窗口壳 ←HTTP/WS→ FastAPI 适配层 → Core），但 Core 不感知通信方式。 |
| **Web 适配层** | FastAPI（Phase 2+） | 仅为 TypeScript 独立壳提供 HTTP/WS 通信通道，对 Core 零侵入——Core 接口保持纯函数调用，FastAPI 在其外围做路由映射。Phase 1 不引入。 |

### 2.2 核心模块及组件

| 模块 | 组件 | 功能说明 | 设计注解 |
|------|------|---------|---------|
| **LLM Gateway** | Provider 适配器 | 适配 OpenAI/Ollama 统一接口 | Ollama 复用 OpenAI 兼容格式，共享 HTTP 调用逻辑 |
|  | 流式适配器 | SSE → AsyncIterator 统一事件流 | Textual 直接消费 AsyncIterator |
| **Executor** | Function Calling 循环 | LLM 决定“调工具还是直接回复” | 无预规划步骤。调工具 → 结果喂回 → 循环至完成或打断 |
|  | 两状态机 | READY / BLOCKED | 失败直接通知用户并询问下一步 |
| **Prompt Manager** | 条目截获引擎 | 信号产生时即时写入条目目录 | 规则引擎，无 LLM 参与，<100ms |
|  | 上下文感知更新 | 查看源会话 + 已有 prompt → LLM 整合 | 用户主动触发，更新期间暂停对话 |
| **ContextManager** | ContextIngester | 消息摄取（写入 turn 文件） | 每轮对话后立即运行 |
|  | ContextAssembler | 组装四层上下文 | 相关性过滤，内存缓存 prompt 文件 |
|  | ContextCompactor | 保留最近 N 轮对话，生成会话总览 | 对话结束后调用 LLM 生成极简摘要 |
| **工具层** | 内置工具 | read_file / write_file / run_shell / search_memory / web_search | Phase 1 五工具，纯 Soul 软约束，无硬校验 |
|  | plugin_protocol.py | Plugin Protocol 定义 | Phase 1 只留接口协议，Phase 2+ 建 plugin_loader/ |
| **Storage** | JSON 读写 | 原子写（tempfile + os.replace）+ 单协程写队列 | 降级为 core 工具文件，不独立成模块 |
| **Runtime** | Bootstrap | 核心文件完整性校验 → 初始化 Core → 启动 Textual App + pystray | 单进程编排 |

---

## 三、核心功能清单

### 3.1 核心推理与执行

- **多轮对话**：通过 LLM Gateway 调用，支持流式响应。
- **Function Calling 循环**：每轮对话组装四层上下文后交给 LLM，LLM 自行决定调用工具或直接回复。调用工具后将结果喂回 LLM 继续循环，直至任务完成、用户打断或达到最大轮次（硬编码 5 轮）。达到上限后强制终止并通知用户"任务可能需要你手动介入"。无独立规划步骤——与 Claude Code、Cursor、Copilot 等主流 Agent 实践一致。
- **两状态执行**：工具执行成功 → 结果喂回 LLM 继续。任何失败 → BLOCKED → 通知用户并询问下一步。
- **模型配置由用户自行管理**：LLM 选择和配置完全由用户在 config.json 中指定，Aide 不提供自动路由或 Fallback 链。

### 3.2 记忆与演化

- **动态 prompt 三类**：偏好 prompt（用户声明 + 行为反馈）、工作流 prompt（用户纠正 + 任务模式提取）、长记忆 prompt（跨会话关键信息，严格触发条件：≥3 次不同会话提及且时间跨度 ≥7 天）。非必要不修改，更新由用户主动触发。
- **条目目录管理**：信号截获时即时写入条目目录 JSON 文件，每条记录包含 `content`、`source`（指向源会话和事件）、`status`（pending/integrated/replaced/orphaned）等字段。截获基于规则引擎 + 轻量语义匹配，无需 LLM 参与，响应时间 <100ms。Phase 1 相似度算法使用 Jaccard（阈值 0.6），预留可插拔接口，Phase 2+ 可升级为 embedding 方案。Prompt Manager 在用户触发更新时查看源会话（仅加载条目产生前后各 5 轮对话），结合已有 prompt 进行上下文感知整合。若源会话已不存在，pending 条目标记为 `orphaned` 并跳过。
- **冷启动引导对话**：首次使用时聚焦 5 个固定问题，以独立表单 UI（非对话形式）完成：Soul 层面——你想怎么称呼我、你希望我的个性是怎样的、你对我有哪些要求；用户层面——你有哪些偏好；工作层面——你希望我按怎样的流程工作；记忆层面——我需要记住哪些重要信息。存入会话记录，作为动态 prompt 的初始锚点。
- **相关性过滤**：动态 prompt 按任务相关性选择性注入上下文。相关性高的段落以“高优先级”形式展开，相关性低的折叠为摘要。Phase 1 采用 Jaccard 相似度，预留算法插拔接口。
- **更新无冷却期**：用户可随时触发 prompt 更新。条目截获照常运行，pending 条目积累后用户自行决定更新时机。
- **条目语义去重**：Phase 1 基于 Jaccard 相似度（阈值 0.6）对条目 `content` 与已有条目比对，高于阈值则更新已有条目而非新增，避免 prompt 膨胀。预留算法接口。

### 3.3 上下文管理

- **五层上下文**：

  | 层级 | 来源 | 内容 | 用途 |
  |------|------|------|------|
  | **1. 系统 Soul** | agent/soul.md | 人设、行为准则、工具描述 | 最高优先级指令位，始终注入 |
  | **2. 动态 prompt** | agent/*.md | 偏好 + 工作流 + 长记忆 | 按相关性选择性注入，高相关展开，低相关折叠 |
  | **3. 会话总览** | overview.json | 会话内早期内容的压缩摘要（自上次 `/compress` 起） | 长会话中提供窗口外的历史上下文 |
  | **4. 窗口上下文** | cache.json | 每轮对话后增量更新的语义记录 | 当前会话的累积上下文素材 |
  | **5. 近 N 轮原文** | messages/ | 最近 8 轮完整对话（含工具调用细节） | 精确原文，保障上下文连贯性 |

- **会话总览（overview.json）**：用户通过 `/compress` 手动触发压缩，每次覆盖写入。取整个会话内容调用 LLM 生成概述，覆盖全部话题、关键偏好声明、Aide 被纠正的行为、达成的决策。长会话可多次压缩（累积 → 压缩 → 继续累积 → 再压缩）。上下文注入时，如有 overview 则作为前段历史的压缩摘要注入。
- **窗口上下文（cache.json）**：每轮对话后 ContextIngester 追加一条简略自然语言摘要，无结构化字段。注入 LLM 上下文，保障会话窗口内的对话连贯。用户触发压缩后清理。
- **会话总线（timeline.json）**：ContextIngester 每轮后生成一句话事件概览 + 时间戳。轮次级索引，不注入上下文，仅用于按时间查找会话入口。
- **对话原文（messages/）**：每轮独立 JSON 文件，完整存档。源回溯用，上下文注入仅取最近 8 轮。
- **上下文用量显示**：不设计自动压缩。界面实时显示当前上下文 token 用量，用户自行判断何时触发 `/compress`。

### 3.4 安全模型

- **纯软约束（Soul 行为准则）**：不设硬约束校验层。网络和文件操作给予权限，但在无明确指令时不主动写入或查找。用户指令优先级最高。安全责任交还用户——Aide 不替用户判断什么操作安全、什么危险。
- **无拦截机制**：Aide 不拒绝任何用户指令。如果用户要求执行危险操作，Aide 执行并承担后果。Soul 可在执行前做口头提醒，但不设阻止。

### 3.5 插件系统

- **Phase 1 范围**：仅在 `tools/` 下定义 Plugin Protocol（工具签名 + 加载/卸载接口协议），不建独立加载器模块，不实现热加载。
- **Phase 2+ 完整形态**：
  - **生态适配**：对标 OpenClaw 的 ClawHub 技能生态和 Claude Code 的 MCP 工具规范。适配 OpenClaw 技能包格式和 Claude Code MCP 工具声明格式（仅兼容声明格式和工具描述规范，不兼容二进制运行时）。社区技能需用 Python 重写执行部分，或通过子进程调用 Node.js 等运行时。
  - **启动加载 + 热加载**：启动时扫描 `plugins/` 目录，importlib 加载，watchdog 文件变更检测触发热加载。
  - **热插拔兜底**：正在执行的调用允许完成，新请求立即返回“插件已卸载”。

### 3.6 工具集

**Phase 1 内置工具**：

| 工具 | 功能 | 说明 |
|------|------|------|
| **read_file** | 读取本地文件内容 | 基础文件操作 |
| **write_file** | 写入/创建文件 | Soul 软引导：无明确指令时不主动写入 |
| **run_shell** | 执行 Shell 命令 | 结果流式返回，超时默认 30s（可配置）。无命令白名单，无危险模式拦截 |
| **search_memory** | 搜索条目目录 + 会话总览 | 关键词匹配 |
| **web_search** | 联网搜索（SearXNG / Tavily） | Soul 软引导：无明确指令时不主动发起 |

Phase 2 新增：`plugin_call`（统一插件调用入口）。

### 3.7 数据安全与并发

- **重点数据一键导出/导入**：用户可通过 `/export` 导出关键数据（Soul、config、prompt 文件、会话数据）为压缩包，通过 `/import` 导入恢复。将备份责任交还用户——Aide 不做自动备份和灾难恢复。条目目录丢失可从源会话重建，Soul 和 config 丢失无法重建，用户自行保管。
- **Write-Actor 并发模型**：所有写操作入队至单一协程顺序执行，内存缓存仅作读副本，通过 tempfile + os.replace 原子替换确保崩溃一致性。

---

## 四、存储结构

```
~/.aide/
├── agent/
│   ├── soul.md                 # 系统提示词（人设+行为准则+工具描述）
│   ├── config.json             # Agent 配置（LLM、插件、限制等）
│   ├── preferences.md          # 偏好 prompt（最终注入上下文）
│   ├── workflows.md            # 工作流 prompt
│   ├── long_term_memory.md     # 长记忆 prompt
│   └── data/                   # 条目目录（永久保留，必须备份）
│       ├── preferences.json    # 偏好条目目录（含来源指针）
│       ├── workflows.json      # 工作流条目目录
│       ├── long_term_memory.json # 长记忆条目目录
│       └── topic_frequency.json # 主题频率追踪（用于长记忆触发判断）
├── sessions/                   # 会话数据
│   └── {session_id}/
│       ├── timeline.json       # 会话总线（轮次级索引 + 一句话事件概览）
│       ├── overview.json       # 会话总览（整个会话的完整概述，跨会话注入上下文）
│       ├── cache.json          # 窗口上下文（每轮语义记录，注入上下文用）
│       └── messages/           # 对话分段文件（完整原文存档）
│           ├── turn_001.json    # 每轮对话独立文件
│           └── ...
├── plugins/                    # Phase 2+ 已安装插件目录
├── logs/                       # 运行日志
└── archives/                   # 归档数据（长期不活跃会话等）
```

### 存储设计注解

- **单 Agent 设计**：agent/ 目录直接存放 soul.md 等文件，无需二级目录。
- **条目目录**（agent/data/ 下的 JSON 文件）：是长期记忆的唯一数据源，必须永久保留并纳入备份。每条记录包含 `content`（自然语言描述）、`source`（指向源会话和事件，如 session_id 和 turn_id）、`status`（pending/integrated/replaced）等字段。条目截获时写入，`status: pending`；Prompt Manager 更新后标记为 `integrated` 或 `replaced`。
- **Prompt 文件**（agent/ 根目录下的 .md 文件）：是最终注入 LLM 上下文的 prompt，由 Prompt Manager 根据条目目录全量重构生成。上下文组装时从内存缓存读取，避免重复读盘。
- **topic_frequency.json**：维护关键词频率表，记录每个关键词的出现会话次数和首次/末次提及时间，用于长记忆截获判断。
- **会话存储**：每个会话以 session_id 为目录名独立存储。
  - **会话总线（timeline.json）**：轮次级索引，每轮一条记录，包含时间戳和一句话事件概览。不注入上下文，仅用于按时间查找会话入口。
  - **会话总览（overview.json）**：用户 `/compress` 手动触发，每次覆盖写入。LLM 根据整个会话生成概述。长会话可多次压缩，每次生成新 overview 覆盖旧文件。
  - **窗口上下文（cache.json）**：每轮后 ContextIngester 追加一条简略自然语言摘要。注入 LLM 上下文，压缩后清理。
  - **对话原文（messages/）**：每轮一个 JSON 文件，完整存档"用户输入 → Aide 响应"过程（含工具调用等中间步骤）。源回溯用，上下文注入仅取最近 8 轮。
- **并发安全**：采用 Write-Actor 模型——所有写操作入队至单一协程顺序执行，内存缓存仅作读副本，通过 tempfile + os.replace 原子替换确保崩溃一致性。

---

## 五、最终实现效果规划

### 5.1 用户日常使用流程

**首次使用 — 冷启动引导：**

打开终端，Aide 以独立表单 UI 引导 5 个固定问题，建立初始画像。引导完成后生成初始 Soul + prompt 文件，进入正常对话。

**日常对话：**

每轮对话后，Aide 实时更新 cache.json（窗口上下文）和 timeline.json（事件索引）。界面实时显示当前上下文 token 用量，用户自行判断是否拥挤。当 cache 累积过多时，用户执行 `/compress` 手动压缩生成 overview.json，随后 cache 清空。

**Prompt 演化：**

当用户觉得 Aide 积累了足够多的偏好信号，执行 `/profile update`。Aide 回溯源会话，整合所有 pending 条目，全量重构 prompt 文件。更新期间暂停对话，完成后恢复。

**数据管理：**

用户通过 `/export` 一键导出关键数据（Soul、config、prompt 文件、会话数据），通过 `/import` 恢复。`~/.aide/` 目录可直接复制作为备份。

**常用命令：**

| 命令 | 功能 |
|------|------|
| `/profile` | 查看当前生效的 Soul + 动态 prompt |
| `/profile update` | 手动触发 prompt 更新（整合 pending 条目） |
| `/compress` | 手动触发会话压缩（cache → overview） |
| `/export` | 一键导出关键数据 |
| `/import` | 一键导入恢复 |
| `/help` | 查看所有可用命令 |

### 5.2 演化视角

Aide 不会自动变得更”强大”，但会越来越”懂你”。演化分两条独立管线：

- **上下文管线（实时）**：每轮对话 → cache.json 增量 + timeline.json 索引 → 用户 `/compress` → overview.json。保障对话连贯性。
- **Prompt 管线（用户主导）**：每轮对话 → 规则引擎截获信号 → 条目目录（pending）→ 用户 `/profile update` → LLM 回溯整合 → 全量重构 prompt 文件。

两条管线互不阻塞。用户纠正、偏好声明被实时截获，但从不静默写入 prompt。演化节奏完全由用户掌控。

### 5.3 生态视角

Phase 2+ Aide 可以直接安装来自 OpenClaw 和 Claude Code 社区的技能包（声明格式兼容），扩展渠道接入、邮件日历、智能家居等能力。插件系统对标社区标准，避免从零构建生态。

---

## 六、已完全移除的功能及原因

| 移除功能 | 原因 |
|---------|------|
| **Planner 模块** | Function calling 循环替代，与 Claude Code/Cursor/Copilot 等主流 Agent 一致。闲聊场景省 50% LLM 调用 |
| **MessageHub 模块** | 职责分给 ContextManager（消息存储）和 Storage（会话元信息），减少概念层级 |
| **Harness + 硬约束安全层** | 纯 Soul 软引导。移除路径白名单、命令模式过滤、频率限制、磁盘检查等所有校验。不设违规降级和审计。安全责任交还用户 |
| **Safe Mode 自动恢复** | 不做自动备份和灾难恢复。替之以 `/export` / `/import` 一键导出导入，备份责任交给用户 |
| **Idle Detection** | 单进程 Textual 应用，进程退出即释放资源，无需进程内空闲检测和模型卸载逻辑 |
| **Plugin Loader（Phase 1）** | Phase 1 无插件可加载，仅在 tools/ 下留 Plugin Protocol 定义 |
| **FastAPI（Phase 1）** | Phase 1 纯 Textual 单进程，无 HTTP 通信需求。Phase 2+ 作为 TypeScript 壳的薄适配层补回 |
| **PySide6 + QWebEngineView + React 19** | Textual 纯 Python TUI 替代，打包体积从 500MB+ 降至 ~50MB |
| **更新冷却期** | 用户随时触发更新，不做时间或轮次限制 |
| 技能系统（Skill IR / Compiler / L0-L3 分级 / 冷热管理） | 进化中心从“能力积累”转向“理解深化”，动态 prompt 替代技能作为唯一演化载体 |
| Intent Layer | 模糊，动态 prompt 已覆盖其职责 |
| Cognitive Stream | Prompt Manager 已通过条目目录实现演化 |
| MCP 服务端 | 无明确使用场景，未来可通过插件实现 |
| DAG 复杂依赖管理 | 个人管家场景极少需要多步依赖 |
| 四状态机（DEFERRED/WAITING_INTERNAL） | 极少触发，简化执行逻辑 |
| Event Wake System | 无 DEFERRED 状态后无必要 |
| 局部重规划 | 失败时直接通知用户即可 |
| Contextual Risk Model | 动态风险评估收益不明确 |
| 模板系统 | Prompt Manager 直接生成 .md，不需要模板 |
| Trace 事件流（events/ 目录） | 事件信息已通过会话总览和条目目录 source 指针覆盖 |
| Anthropic 适配器 | Phase 1 不需要 |
| 补丁式进化引擎 + 进化委员会 | 不可控的 LLM 驱动优化，替换为用户主导的 prompt 更新 |
| 沙箱执行环境 | Soul 软引导替代，不设硬拦截 |
| 内置浏览器（Playwright + mitmproxy） | 偏离管家定位，过于沉重 |
| 定时任务引擎（cron 调度） | 操作系统已有成熟替代方案 |
| ChromaDB 向量检索 | 动态 prompt 已覆盖语义检索需求 |
| 知识图谱引擎 | 日常管家场景收益极低 |
| 工作流引擎 | 主动调度已覆盖 |
| Rust 加速层 | Python 原生足够快 |

---

## 七、关键设计约束（总结）

1. **动态 prompt 是背景信号，用户指令优先**：有明确需求时不使用默认。
2. **条目目录与 prompt 更新解耦**：截获实时化、规则化；更新用户主导、回溯源会话。
3. **非必要不修改 prompt**：触发条件量化，更新由用户手动执行，更新期间暂停对话。
4. **长记忆是故事总线，会话总览是事件摘要**：长记忆记录跨会话重要事实，会话总览记录会话内关键节点。
5. **提示词工程原则**：Soul 为基础层，动态 prompt 为演化层，演化收敛为语义增量。
6. **信息截获实时化、规则化**：基于规则引擎 + 轻量语义匹配，不依赖 LLM，<100ms。
7. **更新期间暂停对话**：用户主动触发，确保数据一致性。
8. **单 Agent 原则**：Aide 永远只维护一个 Agent 实例。不支持创建、切换或多 Agent 协作。
9. **冷启动引导的强制性**：当 agent/data/ 下所有条目目录文件为空或不存在时，系统必须进入冷启动引导流程，引导完成前对话功能受限。
10. **Agent Soul 不可动态切换**：系统提示词在启动时加载，运行期间不可更改。
11. **更新期间严格串行**：更新过程中不可进行对话等操作。
12. **用户自由开关动态 prompt 功能**：用户自由开关 prompt 的动态调整功能，关闭后仍使用上述 prompt，且继续捕获信息，但不再进行调整。
13. **系统绝不自动调用 LLM 进行 prompt 更新**：全权交给用户。
14. **上下文压缩与 prompt 更新不同步**：上下文是会话级的，压缩时可同时进行另一会话的对话；但 prompt 更新期间系统锁定。
15. **会话总览是记忆的核心**：会话总览（overview.json）覆盖整个会话的全部话题、决策、偏好声明，而非近几轮对话的摘要。由对话结束后 LLM 根据完整会话生成，是跨会话历史记忆的核心载体。
16. **Core 与 UI 解耦**：Core 接口为纯 async 函数调用，不感知 Textual / FastAPI / TypeScript 壳等上层实现。Phase 2+ 增加通信层时对 Core 零侵入。
17. **纯软约束，无硬拦截**：不设路径白名单、命令过滤、频率限制等硬校验。安全责任交还用户，Aide 不替用户判断操作风险。
18. **备份责任交还用户**：不做自动备份和灾难恢复。提供 `/export` / `/import` 一键导出导入，由用户自行管理数据安全。
19. **Function Calling 上限**：硬编码最大 5 轮循环，达到上限强制终止并通知用户。
20. **无更新冷却期**：用户可随时触发 prompt 更新，不做时间或轮次间隔限制。
21. **源会话丢失处理**：Prompt 更新时若 pending 条目的源会话已不存在，标记为 `orphaned` 并跳过。

---

## 八、工程规划

### 总览

| Phase | 目标 | 一句话 |
|-------|------|--------|
| **P0** | 裸对话终端 | 能聊天 |
| **P1** | Agent 能力 | 能做事 |
| **P2** | 记忆与演化 | 能记住、能演化 |
| **P3** | 前端 | 好看、好用 |
| **P4** | 打磨与发布 | 能分发 |

---

### P0 — 裸对话终端

> **目标**：证明核心回路能跑。启动一个 Textual 终端窗口，用户输入消息，LLM 流式回复。

**交付物：**

- [ ] `shell/` 入口脚本：启动 Textual App
- [ ] `ui/textual_app/` 最简聊天界面：输入框 + 消息流（支持 Markdown 渲染）
- [ ] `core/llm_gateway/` LLM Gateway 骨架：OpenAI + Ollama 两个 Provider 适配器，统一 `async chat(messages) -> AsyncIterator[str]` 接口
- [ ] `config/settings.py` Pydantic Settings：LLM provider、model、base_url、api_key
- [ ] `config.json` 配置模板（OpenAI + Ollama 双示例）
- [ ] SSE 流式 → Textual 逐字渲染
- [ ] 基础错误处理：LLM 调用失败时显示错误消息，不崩溃
- [ ] 键盘退出（Ctrl+C / Escape）优雅关闭

**不做：**
- 无工具调用
- 无上下文管理（不存 session、不读 Soul）
- 无记忆与演化
- 无系统托盘
- 无命令系统（`/profile` 等）

**目录结构（P0 结束时）：**

```
Aide/
├── core/
│   └── llm_gateway/
│       ├── __init__.py
│       ├── provider.py          # AbstractProvider Protocol
│       ├── openai_provider.py
│       └── ollama_provider.py
├── ui/
│   └── textual_app/
│       ├── __init__.py
│       ├── app.py               # Textual App 主类
│       └── widgets/
│           ├── input_box.py     # 用户输入
│           └── message_list.py  # 消息流
├── shell/
│   └── main.py                  # 入口
├── config/
│   ├── settings.py              # Pydantic Settings
│   └── config.example.json      # 配置模板
└── requirements.txt
```

**验收：** 终端里跟 OpenAI 或 Ollama 模型连续对话，流式输出正常，Ctrl+C 退出不报错。

---

### P1 — Agent 能力

> **目标**：Aide 能调用工具完成任务。用户说"帮我查天气"，Aide 调 `web_search` 返回结果。

**交付物：**

- [ ] `core/tools/` 五个内置工具：`read_file`、`write_file`、`run_shell`、`search_memory`、`web_search`
- [ ] `core/tools/plugin_protocol.py` Plugin Protocol 定义
- [ ] `core/executor/` Function Calling 循环引擎
  - 组装上下文 → LLM 决定（tool_call 或 reply）→ 调工具 → 结果喂回 → 循环
  - `max_turns` 硬编码 5，达到上限强制终止并通知用户
  - 两状态：READY / BLOCKED，失败通知用户并等待指令
- [ ] `core/storage.py` JSON 读写 + Write-Actor（tempfile + os.replace）
- [ ] `core/llm_gateway/` 扩展：function calling 支持（tools 参数透传）
- [ ] LLM function calling 的 JSON schema 与五个工具的映射
- [ ] 工具调用结果在 Textual UI 中展示（"正在调用 web_search..." → 结果摘要）

**目录结构增量：**

```
core/
├── tools/
│   ├── __init__.py
│   ├── read_file.py
│   ├── write_file.py
│   ├── run_shell.py
│   ├── search_memory.py
│   ├── web_search.py
│   └── plugin_protocol.py
├── executor/
│   ├── __init__.py
│   ├── loop.py                 # Function Calling 循环
│   └── state.py                # READY / BLOCKED
└── storage.py                  # JSON + Write-Actor
```

**验收：** 用户说"帮我搜一下今天天气"或"读一下 README.md"，Aide 调用对应工具并返回结果。连续 6 次工具调用后强制终止。工具失败时显示错误并等待用户下一步指令。

---

### P2 — 记忆与演化

> **目标**：Aide 能记住会话上下文、能演化 prompt。五层上下文组装、条目截获、prompt 更新全部就绪。

**交付物：**

- [ ] `core/context_manager/` 上下文管理器
  - **ContextIngester**：每轮后写入 `timeline.json`（一句话摘要）+ 增量更新 `cache.json`（语义记录）+ 写入 `messages/turn_NNN.json`（完整原文）
  - **ContextAssembler**：组装五层上下文（Soul → 动态 prompt → overview → cache → 近 8 轮原文）
  - **ContextCompactor**：`/compress` 触发，取整个会话 → LLM 生成 `overview.json` → 清空 `cache.json`
- [ ] `core/prompt_manager/` Prompt 管理器
  - **条目截获引擎**：规则引擎（关键词正则 + Jaccard 去重 0.6），每轮后运行，<100ms
  - **条目目录管理**：读写 `agent/data/*.json`，维护 pending/integrated/replaced/orphaned 状态
  - **Prompt 更新**：`/profile update` 触发，回溯源会话 → LLM 整合 → 全量重构 `agent/*.md`
  - **topic_frequency.json**：关键词频率追踪，长记忆触发判断（≥3 次 + ≥7 天）
- [ ] **冷启动引导表单**：5 个固定问题，独立 UI，创建初始 Soul + prompt
- [ ] **上下文用量显示**：界面实时显示当前 token 估算
- [ ] Soul、动态 prompt（偏好/工作流/长记忆）的基础模板
- [ ] **命令系统**：`/profile`、`/profile update`、`/compress`、`/export`、`/import`、`/help`
- [ ] `~/.aide/` 目录初始化逻辑

**目录结构增量：**

```
core/
├── context_manager/
│   ├── __init__.py
│   ├── ingester.py             # ContextIngester
│   ├── assembler.py            # ContextAssembler（五层上下文）
│   └── compactor.py            # ContextCompactor（/compress）
├── prompt_manager/
│   ├── __init__.py
│   ├── capture.py              # 条目截获引擎（规则引擎）
│   ├── entries.py              # 条目目录管理
│   └── updater.py              # Prompt 全量重构（/profile update）
ui/textual_app/
├── commands/                   # 命令系统
└── widgets/
    └── onboarding.py           # 冷启动引导表单
```

**验收：**

- 对话自动写入 timeline + cache + messages
- `/compress` 生成 overview.json，cache 清空
- 声明"我喜欢简洁回复"被截获为偏好条目（status: pending）
- `/profile update` 追溯源会话，生成更新后的偏好 prompt
- 后续对话中 Aide 按简洁风格回复
- `/export` 导出压缩包，`/import` 恢复

---

### P3 — 前端

> **目标**：从裸终端升级为完整的可视化交互界面。布局、面板、设置、冷启动表单、命令栏全部就绪。

**交付物：**

- [ ] **布局系统**：分栏/面板结构（对话区、状态栏、命令栏）
- [ ] **消息渲染增强**：代码高亮（Pygments）、Markdown 完整支持（Rich）、工具调用卡片（"调用了 web_search → 结果摘要"）
- [ ] **流式渲染优化**：逐 token 打字效果，不闪烁
- [ ] **状态栏**：当前模型名、上下文 token 用量、会话 ID、连接状态
- [ ] **命令栏**：`/` 触发，自动补全，命令帮助
- [ ] **冷启动引导 UI**：独立表单，分步填写，进度指示
- [ ] **设置面板**：LLM 配置、外观主题、上下文参数（N 值等）
- [ ] **Prompt 查看面板**：`/profile` 的可视化展示（Soul + 三类 prompt 展开/折叠）
- [ ] `pystray` 系统托盘：图标、右键菜单（打开/退出）
- [ ] 快捷键：`Ctrl+C` 退出、`Ctrl+L` 清屏、`Ctrl+S` 打开设置
- [ ] 主题系统：至少 Light/Dark 两套

**验收：** UI 完整可交互。冷启动引导→正常对话→调用工具→查看 prompt→压缩→导出，全流程通过 UI 操作完成。

---

### P4 — 打磨与发布

> **目标**：稳定、不崩、能分发。错误处理、边界情况、打包、测试全部收尾。

**交付物：**

- [ ] **PyInstaller 打包**：Windows 单文件 exe，体积 ≤80MB
- [ ] **错误处理全覆盖**：
  - LLM 超时/断连 → 自动重试（最多 3 次）+ 通知
  - 磁盘空间不足 → /export 和 write_file 的前置检查 + 明确提示
  - JSON 文件损坏 → 标记 degraded + 提示用户重建或导入
  - 大文件读写 → 流式处理，不爆内存
  - 并发边界 → Write-Actor 压测
- [ ] **测试**：
  - LLM Gateway 单元测试（mock HTTP）
  - 工具层单元测试
  - Executor function calling 循环集成测试
  - ContextManager / Prompt Manager 集成测试
  - 端到端：冷启动 → 对话 → 工具调用 → 压缩 → 演化 → 导出 → 导入
- [ ] **文档**：README、CONTEXT.md 最终版、用户快速上手
- [ ] **CI/CD**：GitHub Actions 自动打包发布

**验收：** PyInstaller 产物在纯 Windows 环境（无 Python）启动成功。全部测试通过。README 覆盖安装、配置、基本使用。

---

### 不纳入 Phase 规划的内容

以下明确推迟到 Phase 2+，不在 P0-P4 范围内：

- TypeScript 独立桌面窗口壳
- FastAPI 通信适配层
- 插件加载器（importlib + watchdog 热加载）
- OpenClaw / Claude Code MCP 技能包适配
- Anthropic Provider 适配器
