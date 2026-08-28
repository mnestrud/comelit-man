"""Unit tests for ComelitDoorbellEvent entity."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.comelit_man.event import ComelitDoorbellEvent
from custom_components.comelit_man.models import PushEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entity() -> ComelitDoorbellEvent:
    """Create a ComelitDoorbellEvent with a mocked coordinator."""
    coordinator = MagicMock()
    coordinator.add_push_callback = MagicMock(return_value=lambda: None)
    entity = ComelitDoorbellEvent.__new__(ComelitDoorbellEvent)
    entity.coordinator = coordinator
    entity._entry_id = "test_entry_id"
    entity._attr_unique_id = "test_entry_id_doorbell"
    entity._events = []

    def _fake_trigger_event(event_type, data=None):  # stub _trigger_event for real HA
        entity._events.append({"event_type": event_type, "data": data or {}})

    entity._trigger_event = _fake_trigger_event
    entity.async_write_ha_state = MagicMock()
    return entity


def _push_event(event_type: str, apt_address: str = "SB000001") -> PushEvent:
    return PushEvent(event_type=event_type, apt_address=apt_address)


# ---------------------------------------------------------------------------
# unique_id / device_info
# ---------------------------------------------------------------------------


class TestDoorbellEventMeta:
    def test_unique_id(self):
        entity = _make_entity()
        assert entity._attr_unique_id == "test_entry_id_doorbell"

    def test_event_types(self):
        from custom_components.comelit_man.event import EVENT_TYPES

        assert "ring" in EVENT_TYPES
        assert "missed_call" in EVENT_TYPES

    def test_device_info_returns_dict(self):
        entity = _make_entity()
        info = entity.device_info
        assert isinstance(info, dict)
        assert ("comelit_man", "test_entry_id") in info.get("identifiers", set())


# ---------------------------------------------------------------------------
# _on_push — event routing
# ---------------------------------------------------------------------------


class TestOnPush:
    def test_doorbell_ring_triggers_event(self):
        entity = _make_entity()
        entity._on_push(_push_event("ring"))

        assert len(entity._events) == 1
        assert entity._events[0]["event_type"] == "ring"

    def test_missed_call_triggers_event(self):
        entity = _make_entity()
        entity._on_push(_push_event("missed_call"))

        assert len(entity._events) == 1
        assert entity._events[0]["event_type"] == "missed_call"

    def test_unknown_event_type_ignored(self):
        entity = _make_entity()
        entity._on_push(_push_event("some_unrecognized_type"))

        assert len(entity._events) == 0

    def test_door_opened_event_fired(self):
        entity = _make_entity()
        entity._on_push(_push_event("door_opened"))

        assert len(entity._events) == 1
        assert entity._events[0]["event_type"] == "door_opened"

    def test_apt_address_included_in_event_data(self):
        entity = _make_entity()
        entity._on_push(_push_event("ring", apt_address="SB000006"))

        assert entity._events[0]["data"]["apt_address"] == "SB000006"

    def test_multiple_events_accumulated(self):
        entity = _make_entity()
        entity._on_push(_push_event("ring"))
        entity._on_push(_push_event("missed_call"))

        assert len(entity._events) == 2
        assert entity._events[0]["event_type"] == "ring"
        assert entity._events[1]["event_type"] == "missed_call"


# ---------------------------------------------------------------------------
# async_added_to_hass — callback registration
# ---------------------------------------------------------------------------


class TestDoorbellEventInit:
    def test_init_sets_unique_id(self):
        coordinator = MagicMock()
        entity = ComelitDoorbellEvent(coordinator, "entry_abc")
        assert entity._attr_unique_id == "entry_abc_doorbell"
        assert entity._entry_id == "entry_abc"
        assert entity.coordinator is coordinator

    @pytest.mark.asyncio
    async def test_async_setup_entry_adds_entity(self):
        from custom_components.comelit_man.event import async_setup_entry

        hass = MagicMock()
        entry = MagicMock()
        entry.runtime_data = MagicMock()
        entry.entry_id = "entry_xyz"
        added: list = []
        await async_setup_entry(hass, entry, lambda ents: added.extend(ents))
        assert len(added) == 1
        assert isinstance(added[0], ComelitDoorbellEvent)


class TestAsyncAddedToHass:
    @pytest.mark.asyncio
    async def test_registers_callback_with_coordinator(self):
        entity = _make_entity()

        # Wire up async_on_remove so we can observe the call
        removed = []
        entity.async_on_remove = lambda fn: removed.append(fn)

        await entity.async_added_to_hass()

        entity.coordinator.add_push_callback.assert_called_once()
        # async_on_remove should have been called with the unsubscribe function
        assert len(removed) == 1

    @pytest.mark.asyncio
    async def test_callback_routes_to_on_push(self):
        """The registered callback must actually forward events to _on_push."""
        entity = _make_entity()

        captured_callback = None

        def fake_add_push_callback(cb):
            nonlocal captured_callback
            captured_callback = cb
            return lambda: None

        entity.coordinator.add_push_callback = fake_add_push_callback

        await entity.async_added_to_hass()

        # Simulate the coordinator calling back
        captured_callback(_push_event("ring"))

        assert len(entity._events) == 1
        assert entity._events[0]["event_type"] == "ring"


# ---------------------------------------------------------------------------
# Coordinator.add_push_callback / _on_push_event dispatch
# ---------------------------------------------------------------------------


class TestCoordinatorPushCallbacks:
    def _make_coordinator(self):
        """Build a minimal coordinator (without HA runtime)."""
        from custom_components.comelit_man.coordinator import (
            ComelitLocalCoordinator,
        )

        coordinator = ComelitLocalCoordinator.__new__(ComelitLocalCoordinator)
        coordinator.hass = MagicMock()
        coordinator._push_callbacks = {}
        return coordinator

    def test_add_push_callback_registers(self):
        coord = self._make_coordinator()
        cb = MagicMock()
        coord.add_push_callback(cb)
        assert cb in coord._push_callbacks

    def test_add_push_callback_returns_remover(self):
        coord = self._make_coordinator()
        cb = MagicMock()
        remove = coord.add_push_callback(cb)
        remove()
        assert cb not in coord._push_callbacks

    def test_on_push_event_calls_all_callbacks(self):
        coord = self._make_coordinator()
        cb1 = MagicMock()
        cb2 = MagicMock()
        coord.add_push_callback(cb1)
        coord.add_push_callback(cb2)

        event = PushEvent(event_type="ring")
        coord._on_push_event(event)

        cb1.assert_called_once_with(event)
        cb2.assert_called_once_with(event)

    def test_on_push_event_continues_on_callback_error(self):
        coord = self._make_coordinator()
        cb1 = MagicMock(side_effect=RuntimeError("boom"))
        cb2 = MagicMock()
        coord.add_push_callback(cb1)
        coord.add_push_callback(cb2)

        event = PushEvent(event_type="ring")
        coord._on_push_event(event)  # must not raise

        cb2.assert_called_once_with(event)

    def test_removed_callback_not_called(self):
        coord = self._make_coordinator()
        cb = MagicMock()
        remove = coord.add_push_callback(cb)
        remove()

        coord._on_push_event(PushEvent(event_type="ring"))

        cb.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 6: dynamic per-caller doorbell entities
# ---------------------------------------------------------------------------


class TestDynamicCallerEntities:
    def _entry_and_coordinator(self):
        from custom_components.comelit_man.models import DeviceConfig

        coordinator = MagicMock()
        coordinator.device_name = "Comelit Intercom"
        coordinator.device_config = DeviceConfig(apt_address="SB000006")
        callbacks = []
        coordinator.add_push_callback = lambda cb: callbacks.append(cb) or (lambda: None)
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.entry_id = "entry"
        return entry, coordinator, callbacks

    @pytest.mark.asyncio
    async def test_entity_created_on_first_unknown_caller(self):
        from custom_components.comelit_man.event import async_setup_entry

        entry, _coord, callbacks = self._entry_and_coordinator()
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert len(added) == 1  # just the main doorbell entity

        callbacks[0](PushEvent(event_type="ring", apt_address="SB100001", timestamp=0.0))
        assert len(added) == 2
        assert added[1].name == "Doorbell SB100001"

    @pytest.mark.asyncio
    async def test_same_caller_creates_once(self):
        from custom_components.comelit_man.event import async_setup_entry

        entry, _coord, callbacks = self._entry_and_coordinator()
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        for _ in range(3):
            callbacks[0](PushEvent(event_type="ring", apt_address="SB100001", timestamp=0.0))
        assert len(added) == 2

    @pytest.mark.asyncio
    async def test_distinct_callers_get_distinct_entities(self):
        from custom_components.comelit_man.event import async_setup_entry

        entry, _coord, callbacks = self._entry_and_coordinator()
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        callbacks[0](PushEvent(event_type="ring", apt_address="SB100001", timestamp=0.0))
        callbacks[0](PushEvent(event_type="ring", apt_address="SB100002", timestamp=0.0))
        assert len(added) == 3
        assert added[1].unique_id != added[2].unique_id

    @pytest.mark.asyncio
    async def test_floor_call_named_floor(self):
        """A ring reporting our own apartment address is a floor call."""
        from custom_components.comelit_man.event import async_setup_entry

        entry, _coord, callbacks = self._entry_and_coordinator()
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        callbacks[0](PushEvent(event_type="ring", apt_address="SB000006", timestamp=0.0))
        assert added[1].name == "Floor call"

    @pytest.mark.asyncio
    async def test_non_ring_events_do_not_create_entities(self):
        from custom_components.comelit_man.event import async_setup_entry

        entry, _coord, callbacks = self._entry_and_coordinator()
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        callbacks[0](PushEvent(event_type="door_opened", apt_address="SB100001", timestamp=0.0))
        callbacks[0](PushEvent(event_type="ring", apt_address="", timestamp=0.0))
        assert len(added) == 1

    @pytest.mark.asyncio
    async def test_initial_ring_replayed_on_add(self):
        """The ring that created the entity must not be lost."""
        from custom_components.comelit_man.event import ComelitCallerEvent

        coordinator = MagicMock()
        coordinator.device_name = "Comelit Intercom"
        coordinator.add_push_callback = MagicMock(return_value=lambda: None)
        initial = PushEvent(event_type="ring", apt_address="SB100001", timestamp=0.0)
        ent = ComelitCallerEvent(coordinator, "entry", "SB100001", False, initial)
        ent.hass = MagicMock()
        ent.async_on_remove = MagicMock()
        with (
            patch.object(ent, "_trigger_event") as trigger,
            patch.object(ent, "async_write_ha_state"),
        ):
            await ent.async_added_to_hass()
        trigger.assert_called_once_with("ring", {"apt_address": "SB100001"})

    def test_only_fires_for_its_own_caller(self):
        from custom_components.comelit_man.event import ComelitCallerEvent

        coordinator = MagicMock()
        coordinator.device_name = "Comelit Intercom"
        ent = ComelitCallerEvent(coordinator, "entry", "SB100001", False)
        with (
            patch.object(ent, "_trigger_event") as trigger,
            patch.object(ent, "async_write_ha_state"),
        ):
            ent._on_push(PushEvent(event_type="ring", apt_address="SB100002", timestamp=0.0))
            trigger.assert_not_called()
            ent._on_push(PushEvent(event_type="ring", apt_address="SB100001", timestamp=0.0))
            trigger.assert_called_once()

    def test_ignores_event_types_outside_the_declared_set(self):
        """A non-doorbell push (e.g. a state ping) never reaches _trigger_event."""
        from custom_components.comelit_man.event import ComelitCallerEvent

        coordinator = MagicMock()
        coordinator.device_name = "Comelit Intercom"
        ent = ComelitCallerEvent(coordinator, "entry", "SB100001", False)
        with (
            patch.object(ent, "_trigger_event") as trigger,
            patch.object(ent, "async_write_ha_state"),
        ):
            ent._on_push(PushEvent(event_type="video_started", apt_address="SB100001", timestamp=0.0))
        trigger.assert_not_called()
