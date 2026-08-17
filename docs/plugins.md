# Aide 插件指南

插件统一放在 **`~/.aide/plugins/`** 目录，每个插件一个子文件夹。放入后自动发现，无需重启。

```bash
# 查看当前插件目录（可能因 AIDE_HOME 不同而变化）
/plugins
```

Aide 兼容三种插件格式，放入 `~/.aide/plugins/` 后按优先级自动识别：

| 优先级 | 格式 | 目录要求 |
|--------|------|---------|
| 1 | Claude Code | `.claude-plugin/plugin.json` |
| 2 | OpenClaw 技能 | `SKILL.md`（含 `name` frontmatter） |
| 3 | Aide 原生 | `aide.plugin.json` |

---

## 1. Aide 原生插件（`aide.plugin.json`）

Python 插件，可注册工具 / 命令 / 插槽 / 生命周期钩子 / 上下文提供者。

```
my-plugin/
├── aide.plugin.json     # manifest
└── __init__.py          # define_plugin 入口
```

`aide.plugin.json`：

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "entry": "__init__.py"
}
```

`__init__.py` 用 `define_plugin` 注册能力：

```python
from core.plugins.sdk import define_plugin
from core.tools import ToolDefinition

@define_plugin("my-plugin")
def register(api):
    api.register_tool(ToolDefinition(
        name="my_tool",
        description="What the tool does",
        parameters={"type": "object", "properties": {}},
    ))
    # 也支持 register_command / register_hook / register_slot / provide_context
```

## 2. OpenClaw 技能（`SKILL.md`）

纯文本技能，自动注册为 `skill_<plugin>_<skill>` 工具 + `//<plugin>:<skill>` 命令，内容注入 LLM 上下文。

```
openclaw-agent-browser/
└── SKILL.md        # 必须含 name frontmatter
```

`SKILL.md`：

```markdown
---
name: agent-browser
description: Headless browser automation CLI for AI agents.
command: agent-browser   # 可选：对应可执行命令
---

# Browser Automation

使用说明...（该内容会注入模型上下文，也是 skill 工具执行时的返回内容）
```

> **注意**：插件 id 取自 `name` frontmatter（如 `agent-browser`），不一定是目录名。目录名可以带前缀（如 `openclaw-agent-browser`）。

### `command` 字段（可选）

声明该插件对应的**可执行命令**。声明后，`//plugin:skill` 与 skill 工具的输出会附加提示
`可执行命令：<command>（用 run_shell 调用）`，Aide 据此通过 `run_shell` 实际执行插件功能
（提示型——不直通执行，由模型判断何时调用）。

适用于"CLI 型"技能（如 `agent-browser` 是一个真实命令）；纯提示型技能不声明即可。

可选辅助文件（`references/`、`scripts/`、`_meta.json` 等）会被保留但 Aide 不特殊处理。

## 3. Claude Code 插件（`.claude-plugin/plugin.json`）

社区 Claude Code 插件。Aide 提取其 **skills / hooks** 注册；**MCP 服务器与 settings 仅记录日志，需手动配置**（见 `~/.aide/mcp/servers.json`）。

---

## 命名空间隔离

插件注册的工具 / 命令带命名空间前缀，避免冲突：

| 类型 | 规则 | 示例 |
|------|------|------|
| 工具 | `skill_<plugin>_<skill>` | `skill_agent-browser_agent-browser` |
| 技能命令 | `//<plugin>:<skill>` | `//agent-browser:agent-browser` |
| 插件命令 | `//<plugin>:<cmd>` | `//my-plugin:my-cmd` |
| 手动调用工具命令 | `//<plugin>:<tool>` | `//weixin-bot:wx_status` |

## 手动调用插件工具

插件工具默认只由主 agent（LLM）通过 Function Calling 触发。从命令行 **手动调用** 有两种方式：

**方式一：自动注册的工具命令**（输入 `//` 直接选中）

加载 Python 插件时，每个工具自动注册为一条 `//<plugin>:<tool>` 命令：

```
//weixin-bot:wx_status               # 无参数工具
//weixin-bot:wx_send to=xxx text=yyy  # key=value 参数
//weixin-bot:wx_send {"to":"...","text":"..."}  # JSON 参数
```

**方式二：统一调用器** `//plugin`

```
//plugin                          # 列出所有插件的工具
//plugin weixin-bot               # 列出该插件的工具
//plugin weixin-bot wx_status     # 调用工具
//plugin weixin-bot wx_send to=xxx text=yyy
```

参数支持 **JSON** 或 **key=value**（值自动转 true/false/数字）。工具执行走完整路径（安全/超时/hooks），与 LLM 调用等价。必要参数缺失时返回用法提示。

**友好名别名**：插件命令的裸名也会注册为别名——插件帮助里写的 `//wx login` 可直接使用（`//weixin-bot:wx` → `//wx`）。多个插件注册同名裸名时只保留先注册者（命名空间命令不受影响）。

## 安装方式

**方式一：手动放入**（推荐社区分发）

```bash
mkdir -p ~/.aide/plugins
git clone <仓库> ~/.aide/plugins/<your-plugin>   # 或解压 zip 后把文件夹放进来
```

**方式二：主 agent 安装**（`plugin` 工具）

在对话中让 Aide 帮你安装：告诉它本地插件目录或 zip 的路径，Aide 会调用 `plugin` 工具复制到插件目录并加载。

## 状态管理

- `/plugins` — 状态面板（Ready / Needs Setup / Disabled）
- `/plugin load|unload|reload <id>` — 手动管理加载
- `/plugin enable|disable <id>` — 启用 / 禁用（禁用即卸载）
- `~/.aide/config/plugin_states.json` — 状态持久化

## 依赖声明

Aide 原生插件可在 manifest 声明 `requires`（API key / 系统包 / Python 包），状态面板会显示缺失项：

```json
{
  "requires": {
    "api_keys": ["OPENAI_API_KEY"],
    "system_packages": ["git"]
  }
}
```
