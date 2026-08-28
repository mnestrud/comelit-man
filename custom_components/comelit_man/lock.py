"""Lock entities for Comelit doors and actuators.

These coexist with the door buttons rather than replacing them: existing
automations and the Lovelace cards drive the buttons, and the buttons carry
the stop-video-after-open behaviour.  Locks add the semantics HomeKit, voice
assistants, and the lock card expect.
"""

from __future__ import annotations

import logging

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ComelitLocalConfigEntry, ComelitLocalCoordinator
from .entity import ComelitEntity
from .models import Door

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComelitLocalConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a lock entity per door."""
    coordinator = entry.runtime_data
    config = coordinator.device_config
    if not config:
        return
    async_add_entities(ComelitDoorLock(coordinator, door, entry.entry_id) for door in config.doors)


class ComelitDoorLock(ComelitEntity, LockEntity):
    """Momentary door release exposed as a lock.

    The intercom drives a momentary relay and reports no state, so the entity
    always reads as locked: there is nothing to hold open and nothing to read
    back.  `open` fires the relay; `lock` is a no-op so that voice assistants
    and the lock card have a coherent model.
    """

    _attr_translation_key = "door"
    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(
        self,
        coordinator: ComelitLocalCoordinator,
        door: Door,
        entry_id: str,
    ) -> None:
        """Initialize the door lock entity."""
        super().__init__(coordinator, entry_id)
        self._door = door
        self._attr_unique_id = f"{entry_id}_lock_{door.index}"
        self._attr_name = door.name

    @property
    def is_locked(self) -> bool:
        """Always True — a momentary relay reports no state to read back."""
        return True

    @property
    def available(self) -> bool:
        """Available while the door is still present in the device config."""
        config = self.coordinator.device_config
        if config is None:
            return False
        return any(d.index == self._door.index for d in config.doors)

    async def async_lock(self, **kwargs: object) -> None:
        """No-op — the relay is momentary and re-locks itself."""

    async def async_unlock(self, **kwargs: object) -> None:
        """Unlocking a momentary relay means releasing it."""
        await self.async_open(**kwargs)

    async def async_open(self, **kwargs: object) -> None:
        """Fire the door relay."""
        _LOGGER.info("Opening door via lock entity: %s", self._door.name)
        await self.coordinator.async_open_door(self._door)
