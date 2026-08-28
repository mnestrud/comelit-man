"""Binary sensors: doorbell ringing state and device connectivity."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .coordinator import ComelitLocalConfigEntry, ComelitLocalCoordinator
from .entity import ComelitEntity
from .models import PushEvent

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# How long the ringing sensor stays on after a ring.  The device gives no
# "stopped ringing" signal, so this is a timeout rather than a state read.
RING_ACTIVE_SECONDS = 30


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComelitLocalConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [ComelitConnectivitySensor(coordinator, entry.entry_id)]
    config = coordinator.device_config
    if config and config.doors:
        entities.append(ComelitRingingSensor(coordinator, entry.entry_id))
    async_add_entities(entities)


class ComelitConnectivitySensor(ComelitEntity, BinarySensorEntity):
    """Whether the shared connection to the intercom is healthy."""

    _attr_translation_key = "connectivity"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ComelitLocalCoordinator, entry_id: str) -> None:
        """Initialize the connectivity sensor."""
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_connectivity"

    @property
    def is_on(self) -> bool:
        """True while the coordinator's last update succeeded."""
        return bool(self.coordinator.last_update_success)

    @property
    def available(self) -> bool:
        """Always available — reporting "disconnected" is the point."""
        return True


class ComelitRingingSensor(ComelitEntity, BinarySensorEntity):
    """On for a short window after the doorbell rings."""

    _attr_translation_key = "ringing"
    _attr_device_class = BinarySensorDeviceClass.SOUND

    def __init__(self, coordinator: ComelitLocalCoordinator, entry_id: str) -> None:
        """Initialize the ringing sensor."""
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_ringing"
        self._attr_is_on = False
        self._cancel_clear: object | None = None

    async def async_added_to_hass(self) -> None:
        """Register the push callback."""
        self.async_on_remove(self.coordinator.add_push_callback(self._on_push))

    async def async_will_remove_from_hass(self) -> None:
        """Cancel any pending auto-clear."""
        self._cancel_pending()

    def _cancel_pending(self) -> None:
        if self._cancel_clear is not None:
            self._cancel_clear()  # type: ignore[operator]
            self._cancel_clear = None

    @callback
    def _on_push(self, event: PushEvent) -> None:
        """Turn on for a ring; a missed call or answered call clears it."""
        if event.event_type == "ring":
            self._cancel_pending()
            self._attr_is_on = True
            self.async_write_ha_state()
            self._cancel_clear = async_call_later(self.hass, RING_ACTIVE_SECONDS, self._clear)
        elif event.event_type == "missed_call" and self._attr_is_on:
            self._cancel_pending()
            self._clear()

    @callback
    def _clear(self, _now: object = None) -> None:
        """Clear the ringing state."""
        self._cancel_clear = None
        self._attr_is_on = False
        self.async_write_ha_state()
