"""Unit tests for button entities."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.comelit_man.button import (
    ComelitAnswerDoorbellButton,
    ComelitDoorButton,
    ComelitStartVideoButton,
    ComelitStopVideoButton,
    ComelitSwitchCameraButton,
)
from custom_components.comelit_man.models import Door


def _make_stop_button() -> ComelitStopVideoButton:
    """Create a ComelitStopVideoButton with a mocked coordinator."""
    coordinator = MagicMock()
    coordinator.async_stop_video = AsyncMock()
    coordinator.request_video_stop = MagicMock()

    btn = ComelitStopVideoButton.__new__(ComelitStopVideoButton)
    btn.coordinator = coordinator
    return btn


class TestComelitStopVideoButton:
    @pytest.mark.asyncio
    async def test_press_calls_request_video_stop_before_async_stop(self):
        """request_video_stop() must be called before async_stop_video().

        This ensures the prewarm loop sees the flag and aborts before
        async_stop_video() cancels it, preventing a race where a new session
        is established right after stop.
        """
        btn = _make_stop_button()
        call_order = []

        btn.coordinator.request_video_stop = MagicMock(side_effect=lambda: call_order.append("request_stop"))
        btn.coordinator.async_stop_video = AsyncMock(side_effect=lambda: call_order.append("async_stop"))

        await btn.async_press()

        assert call_order == ["request_stop", "async_stop"]

    @pytest.mark.asyncio
    async def test_press_does_not_raise_on_exception(self):
        """async_press must not propagate exceptions."""
        btn = _make_stop_button()
        btn.coordinator.async_stop_video = AsyncMock(side_effect=RuntimeError("stop failed"))

        await btn.async_press()  # should not raise


# ---------------------------------------------------------------------------
# ComelitDoorButton
# ---------------------------------------------------------------------------


def _make_door_button() -> ComelitDoorButton:
    coordinator = MagicMock()
    coordinator.async_open_door = AsyncMock()
    coordinator.request_video_stop = MagicMock()
    coordinator.async_stop_video = AsyncMock()
    coordinator.video_session = None

    door = Door(id=0, index=0, name="Main Gate", apt_address="SB100001", output_index=0)
    btn = ComelitDoorButton.__new__(ComelitDoorButton)
    btn.coordinator = coordinator
    btn._door = door
    btn.hass = MagicMock()
    return btn


class TestComelitDoorButton:
    @pytest.mark.asyncio
    async def test_press_calls_open_door(self):
        """async_press calls coordinator.async_open_door with the door."""
        btn = _make_door_button()
        await btn.async_press()
        btn.coordinator.async_open_door.assert_awaited_once_with(btn._door)

    @pytest.mark.asyncio
    async def test_press_does_not_schedule_stop_when_no_session(self):
        """No delayed stop is scheduled when no video session is active."""
        btn = _make_door_button()
        btn.coordinator.video_session = None

        await btn.async_press()

        btn.coordinator.restart_stop_video_delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_press_schedules_delayed_stop_when_session_active(self):
        """A delayed stop task is scheduled when a video session is active."""
        btn = _make_door_button()
        session = MagicMock()
        session.active = True
        btn.coordinator.video_session = session

        # Capture and close the coroutine so it doesn't leak
        created_coros = []

        def capture_delay(coro):
            created_coros.append(coro)

        btn.coordinator.restart_stop_video_delay = MagicMock(side_effect=capture_delay)

        await btn.async_press()

        assert len(created_coros) == 1
        # Close the coroutine to prevent "was never awaited" warning
        created_coros[0].close()

    @pytest.mark.asyncio
    async def test_press_does_not_raise_on_door_open_failure(self):
        """async_press swallows exceptions from async_open_door."""
        btn = _make_door_button()
        btn.coordinator.async_open_door = AsyncMock(side_effect=RuntimeError("timeout"))

        await btn.async_press()  # must not raise

    @pytest.mark.asyncio
    async def test_stop_video_after_delay_stops_active_session(self):
        """_stop_video_after_delay calls async_stop_video once the delay elapses.

        The user-initiated-stop flag is set earlier, in async_press, so this
        method only needs to invoke async_stop_video after the sleep.
        """
        btn = _make_door_button()
        session = MagicMock()
        session.active = True
        btn.coordinator.video_session = session

        btn.coordinator.async_stop_video = AsyncMock()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await btn._stop_video_after_delay(10)

        btn.coordinator.async_stop_video.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_press_marks_stop_user_initiated_before_delay(self):
        """Door press must call request_video_stop immediately so a CALL_END
        arriving before the 10s delay isn't auto-restarted.
        """
        btn = _make_door_button()
        session = MagicMock()
        session.active = True
        btn.coordinator.video_session = session

        # Silence the scheduled background task
        btn.coordinator.restart_stop_video_delay = MagicMock()

        btn.coordinator.request_video_stop = MagicMock()

        await btn.async_press()

        btn.coordinator.request_video_stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_video_after_delay_noop_when_session_gone(self):
        """_stop_video_after_delay does nothing if session ends before the delay."""
        btn = _make_door_button()
        btn.coordinator.video_session = None  # session already gone

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await btn._stop_video_after_delay(10)

        btn.coordinator.async_stop_video.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_repeat_press_cancels_pending_stop_timer(self):
        """Re-pressing the door button cancels the earlier press's timer.

        With the old arm-only logic, press #1's 10 s timer expired 0.35 s
        after press #3 and tore the session down mid-strike (field log
        2026-11-17 21:13: the third press's door-open never fired the relay).
        """
        from custom_components.comelit_man.coordinator import ComelitLocalCoordinator

        fake = MagicMock()
        fake.hass = MagicMock()
        fake.config_entry = MagicMock()
        created = []

        def fake_create(hass, coro, name):
            created.append(coro)
            return MagicMock(done=MagicMock(return_value=False))

        fake.config_entry.async_create_background_task = fake_create

        coro1 = asyncio.sleep(0)  # placeholder coroutines
        ComelitLocalCoordinator.restart_stop_video_delay(fake, coro1)
        task1 = fake._pending_stop_video_task

        coro2 = asyncio.sleep(0)
        ComelitLocalCoordinator.restart_stop_video_delay(fake, coro2)

        task1.cancel.assert_called_once()
        assert fake._pending_stop_video_task is not task1

        coro1.close()
        coro2.close()
        fake._pending_stop_video_task.close()


# ---------------------------------------------------------------------------
# Constructor coverage (via real __init__, not __new__)
# ---------------------------------------------------------------------------


class TestButtonConstructors:
    def test_door_button_init(self):
        coordinator = MagicMock()
        door = Door(id=0, index=3, name="Gate", apt_address="SB100001", output_index=0)
        btn = ComelitDoorButton(coordinator, door, "entry_abc")
        assert btn._attr_unique_id == "entry_abc_door_3"
        assert btn._door is door
        assert btn._entry_id == "entry_abc"
        assert btn._attr_name == "Gate"

    def test_start_video_button_init(self):
        coordinator = MagicMock()
        btn = ComelitStartVideoButton(coordinator, "entry_abc")
        assert btn._attr_unique_id == "entry_abc_video_start"
        assert btn._entry_id == "entry_abc"

    def test_stop_video_button_init(self):
        coordinator = MagicMock()
        btn = ComelitStopVideoButton(coordinator, "entry_abc")
        assert btn._attr_unique_id == "entry_abc_video_stop"
        assert btn._entry_id == "entry_abc"


# ---------------------------------------------------------------------------
# ComelitStartVideoButton.async_press
# ---------------------------------------------------------------------------


class TestComelitStartVideoButton:
    @pytest.mark.asyncio
    async def test_press_no_config_returns_early(self):
        coordinator = MagicMock()
        coordinator.device_config = None
        btn = ComelitStartVideoButton(coordinator, "entry_abc")
        await btn.async_press()
        coordinator.async_start_video.assert_not_called()

    @pytest.mark.asyncio
    async def test_press_starts_video(self):
        coordinator = MagicMock()
        coordinator.device_config = MagicMock()
        coordinator.async_start_video = AsyncMock()
        btn = ComelitStartVideoButton(coordinator, "entry_abc")
        await btn.async_press()
        coordinator.async_start_video.assert_awaited_once_with(by_user=True)

    @pytest.mark.asyncio
    async def test_press_does_not_raise_on_exception(self):
        coordinator = MagicMock()
        coordinator.device_config = MagicMock()
        coordinator.async_start_video = AsyncMock(side_effect=RuntimeError("fail"))
        btn = ComelitStartVideoButton(coordinator, "entry_abc")
        await btn.async_press()  # must not raise


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


class TestButtonSetupEntry:
    @pytest.mark.asyncio
    async def test_setup_entry_creates_entities_for_doors(self):
        from custom_components.comelit_man.button import async_setup_entry

        door = Door(id=0, index=0, name="Main", apt_address="SB100001", output_index=0)
        coordinator = MagicMock()
        coordinator.device_config = MagicMock(doors=[door])
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.entry_id = "entry_abc"
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))
        # 1 door button + start video + stop video + answer doorbell + switch camera = 5
        assert len(added) == 5
        assert isinstance(added[0], ComelitDoorButton)
        assert isinstance(added[1], ComelitStartVideoButton)
        assert isinstance(added[2], ComelitStopVideoButton)
        assert isinstance(added[3], ComelitAnswerDoorbellButton)
        assert isinstance(added[4], ComelitSwitchCameraButton)

    @pytest.mark.asyncio
    async def test_setup_entry_no_config_adds_nothing(self):
        from custom_components.comelit_man.button import async_setup_entry

        coordinator = MagicMock()
        coordinator.device_config = None
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))
        assert len(added) == 0

    @pytest.mark.asyncio
    async def test_setup_entry_no_doors_adds_nothing(self):
        from custom_components.comelit_man.button import async_setup_entry

        coordinator = MagicMock()
        coordinator.device_config = MagicMock(doors=[])
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.entry_id = "entry_abc"
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))
        assert len(added) == 0


# ---------------------------------------------------------------------------
# ComelitAnswerDoorbellButton
# ---------------------------------------------------------------------------


def _make_answer_button() -> ComelitAnswerDoorbellButton:
    coordinator = MagicMock()
    coordinator.async_answer_inbound = AsyncMock()
    btn = ComelitAnswerDoorbellButton.__new__(ComelitAnswerDoorbellButton)
    btn.coordinator = coordinator
    return btn


class TestComelitAnswerDoorbellButton:
    @pytest.mark.asyncio
    async def test_press_answers_inbound_call(self):
        btn = _make_answer_button()
        await btn.async_press()
        btn.coordinator.async_answer_inbound.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_press_does_not_raise_on_failure(self):
        """A failed answer must not surface as an unhandled service error."""
        btn = _make_answer_button()
        btn.coordinator.async_answer_inbound = AsyncMock(side_effect=RuntimeError("no session"))
        await btn.async_press()  # must not raise


def _make_switch_button() -> ComelitSwitchCameraButton:
    """Create a ComelitSwitchCameraButton with a mocked coordinator."""
    coordinator = MagicMock()
    coordinator.async_toggle_camera_selection = AsyncMock(return_value="cam2")

    btn = ComelitSwitchCameraButton.__new__(ComelitSwitchCameraButton)
    btn.coordinator = coordinator
    return btn


class TestComelitSwitchCameraButton:
    @pytest.mark.asyncio
    async def test_press_toggles_selector(self):
        btn = _make_switch_button()
        await btn.async_press()
        btn.coordinator.async_toggle_camera_selection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_press_does_not_raise_on_exception(self):
        btn = _make_switch_button()
        btn.coordinator.async_toggle_camera_selection = AsyncMock(side_effect=RuntimeError("boom"))
        await btn.async_press()  # must not propagate
