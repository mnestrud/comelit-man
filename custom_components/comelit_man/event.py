"""Event entity for doorbell ring notifications."""

from __future__ import annotations

import logging

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ComelitLocalConfigEntry, ComelitLocalCoordinator
from .entity import ComelitEntity
from .models import PushEvent

_LOGGER = logging.getLogger(__name__)

EVENT_TYPES = ["doorbell_ring", "missed_call", "door_opened"]

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComelitLocalConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the doorbell event entity."""
    coordinator = entry.runtime_data
    async_add_entities([ComelitDoorbellEvent(coordinator, entry.entry_id)])


class ComelitDoorbellEvent(ComelitEntity, EventEntity):
    """Event entity that fires on doorbell ring."""

    _attr_translation_key = "doorbell"
    _attr_event_types = EVENT_TYPES
    _attr_device_class = EventDeviceClass.DOORBELL

    def __init__(
        self,
        coordinator: ComelitLocalCoordinator,
        entry_id: str,
    ) -> None:
        """Initialize the doorbell event entity."""
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_doorbell"

    async def async_added_to_hass(self) -> None:
        """Register push callback when added to HA."""
        self.async_on_remove(self.coordinator.add_push_callback(self._on_push))

    @callback
    def _on_push(self, event: PushEvent) -> None:
        """Handle a push event from the device."""
        if event.event_type in EVENT_TYPES:
            self._trigger_event(event.event_type, {"apt_address": event.apt_address})
            self.async_write_ha_state()
            _LOGGER.info("Doorbell event fired: %s", event.event_type)
