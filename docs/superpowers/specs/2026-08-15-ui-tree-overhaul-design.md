# UI 树形回合显示改造 — 设计文档

- 日期：2026-08-15
- 状态：已确认，待实现
- 范围：UI 层（`ui/textual_app/`）+ bridge，kernel 零改动

## 背景与动机

当前 UI 是 P4 时代的单栏对话式：所有消息（用户/AI/思考/命令/错误/系统）都用带边框 Panel 堆叠展示。问题在于**信息层级不清**：

- 深度思考混在 AI 正文同一个 Panel 里，灰色斜体，难以区分
- 工具调用完全隐藏（`on_tool_start/done` 为 `pass`），用户不知道 agent 在做什么
- 命令结果与系统通知同为橙色 `#e09030`，视觉无区分
- 全部是边框 Panel，视觉噪声大，看不出主次

本次改造**不改变整体暗色风格与布局**，只改造非正文信息与消息的展示形态：把"边框 Panel 堆叠"改为"**树形回合展示**"，让思考 / 工具 / 正文 / 错误 / 系统各得其所、层级清晰。

## 目标

- 每个 assistant 回合呈现为独立一棵树，节点用 `●` 标记，逻辑块间以 `│` 引导线串联
- 深度思考可折叠；工具调用精简显示（工具名 + 调用参数 + 耗时）；错误/系统/命令单行精简、过长可折叠
- 只改变 `●` 的颜色来区分节点类型，文本一律正常色
- 保留：用户消息气泡框、Markdown 渲染、自动滚动、会话管理

## 非目标（明确不做）

- 不改 kernel / fc_loop / provider —— 工具计时在 UI 桥接层自行完成
- 不做布局重构（侧栏 / 多面板）—— 单栏对话结构保留
- 不做技术栈迁移（仍为 Textual）
- 不做主题系统 / 可配置配色

## 设计

### 1. 整体布局模型

```
┌─────────────────────────────────────┐
│ You  用户消息……                       │
└─────────────────────────────────────┘
        ← 空一行（固定间隔）
● think 深度思考                         ← 树开始
│
● read_file  src/main.py  0.4s
● search_in_files  "TODO"  0.8s
│
● 这里有问题，应该这样子改…                 ← 正文（Markdown）
│
● error 401 …
```

- **每回合独立一棵树**：用户消息框结束后，assistant 回合生成一棵树，引导线到回合末尾截止，不跨回合
- **用户消息**保留气泡框（现状不变：`You` 右对齐、边框 Panel）
- **用户消息框与树之间固定空一行**
- **输入框占满整行**（去掉当前 `#input` 的左右 margin `0 2 1 2`）

### 2. 渲染方式

**不使用左列 gutter 列（独立 Static 画 `│`）**。`│` 和 `●` 直接作为节点文本行里的字面字符：

- 每个节点是一个堆叠的 `Static`，渲染为 `● + 内容`
- 不同逻辑块之间（think → 工具 → 正文 → 错误/系统）插入 `│` 间隔行
- 同一逻辑块内部（如连续多个工具调用）相邻堆叠，不插 `│`

### 3. 节点类型

**统一规则：只有 `●` 的颜色随节点类型变化，文本一律正常色。**

| 节点 | ● 颜色 | 内容 | 折叠 |
|------|--------|------|------|
| think | 灰 `#888888` | `● think` | ✅ 见下方流式规则 |
| 工具 | 暗 `#555555` | `● read_file src/main.py`（工具名 + 调用参数）| ✅ 展开显示参数/结果 |
| 正文 | 亮（默认） | `● 正文` + Markdown | ❌ |
| 错误 | 红 `#cc3333` | `● error 401 …` | ✅（过长时）|
| 系统/命令 | 琥珀 `#e09030` | `● 命令完成：…` 单行摘要 | ✅（过长时）|

细节约定：

- **工具调用**：直接显示工具类型 + 调用内容（`arguments` 的紧凑渲染），如 `read_file src/main.py`、`search_in_files "TODO"`；耗时 `0.4s` 作为暗色尾部元数据（可去掉）
- **think 流式规则**：思考流式到达期间节点保持展开，实时显示思考内容；思考结束后自动折叠为 `● think` 单行
- **命令结果**：`/reflect` 等长文本折叠为一行摘要，展开看全文（复用 think 的折叠机制）

### 4. 交互

- **左键双击** 可折叠节点 → 折叠 / 展开
- **右键点击** 任意节点 → 复制内容到剪贴板（取代现状"双击复制"）
- Enter 键不参与折叠
- 自动滚动保留：树节点追加时自动滚到底部，用户主动向上滚时暂停跟随

### 5. 工具计时的实现（UI 侧，kernel 零改动）

fc_loop 中工具回调是成对且顺序稳定的：`on_tool_start(name, args)` → 之后必有 `on_tool_done(name, result)` 或 `on_tool_error(name, err)`；`gather` 并行执行但每次回调都在同一 `_run_one` 内先 start 后 done；缓存命中路径两者连续调用。因此：

- UIBridge 用 `time.monotonic` 在 `on_tool_start` 记录开始时间
- `on_tool_done` / `on_tool_error` 时计算耗时并写入节点
- 同工具名并行调用：按工具名 FIFO 配对（最旧的未配对 start 与 done 配对）

## 实现

### 文件改动清单

| 文件 | 改动 |
|------|------|
| `ui/textual_app/widgets/message_list.py` | 核心重写：Panel 消息 → 回合树。新增节点类型，`●` 标记 + `│` 引导线，左键双击折叠，右键复制 |
| `ui/textual_app/bridge.py` | 工具回调接线：`on_tool_start`→建工具节点 + 计时，`on_tool_done/error`→收尾打耗时；文本/思考流向树节点；`on_replace_streamed_text` 保留（XML fallback）|
| `ui/textual_app/app.tcss` | 输入框占整行；用户框与树空一行；`●` 按类型配色；`│` 间隔行样式 |
| `ui/textual_app/app.py` | 少量配合改动（用户消息空隙等）|
| `core/locale_data/runtime.json` | 若需要节点标签文案（如折叠提示），尽量不加 |
| `tests/ui/test_bridge.py` | 更新：工具 noop 测试 → 断言工具节点生成；文本流向树节点 |

### 回合树结构

每个 assistant 回合 = 一个 `TurnTree`（`Vertical` 容器），子节点顺序：

```
TurnTree
├─ ThinkNode   (● think，灰●，可折叠，流式展开→结束自动折叠)
├─ ToolNode    (● read_file src/main.py，暗●，展开显示参数/结果)
├─ BodyNode    (● 正文，亮●，Markdown)
├─ ErrorNode   (● error …, 红●，可折叠)
└─ SystemNode  (● 系统/命令, 琥珀●，可折叠)
```

### 流式渲染流程

1. thinking 到达 → 建 ThinkNode（流式期间展开缓冲思考文本）
2. thinking 结束 → ThinkNode 自动折叠为 `● think`
3. 工具执行中 → 建 ToolNode（start 时单行显示工具名+参数）
4. 正文到达 → 建 BodyNode 流式 update
5. 回合结束 `finish` → 收尾（工具耗时已写入）

## 测试

- `tests/ui/test_bridge.py`：更新 `test_tool_start_and_done_are_noops`（现断言 noop → 改为断言生成工具节点）；文本/思考流向断言适配
- 新增 MessageList 单元测试（如节点生成、折叠切换、流式收尾）—— 不依赖完整 Textual App，纯 widget 方法级测试
- 回归：`uv run pytest tests/ -q`（1045 个既有测试全绿）

## 待实现时确认的细节（非阻塞）

- `│` 间隔行的精确位置（逻辑块划分规则）在实现时按视觉效果微调
- 工具参数紧凑渲染的具体格式（`read_file src/main.py` vs `read_file {"path": "..."}`）
- 耗时 `0.4s` 尾部的取舍（保留为暗色元数据，或去掉）
