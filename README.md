# Aide Agent

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#平台支持)

> 本地个人 AI 管家 — 不是"能做多少事"，而是"越用越懂你"。

Aide 是一款运行在你电脑上的终端 AI 助手。所有对话和记忆留在本地，隐私不外泄。基于 Textual TUI 框架，纯暗主题，键盘驱动。

## 安装

### 一键安装（推荐）

```powershell
# Windows
irm https://raw.githubusercontent.com/Yeyr43/Aide-Agent/main/install.ps1 | iex
```

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/Yeyr43/Aide-Agent/main/install.sh | bash
```

脚本自动处理：clone → 装依赖 → 配 PATH。重开终端，输入 `aide` 启动。

> 前置条件：[git](https://git-scm.com) + [uv](https://docs.astral.sh/uv/)

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
uv run python shell/main.py
```

## 使用

### 首次启动

冷启动向导引导你完成：语言选择 → 角色模板 → API 配置 → 个性化设置。4 步走完即可开始对话。

### 输入 `/` 打开命令面板

| 命令 | 说明 |
|------|------|
| `/help` | 列出所有命令 |
| `/profile` | 查看当前 Soul + 动态 Prompt |
| `/compact` | 压缩当前会话上下文 |
| `/session list` | 查看历史会话 |
| `/memory` | 查看记忆条目状态 |
| `/tools` | 列出已注册工具 |
| `/plugin list` | 查看插件 |
| `/api add` | 添加 API 配置 |
| `/model` | 切换模型 |
| `/language` | 切换语言 |
| `/export` / `/import` | 导出/导入数据 |
| `/clear` | 清空会话 |
| `/rollback` | 回滚到指定轮次 |

输入 `//` 弹出技能命令面板。

### 系统托盘

启动后自动最小化到托盘。右键托盘图标：

- **显示窗口** — 展开终端界面
- **隐藏到托盘** — 最小化到后台
- **退出** — 完全退出

适合设为开机自启。

## 特性

- **终端原生 TUI** — Textual 全栈框架，纯暗主题 (`#0c0c0c`)，键盘驱动；每回合以树形展示思考/工具/正文，正文实时 Markdown 渲染
- **多模型** — OpenAI 兼容 API / Ollama 本地 / Anthropic，可自定义 base URL 接入任意兼容端点
- **工具系统** — 8 个内置工具（文件读写 / Shell / 全局搜索 / 记忆检索 / 网页抓取）+ 子 agent 委派 + 只读并行执行 / 写串行 + 高危命令拦截
- **优先级队列上下文** — 收集 → 评分 → 打包，Soul/工具/技能/记忆/总览/历史窗口按相关性与 token 预算注入；记忆注入带边界与新鲜度标注，超窗口自动丢弃最老轮次兜底
- **记忆系统** — 手动 `/reflect` 生成结构化记忆 + 会话总览 + 反馈闭环（L1 语言 / L2 长度校验）；`/mem-auto` 可选每轮自动提取（默认关）
- **插件系统** — Python 插件 + Claude Code / OpenClaw / Aide 三格式技能，自动发现加载；9 类生命周期 Hook + 5 项安全预检 + 热重载
- **MCP 协议** — stdio + HTTP Transport，熔断 + 自动重连，工具级错误正确反馈给模型
- **语义搜索** — 词级 TF-IDF + bigram Jaccard + 同义词扩展，时间衰减加权，跨会话全文检索
- **本地隐私** — 所有对话与记忆存于本地 `~/.aide/`，零云端依赖，备份即复制文件夹
- **跨平台** — Windows / macOS / Linux

## 平台支持

| 平台 | 备注 |
|------|------|
| Windows 11 | ✅ 完整支持 |
| macOS | ✅ 需 `pyobjc-framework-Quartz`（一键安装自动处理） |
| Linux | ✅ 需 GTK3 + AppIndicator（`apt install python3-gi gir1.2-gtk-3.0 gir1.2-appindicator3-0.1`） |

## 技术栈

Python 3.13+ · Textual 0.80+ · Pydantic 2 · pystray · Pygments · httpx · ddgs

## 开发

```bash
uv sync                     # 安装依赖
uv run python shell/main.py # 启动应用
uv run pytest tests/ -q     # 运行全部测试（1175 个）
```

架构与设计文档见 [CLAUDE.md](CLAUDE.md)（核心模块职责、关键模式、已知陷阱）与 [CONTEXT.md](CONTEXT.md)（设计演变与批判性收敛记录）。

## 开源协议

MIT License — 详见 [LICENSE](LICENSE)
