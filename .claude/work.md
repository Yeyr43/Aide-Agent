# Aide Agent 开发工作记录

## 2026-06-29 — v1.0 → v1.1 方案微调

### 技术栈调整
- PySide6 + QWebEngineView + React 19 → **Textual + pystray**（全栈纯 Python，打包体积 500MB+ → ~50MB）
- FastAPI 降为 Phase 2+ 薄适配层，Phase 1 不引入
- Python 3.13+ 保持不变

### 模块精简（12 → 5）
- ❌ **Planner** → Function Calling 循环替代（主流 Agent 一致，闲聊省 50% LLM 调用）
- ❌ **MessageHub** → 职责分给 ContextManager 和 Storage
- ❌ **Harness 硬约束** → 纯 Soul 软引导，安全责任交还用户
- ❌ **Safe Mode** → `/export` `/import` 一键导出导入，备份责任交还用户
- ❌ **Idle Detection** → 单进程 Textual，进程退出即释放资源
- ❌ **Plugin Loader** → Phase 1 仅留 Plugin Protocol 定义

### 核心功能清单修订
- 五层上下文：Soul → 动态 prompt → 会话总览（overview.json）→ 窗口上下文（cache.json）→ 近 8 轮原文
- 会话内三文件：timeline.json（索引）、cache.json（窗口上下文）、overview.json（压缩总览）
- 两条演化管线：上下文管线（实时 + /compress 手动）和 Prompt 管线（用户主导 + /profile update）
- Function Calling 循环 max_turns=5，无冷却期，orphaned 标记
- 冷启动：5 个固定问题，独立表单 UI

### 工程规划（P0-P4）
- **P0** 裸对话终端：Textual + LLM Gateway，能聊天
- **P1** Agent 能力：五工具 + Function Calling 循环
- **P2** 记忆与演化：五层上下文 + 条目截获 + prompt 更新 + 冷启动
- **P3** 前端：完整交互 UI（布局/面板/状态栏/托盘/主题）
- **P4** 打磨与发布：PyInstaller + 测试 + 文档

### 待讨论
- Planner 机制、工具调用流程、WebSocket 协议 → 稍后
- 存储 JSON Schema 细节 → 已开始，待完成

## 2026-06-30 — 简短同步

- 确认 Aide 设计阶段完成，P0 开发为下一步
- 讨论了一个 TypeScript 系统级 Agent（代号 "Aegis"），决定搁置，专注 Aide
- 下次从 P0 开始：Textual TUI + LLM Gateway

## 2026-07-01 — P0 MVP 完成 ✅

### 交付成果
- **13 commits, 18 files, 436 行 Python**
- Textual TUI 聊天终端：输入框 + 消息流（Rich markup 渲染）
- LLM Gateway：OpenAI + Ollama 双 Provider，共享 SSE 解析
- SSE 流式 → 逐 token 渲染到 RichLog
- 配置层：Pydantic Settings + config.example.json
- 错误处理：config 缺失、HTTP 错误、SSE 解析异常全覆盖
- Escape 键优雅退出

### 实现架构
```
shell/main.py → AideApp (Textual) → chat_worker (@work thread=False)
                ↕ AsyncIterator[str]
           LLM Gateway → OpenAIProvider | OllamaProvider
                ↕ _parse_sse_stream (shared)
           httpx.AsyncClient → SSE streaming
```

### 开发过程
- 用 Subagent-Driven Development 流程执行：10 tasks，每 task implementer + reviewer 双检
- 发现并修复了 3 个运行时 bug：RichLog `nl` 参数不存在、`_on_submit` handler 命名错误、`**kwargs` 透传缺失
- 最终审查发现 1 个线程安全问题（`@work thread=False`），已修复

### 下一步
- **P1**：五个内置工具 + Function Calling 循环引擎 + write_file/run_shell/search_memory/web_search + storage.py
- P0 已知局限（P3 修）：无 Shift+Enter 换行、无"思考中…"状态、RichLog 无左右对齐

---

## 2026-07-01（续）— 收尾

- P0 全部完成，13 commits，436 行 Python，smoke test 通过
- 下次从 P1 开始
