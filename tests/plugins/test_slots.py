"""Tests for SlotRegistry — extension point registration."""

import pytest
from core.plugins.slots import SlotRegistry
from core.plugins.contract import PluginSlot


class TestSlotRegistry:
    @pytest.fixture
    def registry(self):
        return SlotRegistry()

    def test_declare_new_slot(self, registry):
        slot = registry.declare("on_startup", "Startup hook")
        assert slot.name == "on_startup"
        assert slot.description == "Startup hook"
        assert slot.filled_by is None
        assert slot.implementation is None

    def test_declare_existing_preserves_if_no_desc(self, registry):
        registry.declare("hook", "original")
        slot = registry.declare("hook")
        assert slot.description == "original"  # preserved

    def test_declare_updates_empty_description(self, registry):
        registry.declare("hook")
        slot = registry.declare("hook", "new desc")
        assert slot.description == "new desc"

    def test_fill_existing_slot(self, registry):
        registry.declare("on_startup")
        result = registry.fill("on_startup", "my-plugin", lambda: None)
        assert result is True
        slot = registry.get("on_startup")
        assert slot.filled_by == "my-plugin"
        assert slot.implementation is not None

    def test_fill_nonexistent_slot(self, registry):
        result = registry.fill("nonexistent", "p", lambda: None)
        assert result is False

    def test_unfill_removes_plugin_implementations(self, registry):
        registry.declare("slot_a")
        registry.declare("slot_b")
        registry.declare("slot_c")
        registry.fill("slot_a", "plugin-x", lambda: None)
        registry.fill("slot_b", "plugin-x", lambda: None)
        registry.fill("slot_c", "plugin-y", lambda: None)

        count = registry.unfill("plugin-x")
        assert count == 2

        # slot_a and slot_b should be unfilled
        assert registry.get("slot_a").filled_by is None
        assert registry.get("slot_b").filled_by is None
        # slot_c should still be filled by plugin-y
        assert registry.get("slot_c").filled_by == "plugin-y"

    def test_unfill_unknown_plugin(self, registry):
        registry.declare("slot")
        count = registry.unfill("unknown-plugin")
        assert count == 0

    def test_get_existing(self, registry):
        registry.declare("test_slot", "A test slot")
        slot = registry.get("test_slot")
        assert slot is not None
        assert slot.name == "test_slot"

    def test_get_nonexistent(self, registry):
        assert registry.get("missing") is None
