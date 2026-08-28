"""Unit tests for the LastRingImage entity."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.comelit_man.image import LastRingImage, async_setup_entry
from custom_components.comelit_man.models import DeviceConfig, Door, PushEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(*, doors: bool = True, snapshot: bytes | None = None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.hass = MagicMock()
    coordinator.device_name = "Comelit Intercom"
    coordinator.last_ring_snapshot = snapshot
    door_list = [Door(id=1, index=0, name="Front", apt_address="SB100001", output_index=0)] if doors else []
    coordinator.device_config = DeviceConfig(apt_address="SB000006", doors=door_list)
    return coordinator


def _make_entity(coordinator: MagicMock | None = None) -> LastRingImage:
    coordinator = coordinator or _make_coordinator()
    entity = LastRingImage.__new__(LastRingImage)
    entity.coordinator = coordinator
    entity._entry_id = "test_entry"
    entity._attr_unique_id = "test_entry_last_ring_image"
    entity._remove_push_cb = None
    return entity


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


class TestAsyncSetupEntry:
    @pytest.mark.asyncio
    async def test_adds_entity_when_doors_present(self):
        coordinator = _make_coordinator(doors=True)
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.entry_id = "test_entry"
        added: list = []

        await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))

        assert len(added) == 1
        assert isinstance(added[0], LastRingImage)
        assert added[0].unique_id == "test_entry_last_ring_image"

    @pytest.mark.asyncio
    async def test_skips_when_no_doors(self):
        coordinator = _make_coordinator(doors=False)
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []

        await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))

        assert added == []

    @pytest.mark.asyncio
    async def test_skips_when_no_config(self):
        coordinator = _make_coordinator()
        coordinator.device_config = None
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []

        await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))

        assert added == []


# ---------------------------------------------------------------------------
# LastRingImage
# ---------------------------------------------------------------------------


class TestLastRingImage:
    def test_construction_sets_attrs(self):
        coordinator = _make_coordinator()
        entity = LastRingImage(coordinator, "entry123")
        assert entity.unique_id == "entry123_last_ring_image"
        assert entity._attr_translation_key == "last_ring_image"
        assert entity._attr_content_type == "image/jpeg"

    @pytest.mark.asyncio
    async def test_async_image_returns_snapshot(self):
        entity = _make_entity(_make_coordinator(snapshot=b"\xff\xd8jpeg"))
        assert await entity.async_image() == b"\xff\xd8jpeg"

    @pytest.mark.asyncio
    async def test_async_image_none_when_no_snapshot(self):
        entity = _make_entity(_make_coordinator(snapshot=None))
        assert await entity.async_image() is None

    @pytest.mark.asyncio
    async def test_added_to_hass_registers_push_callback(self):
        entity = _make_entity()
        remove = MagicMock()
        entity.coordinator.add_push_callback = MagicMock(return_value=remove)

        await entity.async_added_to_hass()

        entity.coordinator.add_push_callback.assert_called_once_with(entity._on_push)
        assert entity._remove_push_cb is remove

    @pytest.mark.asyncio
    async def test_will_remove_unregisters_push_callback(self):
        entity = _make_entity()
        remove = MagicMock()
        entity._remove_push_cb = remove

        await entity.async_will_remove_from_hass()

        remove.assert_called_once()
        assert entity._remove_push_cb is None

    @pytest.mark.asyncio
    async def test_will_remove_noop_without_callback(self):
        entity = _make_entity()
        await entity.async_will_remove_from_hass()  # must not raise

    def test_on_push_ring_updates_state(self):
        entity = _make_entity()
        with patch.object(entity, "async_write_ha_state") as write_state:
            entity._on_push(PushEvent(event_type="ring", apt_address="SB100001", timestamp=0.0))
        write_state.assert_called_once()
        assert entity._attr_image_last_updated is not None

    def test_on_push_other_events_ignored(self):
        entity = _make_entity()
        with patch.object(entity, "async_write_ha_state") as write_state:
            entity._on_push(PushEvent(event_type="missed_call", apt_address="SB100001", timestamp=0.0))
            entity._on_push(PushEvent(event_type="door_opened", apt_address="SB100001", timestamp=0.0))
        write_state.assert_not_called()
