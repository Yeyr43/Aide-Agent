# P0 — 裸对话终端 MVP 实现设计

> **日期**：2026-07-01
> **状态**：已确认
> **关联**：[CONTEXT.md](../../../CONTEXT.md) P0 章节

## 目标

证明核心回路能跑。启动一个 Textual 终端窗口，用户输入消息，LLM 流式回复。

## 架构

```
shell/main.py  →  Textual App  →  LLM Gateway  →  OpenAI/Ollama
                      ↕
                 AsyncIterator[str]  (SSE → 逐 chunk)
```

## 数据流

用户输入 → InputBox → App worker → `await provider.chat(messages)` → AsyncIterator 逐 chunk → `post_message` → MessageList 逐字追加 → 流结束，气泡完成。

## 技术决策

- **包管理**：`uv` + `pyproject.toml`
- **HTTP 客户端**：`httpx`（async，支持 SSE）
- **测试**：P0 无测试，P4 补
- **UI 框架**：Textual（CSS-like 布局 + Rich Markdown 渲染）

## 文件清单

| 文件 | 估计行数 | 职责 |
|------|---------|------|
| `pyproject.toml` | ~20 | uv 项目配置 + 依赖 |
| `config/config.example.json` | ~15 | 配置模板 |
| `config/settings.py` | ~30 | Pydantic Settings |
| `core/__init__.py` | ~3 | 空包 |
| `core/llm_gateway/__init__.py` | ~5 | re-export |
| `core/llm_gateway/provider.py` | ~20 | AbstractProvider Protocol |
| `core/llm_gateway/openai_provider.py` | ~50 | OpenAI SSE 适配 |
| `core/llm_gateway/ollama_provider.py` | ~30 | Ollama SSE 适配 |
| `ui/__init__.py` | ~3 | 空包 |
| `ui/textual_app/__init__.py` | ~3 | 空包 |
| `ui/textual_app/app.py` | ~120 | Textual App 主类 + worker |
| `ui/textual_app/widgets/__init__.py` | ~3 | 空包 |
| `ui/textual_app/widgets/input_box.py` | ~50 | 输入框组件 |
| `ui/textual_app/widgets/message_list.py` | ~80 | 消息流组件 |
| `shell/main.py` | ~25 | 入口 |

~455 行 Python。

## LLM Gateway 设计

### AbstractProvider Protocol

```python
class AbstractProvider(Protocol):
    async def chat(self, messages: list[dict]) -> AsyncIterator[str]:
        """发送消息，返回流式 token 迭代器"""
        ...
```

### OpenAIProvider

- base_url: `https://api.openai.com/v1/chat/completions`
- headers: `Authorization: Bearer $API_KEY`
- body: `{model, messages, stream: true}`
- 用 `httpx` 发送 POST，`response.aiter_lines()` 逐行读 SSE
- SSE 格式：`data: {"choices":[{"delta":{"content":"token"}}]}`

### OllamaProvider

- base_url: `http://localhost:11434/v1/chat/completions`
- 与 OpenAI 共享同一套 SSE 解析逻辑（Ollama 兼容 OpenAI API 格式）
- 适配器只负责拼 base_url 和 headers

## UI 设计

### App 主类

- CSS 布局：全屏，消息区自动滚动，输入区固定底部
- 按键：`Ctrl+C` / `Escape` 退出
- 发送消息时：输入框 disabled + 底部状态 "思考中…"

### InputBox

- 单行/多行输入，Enter 发送，Shift+Enter 换行
- 空消息不发送

### MessageList

- 用户消息右对齐，AI 消息左对齐
- Markdown 渲染（Rich `Markdown` widget）
- 新消息自动滚动到底部
- 流式 chunk 追加到当前 AI 气泡末尾

## 错误处理

| 场景 | 行为 |
|------|------|
| HTTP 4xx/5xx | 红色系统消息显示错误，不崩溃 |
| 网络超时 | "请求超时，请检查网络或重试" |
| SSE 解析异常 | 已接收部分保留 + 错误提示 |
| config.json 缺失 | 提示用户从 config.example.json 复制 |

## 不做

- 无工具调用
- 无上下文管理（不存 session、不读 Soul）
- 无记忆与演化
- 无系统托盘（pystray）
- 无命令系统（`/profile` 等）
- 无测试
- 无 Anthropic Provider
