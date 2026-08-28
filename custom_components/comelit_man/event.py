"""Event entity for doorbell ring notifications."""

from __future__ import annotations

import logging

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ComelitLocalConfigEntry, ComelitLocalCoordinator
from .entity import ComelitEntity
from .models import PushEvent
from .vip_listener import address_matches

_LOGGER = logging.getLogger(__name__)

EVENT_TYPES = ["ring", "missed_call", "door_opened"]

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComelitLocalConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the doorbell event entity and per-caller entities on demand.

    The main doorbell entity fires for every ring regardless of origin, so
    existing automations keep working.  Systems with more than one call
    origin (a second entrance, or a floor door on kit firmware) additionally
    get an entity per origin, created the first time that origin rings —
    the address book does not always list them up front.
    """
    coordinator = entry.runtime_data
    async_add_entities([ComelitDoorbellEvent(coordinator, entry.entry_id)])

    known: set[str] = set()

    @callback
    def _on_push(event: PushEvent) -> None:
        if event.event_type != "ring" or not event.apt_address:
            return
        caller = event.apt_address
        if caller in known:
            return
        known.add(caller)
        config = coordinator.device_config
        # A ring reporting our own apartment address is a floor call
        # (vip_listener rewrites it from the origin tag).
        is_floor = bool(config and address_matches(caller, config.apt_address))
        async_add_entities([ComelitCallerEvent(coordinator, entry.entry_id, caller, is_floor, event)])

    entry.async_on_unload(coordinator.add_push_callback(_on_push))


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


class ComelitCallerEvent(ComelitEntity, EventEntity):
    """Event entity for one specific call origin (entrance panel or floor door).

    Created dynamically on the first ring from an origin, so its very first
    ring is not lost: the payload that triggered creation is replayed once the
    entity is registered with Home Assistant.
    """

    _attr_event_types = EVENT_TYPES
    _attr_device_class = EventDeviceClass.DOORBELL

    def __init__(
        self,
        coordinator: ComelitLocalCoordinator,
        entry_id: str,
        caller: str,
        is_floor: bool,
        initial_event: PushEvent | None = None,
    ) -> None:
        """Initialize a per-caller doorbell event entity."""
        super().__init__(coordinator, entry_id)
        self._caller = caller
        self._initial_event = initial_event
        self._attr_unique_id = f"{entry_id}_doorbell_{caller}"
        self._attr_name = "Floor call" if is_floor else f"Doorbell {caller}"

    async def async_added_to_hass(self) -> None:
        """Register the push callback and replay the ring that created us."""
        self.async_on_remove(self.coordinator.add_push_callback(self._on_push))
        if self._initial_event is not None:
            event, self._initial_event = self._initial_event, None
            self._on_push(event)

    @callback
    def _on_push(self, event: PushEvent) -> None:
        """Fire only for events from this caller."""
        if event.event_type not in EVENT_TYPES:
            return
        if not address_matches(event.apt_address, self._caller):
            return
        self._trigger_event(event.event_type, {"apt_address": event.apt_address})
        self.async_write_ha_state()
