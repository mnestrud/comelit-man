"""Unit tests for binary sensor entities."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.comelit_man.binary_sensor import (
    RING_ACTIVE_SECONDS,
    ComelitConnectivitySensor,
    ComelitRingingSensor,
    async_setup_entry,
)
from custom_components.comelit_man.models import DeviceConfig, Door, PushEvent


def _coordinator(*, doors: bool = True, ok: bool = True) -> MagicMock:
    coordinator = MagicMock()
    coordinator.device_name = "Comelit Intercom"
    coordinator.last_update_success = ok
    door_list = [Door(id=1, index=0, name="Front", apt_address="SB100001", output_index=0)] if doors else []
    coordinator.device_config = DeviceConfig(apt_address="SB000006", doors=door_list)
    return coordinator


def _ringing(coordinator: MagicMock | None = None) -> ComelitRingingSensor:
    sensor = ComelitRingingSensor.__new__(ComelitRingingSensor)
    sensor.coordinator = coordinator or _coordinator()
    sensor._entry_id = "entry"
    sensor._attr_unique_id = "entry_ringing"
    sensor._attr_is_on = False
    sensor._cancel_clear = None
    sensor.hass = MagicMock()
    return sensor


def _ring(caller: str = "SB100001") -> PushEvent:
    return PushEvent(event_type="ring", apt_address=caller, timestamp=0.0)


class TestSetup:
    @pytest.mark.asyncio
    async def test_both_sensors_with_doors(self):
        entry = MagicMock()
        entry.runtime_data = _coordinator(doors=True)
        entry.entry_id = "entry"
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        kinds = {type(e).__name__ for e in added}
        assert kinds == {"ComelitConnectivitySensor", "ComelitRingingSensor"}

    @pytest.mark.asyncio
    async def test_connectivity_only_without_doors(self):
        entry = MagicMock()
        entry.runtime_data = _coordinator(doors=False)
        entry.entry_id = "entry"
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert [type(e).__name__ for e in added] == ["ComelitConnectivitySensor"]


class TestConnectivity:
    def test_on_when_update_succeeds(self):
        sensor = ComelitConnectivitySensor(_coordinator(ok=True), "entry")
        assert sensor.is_on is True

    def test_off_when_update_fails(self):
        sensor = ComelitConnectivitySensor(_coordinator(ok=False), "entry")
        assert sensor.is_on is False

    def test_always_available(self):
        """Reporting the disconnect is the point — must not go unavailable."""
        sensor = ComelitConnectivitySensor(_coordinator(ok=False), "entry")
        assert sensor.available is True


class TestRinging:
    def test_starts_off(self):
        assert _ringing().is_on is False

    def test_ring_turns_on_and_schedules_clear(self):
        sensor = _ringing()
        with (
            patch.object(sensor, "async_write_ha_state"),
            patch("custom_components.comelit_man.binary_sensor.async_call_later") as later,
        ):
            sensor._on_push(_ring())
        assert sensor.is_on is True
        later.assert_called_once()
        assert later.call_args[0][1] == RING_ACTIVE_SECONDS

    def test_clear_turns_off(self):
        sensor = _ringing()
        sensor._attr_is_on = True
        with patch.object(sensor, "async_write_ha_state"):
            sensor._clear()
        assert sensor.is_on is False

    def test_second_ring_reschedules(self):
        sensor = _ringing()
        cancel = MagicMock()
        with (
            patch.object(sensor, "async_write_ha_state"),
            patch("custom_components.comelit_man.binary_sensor.async_call_later", return_value=cancel),
        ):
            sensor._on_push(_ring())
            sensor._on_push(_ring())
        cancel.assert_called_once()  # first timer cancelled

    def test_missed_call_clears_early(self):
        sensor = _ringing()
        cancel = MagicMock()
        with (
            patch.object(sensor, "async_write_ha_state"),
            patch("custom_components.comelit_man.binary_sensor.async_call_later", return_value=cancel),
        ):
            sensor._on_push(_ring())
            sensor._on_push(PushEvent(event_type="missed_call", apt_address="SB100001", timestamp=0.0))
        assert sensor.is_on is False
        cancel.assert_called_once()

    def test_other_events_ignored(self):
        sensor = _ringing()
        with patch.object(sensor, "async_write_ha_state") as write:
            sensor._on_push(PushEvent(event_type="door_opened", apt_address="SB100001", timestamp=0.0))
        assert sensor.is_on is False
        write.assert_not_called()

    @pytest.mark.asyncio
    async def test_removal_cancels_pending_timer(self):
        sensor = _ringing()
        cancel = MagicMock()
        sensor._cancel_clear = cancel
        await sensor.async_will_remove_from_hass()
        cancel.assert_called_once()
        assert sensor._cancel_clear is None

    @pytest.mark.asyncio
    async def test_added_to_hass_registers_push_callback(self):
        coordinator = _coordinator()
        remove = MagicMock()
        coordinator.add_push_callback = MagicMock(return_value=remove)
        sensor = _ringing(coordinator)
        with patch.object(sensor, "async_on_remove") as on_remove:
            await sensor.async_added_to_hass()
        coordinator.add_push_callback.assert_called_once_with(sensor._on_push)
        on_remove.assert_called_once_with(remove)
