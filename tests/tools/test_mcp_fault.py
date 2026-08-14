"""Tests for core.mcp.fault — CircuitBreaker."""

import pytest
from core.mcp.fault import CircuitBreaker


class TestCircuitBreaker:
    def test_initial_state(self):
        cb = CircuitBreaker(threshold=3)
        assert cb.threshold == 3
        assert not cb.is_tripped("test")
        assert cb.tripped_names == set()

    def test_default_threshold(self):
        cb = CircuitBreaker()
        assert cb.threshold == 3

    def test_on_success_resets_count(self):
        cb = CircuitBreaker(threshold=2)
        cb._failures["test"] = 1
        cb.on_success("test")
        assert cb._failures["test"] == 0

    def test_on_failure_increments(self):
        cb = CircuitBreaker(threshold=3)
        assert not cb.on_failure("test")  # 1/3, not tripped yet
        assert cb._failures["test"] == 1

    def test_on_failure_trips_at_threshold(self):
        cb = CircuitBreaker(threshold=2)
        cb.on_failure("svc")  # 1/2
        result = cb.on_failure("svc")  # 2/2 → tripped
        assert result is True
        assert cb.is_tripped("svc")
        assert "svc" in cb.tripped_names

    def test_on_failure_does_not_trip_below_threshold(self):
        cb = CircuitBreaker(threshold=5)
        cb.on_failure("svc")
        cb.on_failure("svc")
        cb.on_failure("svc")
        assert not cb.is_tripped("svc")

    def test_tripped_only_reports_once(self):
        cb = CircuitBreaker(threshold=2)
        cb.on_failure("x")  # 1
        assert cb.on_failure("x") is True  # 2 → tripped
        assert not cb.on_failure("x")  # 3 → already tripped, no new report

    def test_reset_clears_state(self):
        cb = CircuitBreaker(threshold=2)
        cb.on_failure("svc")
        cb.on_failure("svc")
        assert cb.is_tripped("svc")

        cb.reset("svc")
        assert not cb.is_tripped("svc")
        assert "svc" not in cb._failures
        assert "svc" not in cb.tripped_names

    def test_multiple_services_independent(self):
        cb = CircuitBreaker(threshold=2)
        cb.on_failure("svc-a")
        cb.on_failure("svc-a")
        cb.on_failure("svc-b")

        assert cb.is_tripped("svc-a")
        assert not cb.is_tripped("svc-b")

    def test_reset_unknown_service(self):
        cb = CircuitBreaker()
        cb.reset("nonexistent")  # should not crash

    def test_tripped_names_is_copy(self):
        cb = CircuitBreaker(threshold=1)
        cb.on_failure("x")
        names = cb.tripped_names
        names.add("extra")
        assert "extra" not in cb.tripped_names
