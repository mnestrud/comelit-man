"""Shared base entity class for all Comelit Man entities."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import ComelitLocalCoordinator


class ComelitEntity(CoordinatorEntity[ComelitLocalCoordinator]):
    """Base entity providing coordinator availability, device_info, and has_entity_name."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ComelitLocalCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=self.coordinator.device_name,
        )
