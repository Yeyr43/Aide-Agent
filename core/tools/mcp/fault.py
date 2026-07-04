"""CircuitBreaker — 通用熔断器。

P4 Batch 2: 从 MCPAdapter 提取，可复用于任何需要故障计数的场景。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """熔断器 — 连续失败达到阈值后熔断，手动重置。

    用法:
        breaker = CircuitBreaker(threshold=3)
        if breaker.is_tripped("server-a"):
            return "已熔断"
        try:
            result = await do_work()
            breaker.on_success("server-a")
        except Exception:
            if breaker.on_failure("server-a"):
                logger.warning("熔断!")
    """

    def __init__(self, threshold: int = 3) -> None:
        self.threshold = threshold
        self._failures: dict[str, int] = {}
        self._tripped: set[str] = set()

    def on_success(self, name: str) -> None:
        """工具调用成功 → 重置失败计数。"""
        self._failures[name] = 0

    def on_failure(self, name: str) -> bool:
        """工具调用失败 → 递增计数，达到阈值熔断。返回 True 表示刚触发熔断。"""
        count = self._failures.get(name, 0) + 1
        self._failures[name] = count
        if count >= self.threshold and name not in self._tripped:
            self._tripped.add(name)
            logger.warning(f"[CircuitBreaker] 熔断 {name}（连续 {count} 次失败）")
            return True
        return False

    def is_tripped(self, name: str) -> bool:
        """检查是否已熔断。"""
        return name in self._tripped

    def reset(self, name: str) -> None:
        """手动重置熔断器。"""
        self._tripped.discard(name)
        self._failures.pop(name, None)
        logger.info(f"[CircuitBreaker] 已重置: {name}")

    @property
    def tripped_names(self) -> set[str]:
        """返回所有已熔断的名称。"""
        return set(self._tripped)
