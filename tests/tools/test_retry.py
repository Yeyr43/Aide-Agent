"""Tests for core.tools.retry — 重试机制 + 错误分类."""

import asyncio
import pytest

from core.tools.retry import (
    RetryConfig,
    ErrorClass,
    classify_error,
    async_retry,
    DEFAULT_RETRY,
)


class TestErrorClassification:
    def test_timeout_is_transient(self):
        assert classify_error(asyncio.TimeoutError()) == ErrorClass.TRANSIENT

    def test_connection_refused_is_transient(self):
        err = ConnectionRefusedError()
        assert classify_error(err) == ErrorClass.TRANSIENT

    def test_connection_reset_is_transient(self):
        err = ConnectionResetError()
        assert classify_error(err) == ErrorClass.TRANSIENT

    def test_file_not_found_is_permanent(self):
        assert classify_error(FileNotFoundError()) == ErrorClass.PERMANENT

    def test_permission_denied_is_permanent(self):
        assert classify_error(PermissionError()) == ErrorClass.PERMANENT

    def test_value_error_is_permanent(self):
        assert classify_error(ValueError("invalid")) == ErrorClass.PERMANENT

    def test_message_timeout_is_transient(self):
        assert classify_error(Exception("connection timeout")) == ErrorClass.TRANSIENT

    def test_message_rate_limit_is_transient(self):
        assert classify_error(Exception("rate limit exceeded")) == ErrorClass.TRANSIENT

    def test_message_503_is_transient(self):
        assert classify_error(Exception("HTTP 503 Service Unavailable")) == ErrorClass.TRANSIENT

    def test_message_not_found_is_permanent(self):
        assert classify_error(Exception("file not found: /tmp/x")) == ErrorClass.PERMANENT

    def test_message_permission_is_permanent(self):
        assert classify_error(Exception("permission denied")) == ErrorClass.PERMANENT

    def test_unknown_exception_is_unknown(self):
        assert classify_error(Exception("something weird happened")) == ErrorClass.UNKNOWN

    def test_string_error_classification(self):
        assert classify_error("connection timeout") == ErrorClass.TRANSIENT


class TestRetryConfig:
    def test_default_config(self):
        cfg = RetryConfig()
        assert cfg.max_retries == 2
        assert cfg.base_delay == 1.0
        assert cfg.max_delay == 15.0
        assert cfg.backoff_factor == 2.0

    def test_default_retry_global(self):
        assert DEFAULT_RETRY.max_retries == 2


class TestAsyncRetry:
    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await async_retry(fn, tool_name="test")
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_transient_then_success(self):
        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise asyncio.TimeoutError("timed out")
            return "eventually ok"

        result = await async_retry(fn, tool_name="test")
        assert result == "eventually ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_permanent_error_no_retry(self):
        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1
            raise FileNotFoundError("not found")

        result = await async_retry(fn, tool_name="test")
        assert "not found" in result
        assert call_count == 1  # 不重试

    @pytest.mark.asyncio
    async def test_transient_all_retries_exhausted(self):
        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("connection refused")

        result = await async_retry(
            fn,
            config=RetryConfig(max_retries=2, base_delay=0.01),
            tool_name="test",
        )
        assert "connection refused" in result
        assert "已重试 2 次" in result
        assert call_count == 3  # 1 首次 + 2 重试

    @pytest.mark.asyncio
    async def test_zero_retries_no_retry_on_transient(self):
        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("fail")

        result = await async_retry(
            fn,
            config=RetryConfig(max_retries=0),
            tool_name="test",
        )
        assert "fail" in result
        assert call_count == 1
