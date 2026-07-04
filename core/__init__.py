"""Aide Agent — 本地个人 AI 管家。

core 包包含所有零 UI 依赖的核心逻辑：
- kernel: Agent 内核、FC 循环、状态机
- llm_gateway: LLM Provider 适配层（OpenAI / Ollama / Anthropic）
- context: 上下文管线（组装、摄入、压缩、相关性过滤）
- memory: 记忆管线（截获、条目管理、回溯整合、召回）
- commands: 命令注册中心 + 15 个内置命令
- tools: 工具注册中心 + 10 个内置工具 + MCP 适配
- plugins: 插件系统（契约、宿主、SDK、插槽）
- sessions: 会话管理（创建、列表、删除、恢复）
"""
