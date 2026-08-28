"""Unit tests for ring sensors."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.comelit_man.models import DeviceConfig, Door, PushEvent
from custom_components.comelit_man.sensor import (
    ComelitLastRingSensor,
    ComelitRingCountSensor,
    async_setup_entry,
)


def _coordinator(*, doors: bool = True) -> MagicMock:
    coordinator = MagicMock()
    coordinator.device_name = "Comelit Intercom"
    door_list = [Door(id=1, index=0, name="Front", apt_address="SB100001", output_index=0)] if doors else []
    coordinator.device_config = DeviceConfig(apt_address="SB000006", doors=door_list)
    return coordinator


def _ring() -> PushEvent:
    return PushEvent(event_type="ring", apt_address="SB100001", timestamp=0.0)


def _make(cls, **attrs):
    sensor = cls.__new__(cls)
    sensor.coordinator = _coordinator()
    sensor._entry_id = "entry"
    sensor.hass = MagicMock()
    for k, v in attrs.items():
        setattr(sensor, k, v)
    return sensor


class TestSetup:
    @pytest.mark.asyncio
    async def test_adds_both_sensors(self):
        entry = MagicMock()
        entry.runtime_data = _coordinator(doors=True)
        entry.entry_id = "entry"
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert {type(e).__name__ for e in added} == {"ComelitLastRingSensor", "ComelitRingCountSensor"}

    @pytest.mark.asyncio
    async def test_skips_without_doors(self):
        entry = MagicMock()
        entry.runtime_data = _coordinator(doors=False)
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert added == []


class TestLastRing:
    def test_ring_sets_timestamp(self):
        sensor = _make(ComelitLastRingSensor, _attr_native_value=None)
        with patch.object(sensor, "async_write_ha_state"):
            sensor._on_push(_ring())
        assert isinstance(sensor.native_value, datetime)

    def test_other_events_ignored(self):
        sensor = _make(ComelitLastRingSensor, _attr_native_value=None)
        with patch.object(sensor, "async_write_ha_state") as write:
            sensor._on_push(PushEvent(event_type="missed_call", apt_address="x", timestamp=0.0))
        assert sensor.native_value is None
        write.assert_not_called()

    @pytest.mark.asyncio
    async def test_restores_previous_value(self):
        previous = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
        sensor = _make(ComelitLastRingSensor, _attr_native_value=None)
        sensor.async_on_remove = MagicMock()
        sensor.coordinator.add_push_callback = MagicMock(return_value=MagicMock())
        restored = MagicMock()
        restored.native_value = previous
        with (
            patch.object(CoordinatorEntity, "async_added_to_hass", AsyncMock()),
            patch.object(ComelitLastRingSensor, "async_get_last_sensor_data", AsyncMock(return_value=restored)),
        ):
            await sensor.async_added_to_hass()
        assert sensor.native_value == previous

    @pytest.mark.asyncio
    async def test_no_restore_data_leaves_none(self):
        sensor = _make(ComelitLastRingSensor, _attr_native_value=None)
        sensor.async_on_remove = MagicMock()
        sensor.coordinator.add_push_callback = MagicMock(return_value=MagicMock())
        with (
            patch.object(CoordinatorEntity, "async_added_to_hass", AsyncMock()),
            patch.object(ComelitLastRingSensor, "async_get_last_sensor_data", AsyncMock(return_value=None)),
        ):
            await sensor.async_added_to_hass()
        assert sensor.native_value is None


class TestRingCount:
    def test_increments_on_ring(self):
        sensor = _make(ComelitRingCountSensor, _attr_native_value=0)
        with patch.object(sensor, "async_write_ha_state"):
            sensor._on_push(_ring())
            sensor._on_push(_ring())
        assert sensor.native_value == 2

    def test_other_events_ignored(self):
        sensor = _make(ComelitRingCountSensor, _attr_native_value=5)
        with patch.object(sensor, "async_write_ha_state") as write:
            sensor._on_push(PushEvent(event_type="door_opened", apt_address="x", timestamp=0.0))
        assert sensor.native_value == 5
        write.assert_not_called()

    @pytest.mark.asyncio
    async def test_restores_and_continues_counting(self):
        sensor = _make(ComelitRingCountSensor, _attr_native_value=0)
        sensor.async_on_remove = MagicMock()
        sensor.coordinator.add_push_callback = MagicMock(return_value=MagicMock())
        restored = MagicMock()
        restored.native_value = 41
        with (
            patch.object(CoordinatorEntity, "async_added_to_hass", AsyncMock()),
            patch.object(ComelitRingCountSensor, "async_get_last_sensor_data", AsyncMock(return_value=restored)),
        ):
            await sensor.async_added_to_hass()
        assert sensor.native_value == 41
        with patch.object(sensor, "async_write_ha_state"):
            sensor._on_push(_ring())
        assert sensor.native_value == 42

    @pytest.mark.asyncio
    async def test_non_numeric_restore_ignored(self):
        sensor = _make(ComelitRingCountSensor, _attr_native_value=0)
        sensor.async_on_remove = MagicMock()
        sensor.coordinator.add_push_callback = MagicMock(return_value=MagicMock())
        restored = MagicMock()
        restored.native_value = "garbage"
        with (
            patch.object(CoordinatorEntity, "async_added_to_hass", AsyncMock()),
            patch.object(ComelitRingCountSensor, "async_get_last_sensor_data", AsyncMock(return_value=restored)),
        ):
            await sensor.async_added_to_hass()
        assert sensor.native_value == 0
