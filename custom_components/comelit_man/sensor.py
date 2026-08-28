"""Sensors: last ring timestamp and ring count.

Both restore across restarts — a doorbell log that resets every time Home
Assistant reloads is not much of a log.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ComelitLocalConfigEntry, ComelitLocalCoordinator
from .entity import ComelitEntity
from .models import PushEvent

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComelitLocalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ring sensors — only when the device has an intercom."""
    coordinator = entry.runtime_data
    config = coordinator.device_config
    if not config or not config.doors:
        return
    async_add_entities(
        [
            ComelitLastRingSensor(coordinator, entry.entry_id),
            ComelitRingCountSensor(coordinator, entry.entry_id),
        ]
    )


class ComelitLastRingSensor(ComelitEntity, RestoreSensor):
    """Timestamp of the most recent doorbell ring."""

    _attr_translation_key = "last_ring"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: ComelitLocalCoordinator, entry_id: str) -> None:
        """Initialize the last-ring sensor."""
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_last_ring"
        self._attr_native_value: datetime | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the previous value, then listen for rings."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and isinstance(last.native_value, datetime):
            self._attr_native_value = last.native_value
        self.async_on_remove(self.coordinator.add_push_callback(self._on_push))

    @callback
    def _on_push(self, event: PushEvent) -> None:
        """Record the ring time."""
        if event.event_type == "ring":
            self._attr_native_value = datetime.now(UTC)
            self.async_write_ha_state()


class ComelitRingCountSensor(ComelitEntity, RestoreSensor):
    """Cumulative doorbell ring count."""

    _attr_translation_key = "ring_count"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: ComelitLocalCoordinator, entry_id: str) -> None:
        """Initialize the ring-count sensor."""
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_ring_count"
        self._attr_native_value: int = 0

    async def async_added_to_hass(self) -> None:
        """Restore the previous count, then listen for rings."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and isinstance(last.native_value, int | float):
            self._attr_native_value = int(last.native_value)
        self.async_on_remove(self.coordinator.add_push_callback(self._on_push))

    @callback
    def _on_push(self, event: PushEvent) -> None:
        """Increment on each ring."""
        if event.event_type == "ring":
            self._attr_native_value = int(self._attr_native_value) + 1
            self.async_write_ha_state()
