"""Integration tests for run_shell — verifies actual subprocess execution.

Implementation: subprocess.run + asyncio.to_thread (thread pool), shell=True,
stdin=DEVNULL, UTF-8 decode with locale fallback.
"""

import pytest
from core.tools.run_shell import execute, _shell_hint, _compress_repeats


# ── Basic execution ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_shell_echo():
    """Execute a simple echo command and verify output."""
    result = await execute({"command": "echo hello_test_123"})
    assert "hello_test_123" in result


@pytest.mark.asyncio
async def test_run_shell_exit_code():
    """Exit code 0 is included in output header."""
    result = await execute({"command": "echo ok"})
    assert "ok" in result
    assert "退出码: 0" in result or "exit" in result.lower()


@pytest.mark.asyncio
async def test_run_shell_empty_command():
    """Empty command returns error string, never raises."""
    result = await execute({"command": ""})
    assert result  # Should be a non-empty error message


@pytest.mark.asyncio
async def test_run_shell_nonexistent_command():
    """Non-existent command returns error output, never raises."""
    result = await execute({"command": "nonexistent_command_xyz_12345"})
    assert result  # Should not be None, should contain error information


# ── stderr merging ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_shell_stderr_merged():
    """Command writing to stderr should have its output merged into result."""
    # Cross-platform: use Python to write to stderr
    result = await execute({
        "command": 'python -c "import sys; sys.stderr.write(\'err_msg_xyz\')"'
    })
    assert "err_msg_xyz" in result


# ── Non-zero exit code ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_shell_nonzero_exit_shows_code():
    """Non-zero exit code should be reported in output."""
    result = await execute({
        "command": 'python -c "import sys; print(\'failed\'); sys.exit(3)"'
    })
    assert "failed" in result
    # Header should indicate non-zero exit code (locale-dependent)
    assert "3" in result


# ── Encoding fallback ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_shell_binary_output_no_crash():
    """Raw bytes that fail UTF-8 decode must fall back to locale encoding.

    Single bytes 0x80-0x82 are invalid UTF-8 starts (continuation bytes)
    so the first decode attempt fails, exercising the locale fallback path.
    """
    result = await execute({
        "command": 'python -c "import sys; sys.stdout.buffer.write(bytes([0x80, 0x81, 0x82]))"'
    })
    assert isinstance(result, str)
    assert result  # Non-empty: fallback decode produced something


# ── Output truncation ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_shell_truncates_large_output():
    """Output exceeding MAX_OUTPUT_CHARS should be truncated with head-only view."""
    # Generate ~200KB of output (well above MAX_OUTPUT_CHARS=6000)
    result = await execute({
        "command": 'python -c "print(\'X\' * 200000)"',
        "timeout": 15,
    })
    assert "输出过长" in result or "head/tail/grep" in result
    assert len(result) < 200000  # Must be truncated


# ── Timeout parameter validation ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_shell_timeout_zero_uses_default():
    """timeout=0 should be treated as invalid → fall back to DEFAULT_TIMEOUT."""
    result = await execute({"command": "echo still_works", "timeout": 0})
    assert "still_works" in result


@pytest.mark.asyncio
async def test_run_shell_timeout_negative_uses_default():
    """Negative timeout should be treated as invalid → fall back to DEFAULT_TIMEOUT."""
    result = await execute({"command": "echo still_works", "timeout": -5})
    assert "still_works" in result


@pytest.mark.asyncio
async def test_run_shell_timeout_non_numeric_uses_default():
    """Non-numeric timeout should be treated as invalid → fall back to DEFAULT_TIMEOUT."""
    result = await execute({"command": "echo still_works", "timeout": "abc"})
    assert "still_works" in result


@pytest.mark.asyncio
async def test_run_shell_with_timeout():
    """Custom valid timeout is accepted."""
    result = await execute({"command": "echo quick", "timeout": 5})
    assert "quick" in result


# ── Runtime timeout (actual kill) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_shell_timeout_kills_command():
    """Command exceeding timeout must be killed, not hang.

    Uses sleep 10 + timeout=2 to verify subprocess.run actually
    terminates the child process via TimeoutExpired.
    """
    result = await execute({
        "command": 'python -c "import time; time.sleep(10)"',
        "timeout": 2,
    })
    # Must return a timeout error, not hang
    assert "超时" in result or "timeout" in result.lower()


# ── stdin=DEVNULL (interactive command regression) ──────────────────────

@pytest.mark.asyncio
async def test_run_shell_interactive_command_no_hang():
    """Interactive commands must not hang — stdin=DEVNULL gives immediate EOF.

    Regression test for the original run_shell bug: commands like 'date'
    on Windows would prompt for input and hang forever without stdin=DEVNULL.
    """
    result = await execute({
        "command": 'python -c "input()"',
        "timeout": 5,
    })
    # Must complete instantly (input() raises EOFError with stdin=DEVNULL)
    assert result


# ── Repeat compression ───────────────────────────────────────────────────

def test_compress_repeats_empty_input():
    assert _compress_repeats("") == ""
    assert _compress_repeats("single line") == "single line"


def test_compress_repeats_below_threshold():
    """4 repeats of same line < threshold (5) → unchanged."""
    text = "err\n" * 4
    result = _compress_repeats(text.strip())
    assert result == text.strip()
    assert "重复" not in result


def test_compress_repeats_at_threshold():
    """5 repeats = threshold → compressed."""
    text = "err\n" * 5
    result = _compress_repeats(text.strip())
    assert "重复 5 次" in result
    assert result.count("err") == 1  # compressed: one line + count marker (no keyword)


def test_compress_repeats_many_repeats():
    """30 repeats → compressed + only 2 lines in output."""
    text = "error: timeout\n" * 30
    result = _compress_repeats(text.strip())
    assert "重复 30 次" in result
    assert result.count("error: timeout") == 1  # compressed to one occurrence


def test_compress_repeats_mixed_content():
    """Non-repeating content mixed with repeats → only repeats compressed."""
    text = "\n".join(["ok", "err", "err", "err", "err", "err", "ok", "err"])
    result = _compress_repeats(text)
    assert "重复 5 次" in result  # the block of 5 errs compressed
    assert result.count("ok") == 2     # non-repeating lines preserved


def test_compress_repeats_disabled():
    """threshold=0 disables compression."""
    text = "x\n" * 10
    assert _compress_repeats(text, threshold=0) == text


# ── Platform shell hint ──────────────────────────────────────────────────

def test_shell_hint_is_nonempty():
    """_shell_hint() returns a non-empty platform-appropriate string."""
    hint = _shell_hint()
    assert isinstance(hint, str)
    assert len(hint) > 10


class TestRunShellTimeout:
    """回归：外层 wait_for 超时不泄漏命令进程 — 超时必须 kill 进程树。"""

    @pytest.mark.asyncio
    async def test_timeout_returns_message(self):
        result = await execute({
            "command": 'python -c "import time; time.sleep(5)"',
            "timeout": 1,
        })
        assert "超时" in result
        assert "1" in result  # 超时秒数

    @pytest.mark.asyncio
    async def test_timeout_kills_process_tree(self, tmp_path):
        """超时后命令进程（含写文件副作用）必须被杀，不能后台残留。"""
        marker = tmp_path / "done.txt"
        # sleep 30 秒后写文件 —— 若超时 kill 未生效，进程会在后台继续并在 30s 内写文件
        cmd = (
            "python -c \"import time; "
            f"time.sleep(30); open(r'{marker}', 'w').close()\""
        )
        result = await execute({"command": cmd, "timeout": 1})
        assert "超时" in result
        # 等 2s（超过 sleep 前的写文件窗口）——进程应已被 kill，文件不会被写
        import asyncio
        await asyncio.sleep(2)
        assert not marker.exists(), "超时后命令进程仍在后台运行（进程树未被 kill）"

    @pytest.mark.asyncio
    async def test_timeout_parameter_takes_effect(self):
        """传 timeout=5 的短命令正常完成（不被外层硬超时误杀）。"""
        result = await execute({
            "command": 'python -c "import time; time.sleep(1)"',
            "timeout": 5,
        })
        assert "超时" not in result

    @pytest.mark.asyncio
    async def test_invalid_timeout_uses_default(self):
        result = await execute({"command": "echo ok", "timeout": "abc"})
        assert "超时" not in result
        assert "ok" in result


class TestToolTimeoutCoordinator:
    """tool_executor 的 run_shell 外层超时协调（内部 timeout + 2s 缓冲）。"""

    def test_default_is_32(self):
        from core.kernel.tool_executor import _run_shell_tool_timeout
        assert _run_shell_tool_timeout({}) == 32.0

    def test_custom_timeout_adds_buffer(self):
        from core.kernel.tool_executor import _run_shell_tool_timeout
        assert _run_shell_tool_timeout({"timeout": 5}) == 7.0

    def test_clamped_at_max(self):
        from core.kernel.tool_executor import _run_shell_tool_timeout
        assert _run_shell_tool_timeout({"timeout": 100}) == 62.0

    def test_invalid_falls_back(self):
        from core.kernel.tool_executor import _run_shell_tool_timeout
        assert _run_shell_tool_timeout({"timeout": "x"}) == 32.0
