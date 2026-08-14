"""Tests for core.errors — unified error type hierarchy."""

import pytest
from core.errors import (
    AideError,
    ProviderError,
    ToolError,
    ConfigError,
    SessionError,
)


class TestAideError:
    def test_base_is_exception(self):
        assert issubclass(AideError, Exception)

    def test_instantiate_minimal(self):
        err = AideError()
        assert str(err) == ""

    def test_instantiate_with_message(self):
        err = AideError("something went wrong")
        assert str(err) == "something went wrong"


class TestProviderError:
    def test_minimal(self):
        err = ProviderError("API 调用失败")
        assert str(err) == "API 调用失败"
        assert err.provider == ""
        assert err.status_code is None
        assert err.retry_after is None

    def test_with_provider_and_status(self):
        err = ProviderError("bad request", provider="openai", status_code=400)
        assert err.provider == "openai"
        assert err.status_code == 400

    def test_with_retry_after(self):
        err = ProviderError("rate limited", provider="deepseek", retry_after=30.0)
        assert err.retry_after == 30.0

    def test_is_aide_error(self):
        err = ProviderError("test")
        assert isinstance(err, AideError)

    def test_can_catch_as_aide_error(self):
        """ProviderError should be catchable as AideError for generic handling."""
        with pytest.raises(AideError):
            raise ProviderError("test")


class TestToolError:
    def test_minimal(self):
        err = ToolError("execution failed")
        assert str(err) == "execution failed"
        assert err.tool == ""
        assert err.arguments == {}

    def test_with_tool_and_args(self):
        err = ToolError("timeout", tool="run_shell", arguments={"cmd": "ls"})
        assert err.tool == "run_shell"
        assert err.arguments == {"cmd": "ls"}

    def test_is_aide_error(self):
        err = ToolError("test")
        assert isinstance(err, AideError)


class TestConfigError:
    def test_minimal(self):
        err = ConfigError("invalid config")
        assert str(err) == "invalid config"

    def test_is_aide_error(self):
        err = ConfigError("test")
        assert isinstance(err, AideError)


class TestSessionError:
    def test_minimal(self):
        err = SessionError("session not found")
        assert str(err) == "session not found"

    def test_is_aide_error(self):
        err = SessionError("test")
        assert isinstance(err, AideError)
