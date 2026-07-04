"""Headless CLI 入口 — 供社区开发非终端 UI 使用。

P4 Batch 1: 预留 API，不实际实现。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from core.config import Config
from core.kernel.agent import AgentKernel


class HeadlessUI:
    """纯 CLI 调用接口示例。

    社区可基于 AgentKernel 构建 Web UI、Desktop GUI 等。
    """

    def __init__(self, config_path: str | None = None):
        self.config = Config.load()
        # 初始化 kernel（需要 provider 等完整对接，此处为骨架）
        self._kernel = None  # AgentKernel(...) in full implementation

    async def send_message(self, user_msg: str, session_id: str | None = None) -> str:
        """(预留) 发送消息，返回 AI 回复文本。"""
        raise NotImplementedError("P4 Batch 2")
