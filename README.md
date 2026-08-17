# Aide Agent

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#支持范围)

> 本地个人 AI 管家。

Aide 是一个运行在你电脑上的终端 AI 助手。所有对话与记忆都留在本地，隐私不外泄。

## 核心主旨

通过动态 prompt 让模型行为逐渐贴合你的使用习惯。Soul（角色设定）、记忆（偏好 / 工作流 / 长期记忆）、会话总览和反馈闭环共同构成发给模型的 prompt，积累越多越贴合你的习惯。

更新由你手动触发（`/reflect`），生成结果审查确认后生效；`/mem-auto` 是显式开关，默认关闭。

## 技术栈、功能、特点

**技术栈**：Python 3.13+ · Textual（TUI）· pystray · asyncio · JSON 文件存储 · Pygments · httpx · ddgs

**功能**：

- **终端原生界面** — Textual 全栈 TUI，纯暗主题，键盘驱动。每个回合以树形展示思考 / 工具 / 正文，正文实时渲染 Markdown
- **多模型** — OpenAI 兼容 API / Ollama 本地 / Anthropic，可自定义 base URL 接入任意兼容端点
- **工具系统** — 8 个内置工具：文件读写 / Shell / 全局搜索 / 记忆检索 / 网页抓取 / 子 agent 委派。只读工具并行、写工具串行，高危命令拦截，工具结果错误可反馈回模型
- **记忆系统** — `/reflect` 手动生成结构化记忆与会话总览；反馈闭环做语言与长度校验；`/mem-auto` 可选每轮自动提取（默认关）
- **上下文管线** — 收集 → 评分 → 打包，按相关性与 token 预算注入记忆 / 总览 / 历史；窗口超限自动丢弃最老轮次兜底
- **插件系统** — Python 插件 + Claude Code / OpenClaw / Aide 三格式技能，自动发现加载；生命周期 Hook、安全预检、热重载
- **MCP 协议** — stdio + HTTP 传输，熔断与自动重连
- **跨会话搜索** — 词级 TF-IDF + bigram Jaccard + 同义词扩展，时间衰减加权
- **本地存储** — 全部数据存于本地 `~/.aide/`，零云端依赖；备份即复制文件夹

**特点**（有什么说什么）：

- 本地优先，数据不离开你的电脑
- 纯文本驱动，prompt 可读可改，你始终知道它在想什么
- 命令行界面，适合习惯终端、键盘操作的用户
- 不自动联网、不自动改配置——所有敏感动作需要你的参与

## 部署

### 一键安装（推荐）

前置条件：[git](https://git-scm.com) + [uv](https://docs.astral.sh/uv/)

```powershell
# Windows
irm https://raw.githubusercontent.com/Yeyr43/Aide-Agent/main/install.ps1 | iex
```

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/Yeyr43/Aide-Agent/main/install.sh | bash
```

脚本自动处理：clone → 装依赖 → 配 PATH。重开终端，输入 `aide` 启动。

### 二进制下载（无需 Python）

从 [GitHub Releases](https://github.com/Yeyr43/Aide-Agent/releases) 下载对应平台压缩包，解压后运行安装脚本：

| 平台 | 操作 |
|------|------|
| Windows | 右键 `install.ps1` → "使用 PowerShell 运行" |
| macOS | `bash install.sh` |
| Linux | `bash install.sh` |

重开终端，输入 `aide` 启动。

### 源码手动运行

```bash
git clone https://github.com/Yeyr43/Aide-Agent.git
cd Aide-Agent
uv sync
uv run python core/main.py
```

## 使用

### 首次启动

冷启动向导引导你完成：语言选择 → 角色模板 → API 配置 → 个性化设置。4 步走完即可开始对话。

### 日常对话

在输入框直接输入内容，回车发送。`Ctrl+Q` 可强制终止 agent 正在进行的任务（不退出 Aide）。

### 输入 `/` 打开命令面板

| 命令 | 说明 |
|------|------|
| `/help` | 列出所有命令 |
| `/profile` | 查看当前 Soul + 动态 Prompt |
| `/reflect` | 生成结构化记忆 + 会话总览（手动更新 prompt 的入口） |
| `/mem-auto on` | 开启每轮自动记忆提取（默认关闭） |
| `/compact` | 压缩当前会话上下文 |
| `/session list` | 查看历史会话 |
| `/memory` | 查看记忆条目状态 |
| `/tools` | 列出已注册工具 |
| `/plugin list` | 查看插件 |
| `/api add` | 添加 API 配置 |
| `/model` | 切换模型 |
| `/language` | 切换语言 |
| `/export` / `/import` | 导出 / 导入数据 |
| `/clear` | 清空会话 |
| `/rollback` | 回滚到指定轮次 |

输入 `//` 弹出技能命令面板。

### 插件

插件统一放在 **`~/.aide/plugins/`** 目录（每个插件一个子文件夹），放入后自动发现，无需重启。

- **Aide 原生插件**：文件夹含 `aide.plugin.json` + `__init__.py`
- **Claude Code 插件**：文件夹含 `.claude-plugin/plugin.json`
- **OpenClaw 技能**：文件夹含 `SKILL.md`（`name` frontmatter 即插件 id）

> ⚠️ 不要放到 Aide 安装目录或项目根目录——那里不会被扫描。用 `/plugins` 命令可查看当前插件目录路径。

```bash
# 以 OpenClaw 技能为例
mkdir -p ~/.aide/plugins
git clone <仓库> ~/.aide/plugins/your-skill   # 或直接把解压出的文件夹放进来
```

### 系统托盘

启动后自动最小化到托盘。右键托盘图标：

- **显示窗口** — 展开终端界面
- **隐藏到托盘** — 最小化到后台
- **退出** — 完全退出

适合设为开机自启。

## 支持范围

| 平台 | 备注 |
|------|------|
| Windows 11 | ✅ 完整支持 |
| macOS | ✅ 需 `pyobjc-framework-Quartz`（一键安装自动处理） |
| Linux | ✅ 需 GTK3 + AppIndicator（`apt install python3-gi gir1.2-gtk-3.0 gir1.2-appindicator3-0.1`） |

**模型**：OpenAI 兼容 API（含 DeepSeek 等）/ Ollama 本地 / Anthropic。

**明确不做**（保持简单）：Planner、向量搜索、自动摘要（摘要归 `/reflect` 手动触发）、自动修改 prompt（默认关闭）。

## 开源协议

MIT License — 详见 [LICENSE](LICENSE)
