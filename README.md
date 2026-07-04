# Aide Agent

> 本地个人 AI 管家 — 不是"能做多少事"，而是"越用越懂你"。

Aide 是一款运行在你电脑上的终端 AI 助手。所有对话和记忆留在本地，隐私不外泄。基于 Textual TUI 框架，支持 OpenAI / Ollama 等多种 LLM 后端。

## 特性

- **终端原生** — 基于 Textual 构建，纯暗主题，键盘驱动
- **多模型支持** — OpenAI 兼容 API、Ollama 本地模型、自定义 base URL
- **12 个内置命令** — `/help` `/profile` `/compact` `/export` `/import` `/session` `/memory` `/tools` `/plugin` `/api` `/model` `/mcp`
- **10 个内置工具** — 文件读写、Shell 执行、Web 搜索、代码搜索、剪贴板读写
- **五层上下文** — Soul 人设 → 工具提示 → 动态 Prompt → 会话总览 → 窗口上下文
- **记忆系统** — 自动截获偏好/工作流/长记忆，LLM 回溯整合
- **插件系统** — Openclaw 兼容 Manifest，支持 Python 插件和 Markdown 技能
- **MCP 协议** — 支持 stdio + HTTP Transport，健康检查 + 自动重连
- **语义搜索** — ONNX embedding（all-MiniLM-L6-v2），TF-IDF + 语义混合排序
- **跨平台** — Windows / macOS / Linux，系统托盘常驻

## 安装

### 源码运行（推荐开发者）

```bash
# 1. 克隆仓库
git clone https://github.com/Yeyr43/Aide-Agent.git
cd Aide-Agent

# 2. 安装依赖
uv sync

# 3. 运行
uv run python shell/main.py
```

> 需要 Python 3.13+ 和 [uv](https://docs.astral.sh/uv/)。

### 二进制下载（推荐普通用户）

从 [GitHub Releases](https://github.com/Yeyr43/Aide-Agent/releases) 下载对应平台的压缩包，解压即可运行。

- **Windows**: 解压后双击 `Aide.exe`
- **macOS**: `./Aide.app/Contents/MacOS/Aide`
- **Linux**: `./Aide`

## 平台支持

| 平台 | 状态 | 备注 |
|------|------|------|
| Windows 11 | ✅ 完整支持 | 默认 SelectorEventLoop |
| macOS | ✅ 完整支持 | pystray 需 `pyobjc-framework-Quartz` |
| Linux | ✅ 完整支持 | pystray 需 GTK3 + AppIndicator |

## 快速开始

### 安装 `aide` 命令（可选）

将 `aide` 加入 PATH，之后在终端输入 `aide` 即可启动：

```powershell
# Windows PowerShell（在项目根目录运行）
powershell -ExecutionPolicy Bypass -File install.ps1

# 安装后重新打开终端
aide    # 启动（自动最小化到托盘，右键托盘图标操作）
```

```bash
# Linux / macOS
sudo cp aide /usr/local/bin/aide
chmod +x /usr/local/bin/aide
```

### 使用方式

```
aide
```

启动后自动最小化到系统托盘。右键托盘图标：
- **显示窗口** — 打开终端界面进行对话
- **隐藏到托盘** — 最小化到后台
- **退出** — 完全退出

适合设为开机自启。

### 冷启动向导

首次启动会进入冷启动向导，引导你：

1. 选择语言（中文/English）
2. 选择角色模板（开发者/写作者/管理者）
3. 配置 LLM（OpenAI / Ollama / 自定义）
4. 个性化设置（称呼/个性/工作风格）

完成后即可开始对话。

### 常用命令

| 命令 | 说明 |
|------|------|
| `/api add <name> <provider> <model> <key>` | 添加 API 配置 |
| `/model <name>` | 切换模型 |
| `/plugin list` | 查看插件状态 |
| `/session list` | 查看历史会话 |
| `/compact` | 压缩当前会话上下文 |
| `/profile` | 查看当前 Soul + Prompt |
| `/help` | 列出所有命令 |

输入 `/` 弹出命令面板，输入 `//` 弹出技能命令面板。

## 技术栈

- **Python 3.13+** — 异步优先（asyncio）
- **Textual 0.80+** — 终端 UI 框架
- **ONNX Runtime** — 语义嵌入（all-MiniLM-L6-v2, 384-dim）
- **Pydantic 2** — 数据验证
- **JSON 文件系统** — 零依赖存储（Write-Actor 并发模型）
- **pystray** — 系统托盘
- **Pygments** — 代码语法高亮

## 项目结构

```
core/           # 内核（零 UI 依赖）
├── kernel/     # Agent 门面 + FC 循环
├── llm_gateway/# OpenAI / Ollama 适配器
├── context/    # 六层上下文管线
├── memory/     # 记忆截获 + 整合
├── commands/   # 命令路由 + 12 个内置命令
├── plugins/    # 插件系统（Manifest + Host + SDK）
├── tools/      # 10 个内置工具 + MCP 适配
└── sessions/   # 会话管理

ui/
├── textual_app/ # Textual TUI（app + screens + widgets）
└── headless.py  # 社区 API 预留

shell/main.py    # 入口
```

## 开源协议

MIT License — 详见 [LICENSE](LICENSE)
