"""Tests for core.kernel.tool_executor — web 工具连续失败熔断。

回归：web 计数原为调用总数累计（成功也累计），一轮内 3 次后永久卡死。
改为连续失败熔断：成功清零，仅连续失败达上限才在当前轮拒绝（下轮 reset 恢复）。

注：web 工具可缓存（同轮相同参数复用）。测试用不同参数避免缓存干扰
真实执行，缓存命中按缓存结果判定成败（缓存可能也是失败结果）。
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from core.kernel.tool_executor import ToolExecutor, MAX_WEB_CALLS


def _tool_call(name: str = "web", args: str = "{}") -> dict:
    return {"id": "c1", "type": "function",
            "function": {"name": name, "arguments": args}}


def _make_executor() -> tuple[ToolExecutor, MagicMock]:
    reg = MagicMock()
    reg.execute = AsyncMock()
    return ToolExecutor(reg), reg


async def _run(ex: ToolExecutor, name: str = "web", args: str = "{}") -> tuple[bool, str]:
    ok, rd = await ex._run_one(_tool_call(name, args), MagicMock())
    return ok, rd["content"]


class TestWebCircuitBreaker:
    async def test_success_resets_count(self):
        """成功后计数清零 — 不因调用总数累计而卡死。"""
        ex, reg = _make_executor()
        reg.execute.return_value = "错误：搜索失败"
        await _run(ex, args='{"url":"a"}')
        await _run(ex, args='{"url":"b"}')
        assert ex._web_call_count == 2
        reg.execute.return_value = "正常搜索结果"
        await _run(ex, args='{"url":"c"}')
        assert ex._web_call_count == 0, "成功应清零计数"

    async def test_blocked_after_consecutive_failures(self):
        """连续失败 3 次 → 第 4 次当前轮拒绝。"""
        ex, reg = _make_executor()
        reg.execute.return_value = "错误：请求超时"
        for u in ("a", "b", "c"):
            await _run(ex, args=f'{{"url":"{u}"}}')
        assert ex._web_call_count == MAX_WEB_CALLS
        ok, content = await _run(ex, args='{"url":"d"}')
        assert not ok
        assert "已达上限" in content
        # 拒绝不额外累计（仍是 MAX，非 MAX+1）
        assert ex._web_call_count == MAX_WEB_CALLS

    async def test_failure_then_success_recovers(self):
        """失败累计中某次成功 → 清零，后续可继续调用。"""
        ex, reg = _make_executor()
        reg.execute.side_effect = ["错误：A", "错误：B", "成功内容", "再次调用"]
        await _run(ex, args='{"url":"a"}')   # 失败 → count=1
        await _run(ex, args='{"url":"b"}')   # 失败 → count=2
        await _run(ex, args='{"url":"c"}')   # 成功 → count=0
        assert ex._web_call_count == 0
        await _run(ex, args='{"url":"d"}')   # 恢复后可继续
        assert ex._web_call_count == 0

    async def test_cached_failure_still_counts(self):
        """同轮相同参数命中缓存（失败结果）→ 仍按失败累计，不误清零。"""
        ex, reg = _make_executor()
        reg.execute.return_value = "错误：网络错误"
        # 第一次真实失败（count=1），第二次同参命中缓存（缓存也是失败 → count=2）
        await _run(ex, args='{"url":"same"}')
        assert ex._web_call_count == 1
        await _run(ex, args='{"url":"same"}')  # 命中缓存
        assert ex._web_call_count == 2, "缓存命中失败结果不应清零"

    async def test_non_web_tool_not_counted(self):
        """非 web 工具不影响 web 熔断计数。"""
        ex, reg = _make_executor()
        reg.execute.return_value = "ok"
        await _run(ex, name="read_file", args='{"path":"x"}')
        await _run(ex, name="read_file", args='{"path":"y"}')
        assert ex._web_call_count == 0

    async def test_timeout_counts_as_failure(self):
        """web 工具超时 → 计数 +1（失败）。"""
        ex, reg = _make_executor()
        reg.execute.side_effect = asyncio.TimeoutError
        with patch("core.kernel.tool_executor.TOOL_TIMEOUT", 0.01):
            ok, content = await _run(ex, args='{"url":"a"}')
        assert not ok
        assert ex._web_call_count == 1
