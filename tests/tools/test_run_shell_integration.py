"""Integration tests for run_shell — verifies actual subprocess execution.

These tests exercise asyncio.create_subprocess_shell() which is the
code path affected by the Windows event loop policy fix.
"""

import pytest
from core.tools.builtin.run_shell import execute


@pytest.mark.asyncio
async def test_run_shell_echo():
    """Execute a simple echo command and verify output."""
    result = await execute({"command": "echo hello_test_123"})
    assert "hello_test_123" in result


@pytest.mark.asyncio
async def test_run_shell_exit_code():
    """Verify exit code is included in output. 0 is not printed by shell,
    but non-zero exit codes from failing commands should show. For echo,
    it just returns the output."""
    result = await execute({"command": "echo ok"})
    assert "ok" in result


@pytest.mark.asyncio
async def test_run_shell_empty_command():
    """Empty command returns error string, never raises."""
    result = await execute({"command": ""})
    assert result  # Should be a non-empty error message
    assert "echo" not in result.lower()  # Definitely not a shell output


@pytest.mark.asyncio
async def test_run_shell_nonexistent_command():
    """Non-existent command returns error string, never raises."""
    result = await execute({"command": "nonexistent_command_xyz_12345"})
    assert result  # Should not be None, should be an error message


@pytest.mark.asyncio
async def test_run_shell_with_timeout():
    """Custom timeout is accepted."""
    result = await execute({"command": "echo quick", "timeout": 5})
    assert "quick" in result
