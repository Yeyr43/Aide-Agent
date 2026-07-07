"""Integration tests for run_shell — verifies actual subprocess execution.

Implementation: subprocess.run + asyncio.to_thread (thread pool), shell=True,
stdin=DEVNULL, UTF-8 decode with locale fallback.
"""

import pytest
from core.tools.builtin.run_shell import execute, _shell_hint


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
    """Output exceeding 100KB should be truncated with a notice."""
    # Generate ~200KB of output (well above MAX_OUTPUT_BYTES=100KB)
    result = await execute({
        "command": 'python -c "print(\'X\' * 200000)"',
        "timeout": 15,
    })
    assert "截断" in result or "truncat" in result.lower()
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


# ── Platform shell hint ──────────────────────────────────────────────────

def test_shell_hint_is_nonempty():
    """_shell_hint() returns a non-empty platform-appropriate string."""
    hint = _shell_hint()
    assert isinstance(hint, str)
    assert len(hint) > 10
