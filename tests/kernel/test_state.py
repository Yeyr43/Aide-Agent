"""Tests for ExecutorState — state machine for FC loop."""

import pytest
from core.kernel.state import ExecutorState


class TestExecutorState:
    def test_ready_value(self):
        assert ExecutorState.READY.value == "ready"

    def test_blocked_value(self):
        assert ExecutorState.BLOCKED.value == "blocked"

    def test_two_states_only(self):
        states = list(ExecutorState)
        assert len(states) == 2

    def test_state_equality(self):
        assert ExecutorState.READY == ExecutorState.READY
        assert ExecutorState.BLOCKED == ExecutorState.BLOCKED
        assert ExecutorState.READY != ExecutorState.BLOCKED

    def test_state_is(self):
        """is should work because Enum members are singletons."""
        assert ExecutorState.READY is ExecutorState("ready")

    def test_from_string(self):
        assert ExecutorState("ready") == ExecutorState.READY
        assert ExecutorState("blocked") == ExecutorState.BLOCKED

    def test_invalid_state_raises(self):
        with pytest.raises(ValueError):
            ExecutorState("invalid")

    def test_ready_is_not_blocked(self):
        """READY state means the loop can continue."""
        state = ExecutorState.READY
        assert state != ExecutorState.BLOCKED
        assert state is not ExecutorState.BLOCKED
