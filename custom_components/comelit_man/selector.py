"""Select entity for the entrance panel's camera/door selector relay."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ComelitLocalConfigEntry, ComelitLocalCoordinator
from .entity import ComelitEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComelitLocalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the camera selector select entity."""
    coordinator = entry.runtime_data
    config = coordinator.device_config
    if not config:
        return
    if config.doors:
        async_add_entities([ComelitCameraSelector(coordinator, entry.entry_id)])


class ComelitCameraSelector(ComelitEntity, SelectEntity):
    """Select which entrance camera (and door) the panel streams and opens.

    The panel routes its two physical cameras (and the door strike) through
    a selector relay that advances on every new call — there is no direct
    "pick camera N" command. Selecting an option therefore restarts the
    video session at most once: if the requested camera is already active
    nothing happens, otherwise one toggle advances the relay. Which physical
    camera is streaming is identified by its RTP SSRC; the user labels the
    two slots ("urbanización"/"casa") via the options flow, since the relay
    position — and thus the camera↔door mapping — is not stable across
    power cuts.
    """

    _attr_translation_key = "camera_selector"

    def __init__(
        self,
        coordinator: ComelitLocalCoordinator,
        entry_id: str,
    ) -> None:
        """Initialize the camera selector entity."""
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_camera_selector"
        self._remove_state_callback = None

    @property
    def options(self) -> list[str]:
        """Return the two user-labelled camera slots."""
        labels = self.coordinator.camera_labels()
        return [labels["cam1"], labels["cam2"]]

    @property
    def current_option(self) -> str | None:
        """Return the label of the currently active camera, if known."""
        slot = self.coordinator.active_camera_slot
        if slot is None:
            return None
        return self.coordinator.camera_labels().get(slot)

    async def async_select_option(self, option: str) -> None:
        """Switch to the requested camera with at most one selector toggle."""
        slot = self.coordinator.slot_for_label(option)
        if slot is None:
            _LOGGER.warning("Unknown camera option: %s", option)
            return
        if self.coordinator.active_camera_slot == slot:
            _LOGGER.debug("Camera %s (%s) already selected", option, slot)
            return
        await self.coordinator.async_toggle_camera_selection()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose selector diagnostics (SSRC tracking state)."""
        return {
            "active_slot": self.coordinator.active_camera_slot,
            "video_ssrc": self.coordinator.camera_ssrc,
            "slot_map": {
                f"0x{ssrc:08X}": slot for ssrc, slot in self.coordinator.camera_slot_map.items()
            },
        }

    async def _async_video_state_changed(self) -> None:
        """Refresh HA state when the active camera slot may have changed."""
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register the slot-change listener once added to hass."""
        await super().async_added_to_hass()
        self._remove_state_callback = self.coordinator.add_video_state_change_callback(
            self._async_video_state_changed
        )
        # Pick up whatever the coordinator already learned before we existed
        # (e.g. after an entity-platform reload mid-session).
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the slot-change listener."""
        if self._remove_state_callback is not None:
            self._remove_state_callback()
            self._remove_state_callback = None
