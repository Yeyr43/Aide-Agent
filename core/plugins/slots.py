"""Slot 系统 — 扩展点注册与匹配。"""

from .contract import PluginSlot


class SlotRegistry:
    """Slot 注册表。"""

    def __init__(self) -> None:
        self._slots: dict[str, PluginSlot] = {}

    def declare(self, name: str, description: str = "") -> PluginSlot:
        slot = self._slots.get(name, PluginSlot(name=name, description=description))
        if description and not slot.description:
            slot.description = description
        self._slots[name] = slot
        return slot

    def fill(self, name: str, plugin_id: str, implementation: object) -> bool:
        slot = self._slots.get(name)
        if slot is None:
            return False
        slot.filled_by = plugin_id
        slot.implementation = implementation
        return True

    def unfill(self, plugin_id: str) -> int:
        count = 0
        for slot in self._slots.values():
            if slot.filled_by == plugin_id:
                slot.filled_by = None
                slot.implementation = None
                count += 1
        return count

    def get(self, name: str) -> PluginSlot | None:
        return self._slots.get(name)
