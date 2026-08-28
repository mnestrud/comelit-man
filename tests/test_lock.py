"""Unit tests for the door lock entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.comelit_man.lock import ComelitDoorLock, async_setup_entry
from custom_components.comelit_man.models import DeviceConfig, Door


def _door(index: int = 0, name: str = "Front") -> Door:
    return Door(id=index + 1, index=index, name=name, apt_address="SB100001", output_index=index)


def _coordinator(doors: list[Door] | None = None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.device_name = "Comelit Intercom"
    coordinator.async_open_door = AsyncMock()
    coordinator.device_config = DeviceConfig(apt_address="SB000006", doors=doors if doors is not None else [_door()])
    return coordinator


def _lock(coordinator: MagicMock | None = None, door: Door | None = None) -> ComelitDoorLock:
    coordinator = coordinator or _coordinator()
    lock = ComelitDoorLock.__new__(ComelitDoorLock)
    lock.coordinator = coordinator
    lock._entry_id = "entry"
    lock._door = door or _door()
    lock._attr_unique_id = "entry_lock_0"
    lock._attr_name = "Front"
    return lock


class TestSetup:
    @pytest.mark.asyncio
    async def test_one_lock_per_door(self):
        coordinator = _coordinator([_door(0, "Front"), _door(1, "Gate")])
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.entry_id = "entry"
        added: list = []

        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))

        assert len(added) == 2
        assert {lock.name for lock in added} == {"Front", "Gate"}

    @pytest.mark.asyncio
    async def test_no_config_adds_nothing(self):
        coordinator = _coordinator()
        coordinator.device_config = None
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []

        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))

        assert added == []

    @pytest.mark.asyncio
    async def test_unique_ids_distinct_from_buttons(self):
        """Locks must not collide with the door buttons' unique ids."""
        coordinator = _coordinator([_door(0)])
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.entry_id = "entry"
        added: list = []

        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))

        assert added[0].unique_id == "entry_lock_0"


class TestComelitDoorLock:
    def test_always_reports_locked(self):
        assert _lock().is_locked is True

    @pytest.mark.asyncio
    async def test_open_fires_relay(self):
        lock = _lock()
        await lock.async_open()
        lock.coordinator.async_open_door.assert_awaited_once_with(lock._door)

    @pytest.mark.asyncio
    async def test_unlock_is_open(self):
        lock = _lock()
        await lock.async_unlock()
        lock.coordinator.async_open_door.assert_awaited_once_with(lock._door)

    @pytest.mark.asyncio
    async def test_lock_is_noop(self):
        lock = _lock()
        await lock.async_lock()
        lock.coordinator.async_open_door.assert_not_called()
        assert lock.is_locked is True

    def test_available_while_door_in_config(self):
        assert _lock().available is True

    def test_unavailable_when_door_gone(self):
        coordinator = _coordinator([_door(1, "Other")])
        assert _lock(coordinator, _door(0)).available is False

    def test_unavailable_without_config(self):
        coordinator = _coordinator()
        coordinator.device_config = None
        assert _lock(coordinator).available is False
