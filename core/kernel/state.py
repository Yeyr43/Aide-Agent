"""Executor 状态机 — READY / BLOCKED。"""

from enum import Enum


class ExecutorState(Enum):
    """Function Calling 循环的两种状态。

    READY: 正常执行，LLM 可继续调用工具或回复
    BLOCKED: 工具执行失败，等待用户指令
    """
    READY = "ready"
    BLOCKED = "blocked"
