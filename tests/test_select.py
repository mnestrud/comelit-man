"""Unit tests for the camera selector select entity — mocked coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.comelit_man.select import ComelitCameraSelector


def _make_selector(
    *,
    labels: dict[str, str] | None = None,
    active_slot: str | None = None,
) -> tuple[ComelitCameraSelector, MagicMock]:
    """Create a ComelitCameraSelector with a mocked coordinator."""
    coordinator = MagicMock()
    coordinator.camera_labels.return_value = labels or {"cam1": "Camera 1", "cam2": "Camera 2"}
    coordinator.active_camera_slot = active_slot
    coordinator.async_toggle_camera_selection = AsyncMock(return_value=active_slot)
    coordinator.camera_ssrc = 0x11111111
    coordinator.camera_slot_map = {0x11111111: "cam1"}

    select = ComelitCameraSelector.__new__(ComelitCameraSelector)
    select.coordinator = coordinator
    return select, coordinator


class TestOptions:
    def test_options_are_labels(self):
        select, _ = _make_selector(labels={"cam1": "Urbanización", "cam2": "Casa"})
        assert select.options == ["Urbanización", "Casa"]

    def test_current_option_returns_active_label(self):
        select, _ = _make_selector(labels={"cam1": "Urbanización", "cam2": "Casa"}, active_slot="cam2")
        assert select.current_option == "Casa"

    def test_current_option_none_when_unknown(self):
        select, _ = _make_selector(active_slot=None)
        assert select.current_option is None


class TestSelectOption:
    @pytest.mark.asyncio
    async def test_selecting_other_option_toggles(self):
        select, coordinator = _make_selector(labels={"cam1": "A", "cam2": "B"}, active_slot="cam1")
        await select.async_select_option("B")
        coordinator.async_toggle_camera_selection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_selecting_current_option_is_noop(self):
        select, coordinator = _make_selector(labels={"cam1": "A", "cam2": "B"}, active_slot="cam1")
        await select.async_select_option("A")
        coordinator.async_toggle_camera_selection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_option_does_not_toggle(self):
        select, coordinator = _make_selector(active_slot="cam1")
        await select.async_select_option("No existe")
        coordinator.async_toggle_camera_selection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_selecting_when_state_unknown_toggles(self):
        """With no known active camera, any choice advances the selector."""
        select, coordinator = _make_selector(active_slot=None)
        await select.async_select_option("A")
        coordinator.async_toggle_camera_selection.assert_awaited_once()


class TestAttributes:
    def test_extra_state_attributes(self):
        select, _ = _make_selector(active_slot="cam1")
        attrs = select.extra_state_attributes
        assert attrs["active_slot"] == "cam1"
        assert attrs["video_ssrc"] == 0x11111111
        assert attrs["slot_map"] == {"0x11111111": "cam1"}


class TestStateCallback:
    @pytest.mark.asyncio
    async def test_added_to_hass_registers_callback(self):
        select, coordinator = _make_selector(active_slot="cam1")
        coordinator.add_video_state_change_callback.return_value = MagicMock()
        select.hass = MagicMock()
        select.async_write_ha_state = MagicMock()

        await select.async_added_to_hass()

        coordinator.add_video_state_change_callback.assert_called_once()
        assert select._remove_state_callback is not None

    @pytest.mark.asyncio
    async def test_removed_from_hass_unregisters_callback(self):
        select, coordinator = _make_selector(active_slot="cam1")
        remover = MagicMock()
        coordinator.add_video_state_change_callback.return_value = remover
        select.hass = MagicMock()
        select.async_write_ha_state = MagicMock()
        await select.async_added_to_hass()

        await select.async_will_remove_from_hass()
        remover.assert_called_once()


class TestSelectorSetupEntry:
    @pytest.mark.asyncio
    async def test_setup_entry_creates_selector_when_doors_exist(self):
        from custom_components.comelit_man.select import async_setup_entry

        coordinator = MagicMock()
        coordinator.device_config = MagicMock(doors=[MagicMock()])
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.entry_id = "entry_abc"
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))
        assert len(added) == 1
        assert isinstance(added[0], ComelitCameraSelector)

    @pytest.mark.asyncio
    async def test_setup_entry_no_config_adds_nothing(self):
        from custom_components.comelit_man.select import async_setup_entry

        coordinator = MagicMock()
        coordinator.device_config = None
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))
        assert added == []

    @pytest.mark.asyncio
    async def test_setup_entry_no_doors_adds_nothing(self):
        from custom_components.comelit_man.select import async_setup_entry

        coordinator = MagicMock()
        coordinator.device_config = MagicMock(doors=[])
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))
        assert added == []
