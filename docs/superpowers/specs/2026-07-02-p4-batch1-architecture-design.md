# P4 Batch 1 — 架构升级设计文档

**日期**：2026-07-02  
**状态**：待实现  
**范围**：内核重构 + 插件系统 + 配置迁移 + 目录重组

---

## 1. 目标

P4 是 Aide 周期最长的一个阶段。Batch 1 先修内功：

- **扩展性**：插件系统 + 工具/指令自注册协议
- **模块边界**：kernel ↔ UI 分离，领域驱动目录结构
- **配置迁移**：从项目目录移到 `~/.aide/config/`，注入式使用
- **文件减重**：app.py 640→300 行，assembler.py 350→200 行
- **Openclaw 兼容**：manifest 格式兼容，概念层一致

**不做**：记忆召回精度、会话压缩质量、Prompt 演化版本化、冷启动体验、工具执行可靠性 — 这些留到 Batch 2。

---

## 2. 新目录结构

```
Aide/
├── core/
│   ├── kernel/            # AgentKernel 门面 + FC 循环 + 状态机
│   │   ├── __init__.py
│   │   ├── agent.py       # AgentKernel — 编排门面
│   │   ├── fc_loop.py     # FC 循环（从 executor/loop.py 移入）
│   │   ├── state.py       # ExecutorState
│   │   └── protocols.py   # ExecutorUI + 其他协议
│   │
│   ├── context/           # 上下文管线
│   │   ├── __init__.py
│   │   ├── pipeline.py    # ContextPipeline — 组装入口
│   │   ├── ingester.py    # 上下文写入
│   │   ├── compactor.py   # /compress 压缩
│   │   └── relevance.py   # bigram Jaccard + 话题提取 + 决策检测
│   │
│   ├── tools/             # 工具系统
│   │   ├── __init__.py    # ToolRegistry + ToolDefinition
│   │   ├── protocol.py    # ToolProtocol
│   │   ├── discovery.py   # 自动发现（内置 + 插件）
│   │   ├── builtin/       # 5 个内置工具
│   │   └── mcp/           # MCP 适配器
│   │
│   ├── commands/          # 指令系统（从 ui/ 提出）
│   │   ├── __init__.py    # CommandRegistry + CommandDefinition
│   │   ├── router.py      # 指令路由
│   │   └── builtin/       # 6 个内置指令（含 /plugin）
│   │
│   ├── plugins/           # 插件系统 ✨
│   │   ├── __init__.py
│   │   ├── contract.py    # PluginManifest + PluginAPI
│   │   ├── host.py        # PluginHost — 生命周期管理
│   │   ├── sdk.py         # define_plugin() + 对外 API
│   │   └── slots.py       # Slot 注册与匹配
│   │
│   ├── memory/            # 记忆系统（从 prompt_manager 重构）
│   │   ├── __init__.py
│   │   ├── capture.py     # CaptureEngine
│   │   ├── entries.py     # EntryManager
│   │   ├── updater.py     # PromptUpdater
│   │   ├── tracker.py     # TopicFrequencyTracker
│   │   └── recall.py      # 记忆召回 ✨
│   │
│   ├── sessions/          # 会话管理 ✨
│   │   ├── __init__.py
│   │   └── manager.py     # SessionManager
│   │
│   ├── storage/           # 存储（基本不动）
│   │   └── storage.py     # JsonStore + Write-Actor
│   │
│   └── config.py          # Config dataclass + 分层加载
│
├── ui/
│   ├── textual_app/
│   │   ├── app.py         # AideApp（~150 行目标）
│   │   ├── bridge.py      # UIBridge ✨
│   │   ├── screens/
│   │   ├── widgets/
│   │   └── tray/
│   └── headless.py        # 纯 CLI 模式入口（预留）✨
│
├── tests/
│   ├── kernel/
│   ├── context/
│   ├── plugins/
│   ├── tools/
│   ├── commands/
│   ├── memory/
│   └── sessions/
│
└── shell/main.py          # 入口
```

## 3. 插件系统

### 3.1 三层架构

```
CONTRACT 层  (core/plugins/contract.py)
  - PluginManifest — Openclaw 兼容的 manifest 模型
  - PluginAPI — 插件能调用的注册接口
  - PluginSlot — 扩展点定义

RUNTIME 层  (core/plugins/host.py)
  - PluginHost — 发现 → 校验 → 加载 → 激活 → 卸载
  - 安全门：路径逃逸检测、world-writable 检查
  - 热插拔：文件监听 (watchdog) + 手动触发 (/plugin load|unload)
  - 隔离：单插件加载失败不影响其他

SDK 层  (core/plugins/sdk.py)
  - define_plugin() 装饰器
  - 对外暴露的最小 API surface
```

### 3.2 Openclaw 兼容

- **Manifest**：直接读取 `openclaw.plugin.json`，字段语义一致
- **概念模型**：tool / command / provider / hook / slot 一一对应
- **运行时**：Python 原生实现，TypeScript 插件不支持二进制兼容

Aide 原生 manifest 为 `aide.plugin.json`：

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "description": "一句话描述",
  "kind": "tool",
  "entry": "__init__.py",
  "configSchema": {
    "type": "object",
    "properties": {}
  },
  "requires": { "aide": ">=0.4.0" },
  "slots": [],
  "provides": []
}
```

### 3.3 PluginAPI

```python
class PluginAPI:
    def register_tool(self, tool: ToolDefinition) -> None: ...
    def register_command(self, cmd: CommandDefinition) -> None: ...
    def register_context_provider(self, provider: ContextProvider) -> None: ...
    def fill_slot(self, slot_name: str, implementation: Any) -> None: ...
    def provide_slot(self, slot_name: str) -> None: ...
    def on_startup(self, callback: Callable) -> None: ...
    def on_shutdown(self, callback: Callable) -> None: ...
```

### 3.4 热插拔

```
文件监听 (watchdog)          手动触发 (/plugin load|unload)
       │                              │
       └──────────┬───────────────────┘
                  ▼
        PluginHost.reconcile()
             │
   ┌─────────┼─────────┐
   ▼         ▼         ▼
 load()   unload()  reload()
```

- `/plugin load <id>` — 加载并激活
- `/plugin unload <id>` — dispose + 从 registry 移除
- `/plugin reload <id>` — unload + load
- `/plugin list` — 列出已加载插件

## 4. Kernel ↔ UI 分离

### 4.1 三层模型

```
AideApp (Textual App)       ~150 行
  - Textual 生命周期
  - BINDINGS / 快捷键
  - push_screen / pop_screen
        │
UIBridge                     ~200 行
  - 实现 ExecutorUI Protocol
  - kernel 事件 → Textual widget
  - Textual 输入 → kernel 调用
        │
AgentKernel (纯 Python)      ~120 行
  - 持有 6 个子组件
  - 每个方法 ≤ 10 行，纯编排
```

### 4.2 AgentKernel 子组件

| 组件 | 职责 | 来源 |
|------|------|------|
| `FCLoop` | LLM ↔ 工具交替循环 (max_turns=5) | executor/loop.py |
| `ContextPipeline` | Soul→Prompt→Overview→Cache 管线 | context_manager/assembler.py |
| `SessionManager` | 会话 CRUD + 智能标题 | app.py + ingester.py |
| `CaptureEngine` | 规则引擎截获偏好/工作流/长记忆 | prompt_manager/capture.py |
| `PluginHost` | 插件生命周期 + 热插拔 | 全新 |
| `ToolRegistry` | 工具注册与执行 | tools/__init__.py |
| `CommandRegistry` | 指令注册与路由 | 全新 |

### 4.3 一次 chat 调用的数据流

```
用户输入 → AgentKernel.chat(msg, session_dir, ui)
  │
  ├─1─→ ContextPipeline.assemble(...)
  │       返回: (system_msgs, trimmed_conv)
  │
  ├─2─→ FCLoop.run(system_msgs + trimmed_conv, ui)
  │       返回: updated_conversation
  │
  ├─3─→ Ingester.ingest(...)
  │       写入 timeline.json + cache.json + messages/turn_NNN.json
  │
  ├─4─→ CaptureEngine.capture(...)
  │       规则引擎截获 → 去重 → 写条目
  │
  └──→ ChatResult(conversation, captured, usage)
```

### 4.4 硬约束

- `core/` 任何模块 **不 import textual** — CI 检查强制执行
- 每个子包 `__init__.py` 只 re-export 3~5 个公开符号
- AgentKernel 的每个公开方法 ≤ 10 行

## 5. 配置 & 存储

### 5.1 ~/.aide/ 目录布局

```
~/.aide/
├── config/
│   ├── settings.json      # 用户配置
│   └── defaults.json      # 系统默认值
├── agent/
│   ├── soul.md
│   ├── preferences.md
│   ├── workflows.md
│   ├── long_term_memory.md
│   └── data/
│       ├── preferences.json
│       ├── workflows.json
│       ├── long_term_memory.json
│       └── topic_frequency.json
├── plugins/               # 第三方插件
├── sessions/              # 会话数据
├── backups/               # 数据备份（/backup 指令写入）
│   └── {timestamp}/
│       ├── agent/
│       ├── config/
│       └── sessions/
└── logs/
```

### 5.2 配置加载优先级

```
命令行参数 > 环境变量 (AIDE_*) > settings.json > defaults.json
```

### 5.3 Config 类

```python
@dataclass
class Config:
    llm: LLMConfig
    app: AppConfig
    aide_root: Path = Path.home() / ".aide"

    @classmethod
    def load(cls, cli_args: dict | None = None) -> "Config":
        """分层加载：defaults → settings.json → env → cli_args"""
```

从 Pydantic Settings 切换到 dataclass — 更轻，更本地友好。

### 5.4 存储

JsonStore 保持现有 Write-Actor 实现，唯一改动：接受 `base_dir: Path` 参数，默认 `~/.aide/`。

## 6. 实现顺序

### Step 1：地基迁移
- `config/settings.py` → `core/config.py`
- `~/.aide/` 目录结构搭建
- 旧 import 加 shim
- 验证：应用启动 + 63 测试通过

### Step 2：模块搬家
- executor/ → kernel/, context_manager/ → context/, prompt_manager/ → memory/
- 工具文件 → tools/builtin/
- 指令 → commands/builtin/
- 旧位置留 re-export shim
- 验证：63 测试全绿

### Step 3：新建核心组件（纯增量）
- `core/plugins/` — 完整插件系统
- `core/tools/discovery.py` — 工具自动发现
- `core/tools/protocol.py` — ToolProtocol
- `core/commands/` — CommandRegistry + Router
- `core/sessions/manager.py` — SessionManager
- `core/memory/recall.py` — 记忆召回升级
- `core/kernel/agent.py` — AgentKernel 门面
- `core/kernel/protocols.py` — 协议定义
- `ui/textual_app/bridge.py` — UIBridge
- 对应测试文件
- 验证：全测试 + 新模块单元测试

### Step 4：接线
- app.py 切换到 kernel.chat()
- 会话管理委托给 kernel
- 指令委托给 kernel
- 插件由 kernel 管理
- 旧代码路径保留注释，确认后删除
- 验证：完整手动测试 + 63 测试

### Step 5：清理 & 收尾
- 删除过渡期 shim
- 删除旧目录
- 更新 CLAUDE.md
- 最终全量测试

## 7. 风险 & 缓解

| 风险 | 缓解 |
|------|------|
| 重构引入回归 bug | 每步验证 63 测试 + 手动测试 |
| app.py 拆分漏掉功能 | UIBridge 先做 mirror（行为不变），再逐步切 |
| 插件热插拔状态泄漏 | PluginHost 强制 dispose() 清理；单元测试覆盖 |
| 配置迁移用户数据丢失 | 首次启动自动从旧路径迁移；备份目录 `~/.aide/backups/` |
| MCP 适配迟迟做不完 | 只出 protocol + 一个示例 adapter，不做完整社区接入 |

## 8. 不在此批次范围

- 记忆召回精度（synonym map + 时间衰减）
- 会话压缩质量（分层压缩）
- Prompt 演化质量（版本化 + 冲突检测）
- 冷启动体验（零步冷启动 + 模板）
- 工具执行可靠性（降级/重试）
- MCP 完整适配（只出 protocol + 骨架）
- Windows/Mac 桌面窗口（纯终端，不做窗口化）
