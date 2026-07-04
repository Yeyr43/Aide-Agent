"""工具执行重试机制。

为 ToolRegistry 提供：
  1. RetryConfig — 重试参数（次数、退避、超时）
  2. classify_error() — 区分瞬态/永久错误
  3. async_retry() — 异步重试执行器

瞬态错误（可重试）：网络失败、超时、临时资源不可用
永久错误（不重试）：文件不存在、权限拒绝、参数无效
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Awaitable

from core.locale import t

logger = logging.getLogger(__name__)


# ── 错误分类 ─────────────────────────────────────────────────────────


class ErrorClass(Enum):
    """错误类别。"""
    TRANSIENT = auto()   # 瞬态错误：可重试
    PERMANENT = auto()   # 永久错误：不应重试
    UNKNOWN = auto()     # 未知：保守重试 1 次


# 瞬态错误关键词（匹配错误消息）
_TRANSIENT_PATTERNS: list[str] = [
    "connection",
    "timeout",
    "temporarily unavailable",
    "too many requests",
    "rate limit",
    "network",
    "dns",
    "resolve",
    "refused",
    "reset by peer",
    "broken pipe",
    "try again",
    "busy",
    "503",
    "502",
    "504",
    "429",
]

# 永久错误关键词
_PERMANENT_PATTERNS: list[str] = [
    "file not found",
    "找不到命令",
    "permission denied",
    "没有读取权限",
    "没有写入权限",
    "invalid",
    "not found",
    "does not exist",
    "is a directory",
    "不能为空",
    "must be",
]


def classify_error(error: Exception | str) -> ErrorClass:
    """根据异常类型和消息分类错误。

    Args:
        error: 异常对象或错误字符串

    Returns:
        ErrorClass 枚举值
    """
    msg = str(error).lower()

    # 1. 按异常类型判断
    if isinstance(error, asyncio.TimeoutError):
        return ErrorClass.TRANSIENT
    if isinstance(error, ConnectionError):
        return ErrorClass.TRANSIENT
    if isinstance(error, OSError):
        # OS 层错误需看具体类型
        import errno
        if hasattr(error, "errno") and error.errno:
            if error.errno in (errno.ECONNREFUSED, errno.ECONNRESET,
                               errno.ETIMEDOUT, errno.ENETUNREACH,
                               errno.ENETDOWN, errno.EHOSTUNREACH):
                return ErrorClass.TRANSIENT
            if error.errno in (errno.ENOENT, errno.EACCES, errno.EPERM,
                               errno.EEXIST, errno.ENOTDIR, errno.EISDIR):
                return ErrorClass.PERMANENT
    if isinstance(error, FileNotFoundError):
        return ErrorClass.PERMANENT
    if isinstance(error, PermissionError):
        return ErrorClass.PERMANENT
    if isinstance(error, ValueError):
        return ErrorClass.PERMANENT

    # 2. 按消息关键词判断
    for pat in _PERMANENT_PATTERNS:
        if pat in msg:
            return ErrorClass.PERMANENT

    for pat in _TRANSIENT_PATTERNS:
        if pat in msg:
            return ErrorClass.TRANSIENT

    return ErrorClass.UNKNOWN


# ── 重试配置 ─────────────────────────────────────────────────────────


@dataclass
class RetryConfig:
    """单次工具调用的重试配置。

    Attributes:
        max_retries: 最大重试次数（不含首次尝试）
        base_delay: 基础等待秒数
        max_delay: 最大等待秒数上限
        backoff_factor: 退避倍数（指数退避）
        retry_on: 允许重试的错误类别集合
    """
    max_retries: int = 2
    base_delay: float = 1.0
    max_delay: float = 15.0
    backoff_factor: float = 2.0
    retry_on: set[ErrorClass] = field(
        default_factory=lambda: {ErrorClass.TRANSIENT, ErrorClass.UNKNOWN}
    )


# 默认配置：瞬态错误重试 2 次，每次指数退避
DEFAULT_RETRY = RetryConfig()


# ── 重试执行器 ───────────────────────────────────────────────────────


async def async_retry(
    fn: Callable[[], Awaitable[str]],
    config: RetryConfig | None = None,
    tool_name: str = "",
) -> str:
    """异步重试执行器。

    对瞬态错误指数退避重试，永久错误立即返回。

    Args:
        fn: 无参异步函数，返回结果字符串
        config: 重试配置，None 使用默认
        tool_name: 工具名（日志用）

    Returns:
        执行结果字符串。若所有重试用尽仍失败，返回错误描述。
    """
    cfg = config or DEFAULT_RETRY
    last_error = ""
    total_attempts = cfg.max_retries + 1

    for attempt in range(total_attempts):
        try:
            result = await fn()
            if attempt > 0:
                logger.info(f"[retry] {tool_name} 第 {attempt} 次重试成功")
            return result
        except Exception as exc:
            error_class = classify_error(exc)
            last_error = str(exc)

            # 永久错误 → 立即返回
            if error_class == ErrorClass.PERMANENT:
                logger.debug(f"[retry] {tool_name} 永久错误，不重试: {last_error[:80]}")
                return t("tool.retry.error", msg=last_error)

            # 最后一次尝试
            if attempt >= cfg.max_retries:
                logger.warning(
                    f"[retry] {tool_name} 已重试 {cfg.max_retries} 次，全部失败: {last_error[:80]}"
                )
                return t("tool.retry.exhausted", msg=last_error, n=cfg.max_retries)

            # 计算退避延迟
            delay = min(cfg.base_delay * (cfg.backoff_factor ** attempt), cfg.max_delay)
            logger.info(
                f"[retry] {tool_name} {error_class.name} 错误，"
                f"{delay:.1f}s 后重试 ({attempt + 1}/{cfg.max_retries}): {last_error[:60]}"
            )
            await asyncio.sleep(delay)

    return t("tool.retry.exhausted", msg=last_error, n=cfg.max_retries)
