"""Tests for camera entities — placeholder image and stream_source gating."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.comelit_man.camera import (
    ComelitCamera,
    ComelitIntercomCamera,
)
from custom_components.comelit_man.models import Camera as CameraModel, PushEvent
from custom_components.comelit_man.placeholder import PLACEHOLDER_JPEG


# ---------------------------------------------------------------------------
# Placeholder JPEG validity
# ---------------------------------------------------------------------------

def test_placeholder_jpeg_valid():
    """Placeholder JPEG starts with SOI and ends with EOI markers."""
    assert PLACEHOLDER_JPEG[:2] == b"\xff\xd8"  # SOI
    assert PLACEHOLDER_JPEG[-2:] == b"\xff\xd9"  # EOI
    assert len(PLACEHOLDER_JPEG) > 100


# ---------------------------------------------------------------------------
# Camera fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def camera() -> ComelitIntercomCamera:
    """Create a ComelitIntercomCamera with a mocked coordinator."""
    coordinator = MagicMock()
    coordinator.video_session = None
    coordinator.device_config = MagicMock()
    coordinator._video_ready_event = asyncio.Event()
    cam = ComelitIntercomCamera(coordinator, "test_entry")
    return cam


# ---------------------------------------------------------------------------
# async_camera_image
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_camera_image_returns_placeholder_when_no_session(camera):
    """async_camera_image returns placeholder JPEG when no video session."""
    result = await camera.async_camera_image()
    assert result is PLACEHOLDER_JPEG


@pytest.mark.asyncio
async def test_camera_image_returns_placeholder_when_session_inactive(camera):
    """async_camera_image returns placeholder when session exists but inactive."""
    session = MagicMock()
    session.active = False
    camera.coordinator.video_session = session

    result = await camera.async_camera_image()
    assert result is PLACEHOLDER_JPEG


@pytest.mark.asyncio
async def test_camera_image_returns_frame_when_active(camera):
    """async_camera_image returns RTP frame when session is active."""
    fake_frame = b"\xff\xd8fake_jpeg\xff\xd9"
    session = MagicMock()
    session.active = True
    session.rtp_receiver = MagicMock()
    session.rtp_receiver.get_jpeg_frame = AsyncMock(return_value=fake_frame)
    camera.coordinator.video_session = session

    result = await camera.async_camera_image()
    assert result == fake_frame


# ---------------------------------------------------------------------------
# stream_source
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_source_returns_url_when_session_active(camera):
    """stream_source returns RTSP URL when a session exists."""
    camera.coordinator.video_session = MagicMock()
    camera.coordinator.rtsp_url = "rtsp://127.0.0.1:12345/intercom"

    url = await camera.stream_source()
    assert url == "rtsp://127.0.0.1:12345/intercom"


@pytest.mark.asyncio
async def test_stream_source_returns_none_when_no_session_and_timeout(camera):
    """stream_source returns None when no session is active and the ready event times out."""
    camera.coordinator.video_session = None
    camera.coordinator.rtsp_url = "rtsp://127.0.0.1:12345/intercom"

    # Patch wait_for as a real coroutine so the Event.wait() coroutine it
    # receives is properly closed (avoids "was never awaited" RuntimeWarning).
    async def _timeout(coro, timeout=None):
        coro.close()
        raise TimeoutError

    with patch("custom_components.comelit_man.camera.asyncio.wait_for", _timeout):
        url = await camera.stream_source()
    assert url is None


@pytest.mark.asyncio
async def test_stream_source_returns_url_when_ready_event_fires(camera):
    """stream_source returns the RTSP URL once _video_ready_event is set."""
    camera.coordinator.video_session = None
    camera.coordinator.rtsp_url = "rtsp://127.0.0.1:12345/intercom"
    camera.coordinator._video_ready_event.set()

    url = await camera.stream_source()
    assert url == "rtsp://127.0.0.1:12345/intercom"


# ---------------------------------------------------------------------------
# Doorbell push guard
# ---------------------------------------------------------------------------

def test_on_push_skips_when_already_active(camera):
    """_on_push does not start video if session already active."""
    session = MagicMock()
    session.active = True
    camera.coordinator.video_session = session
    camera.hass = MagicMock()

    event = PushEvent(event_type="doorbell_ring")
    camera._on_push(event)

    camera.hass.async_create_task.assert_not_called()


def test_on_push_does_not_auto_start_video(camera):
    """_on_push no longer auto-starts video — user controls video via button or automation."""
    camera.coordinator.video_session = None
    camera.hass = MagicMock()

    event = PushEvent(event_type="doorbell_ring")
    camera._on_push(event)

    camera.hass.async_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# ComelitCamera (external RTSP camera)
# ---------------------------------------------------------------------------


class TestComelitCamera:
    def _make_camera(self, rtsp_url: str = "rtsp://192.168.1.200/stream") -> ComelitCamera:
        coordinator = MagicMock()
        coordinator.host = "192.168.1.111"
        cam_model = CameraModel(id=5, name="Entrance", rtsp_url=rtsp_url)
        return ComelitCamera(coordinator, cam_model, "entry_abc")

    def test_init_sets_unique_id_and_name(self):
        cam = self._make_camera()
        assert cam._attr_unique_id == "entry_abc_camera_5"
        assert cam._attr_name == "Entrance"
        assert cam._entry_id == "entry_abc"

    def test_device_info_returns_dict(self):
        cam = self._make_camera()
        info = cam.device_info
        assert isinstance(info, dict)
        ids = info.get("identifiers", set())
        assert any("entry_abc_camera_5" in str(i) for i in ids)

    @pytest.mark.asyncio
    async def test_stream_source_returns_url(self):
        cam = self._make_camera("rtsp://192.168.1.200/stream")
        url = await cam.stream_source()
        assert url is not None
        assert "rtsp" in url

    @pytest.mark.asyncio
    async def test_stream_source_returns_none_when_no_url(self):
        cam = self._make_camera("")
        url = await cam.stream_source()
        assert url is None


# ---------------------------------------------------------------------------
# ComelitIntercomCamera — is_streaming property
# ---------------------------------------------------------------------------


def test_is_streaming_false_when_no_session(camera):
    camera.coordinator.video_session = None
    assert camera.is_streaming is False


def test_is_streaming_true_when_session_exists(camera):
    camera.coordinator.video_session = MagicMock()
    assert camera.is_streaming is True


# ---------------------------------------------------------------------------
# ComelitIntercomCamera — lifecycle callbacks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_added_to_hass_registers_all_callbacks(camera):
    camera.coordinator.add_push_callback = MagicMock(return_value=lambda: None)
    camera.coordinator.add_stop_video_callback = MagicMock(return_value=lambda: None)
    camera.coordinator.add_video_state_change_callback = MagicMock(return_value=lambda: None)

    await camera.async_added_to_hass()

    camera.coordinator.add_push_callback.assert_called_once()
    camera.coordinator.add_stop_video_callback.assert_called_once()
    camera.coordinator.add_video_state_change_callback.assert_called_once()
    assert camera._remove_push_cb is not None
    assert camera._remove_stop_video_cb is not None
    assert camera._remove_state_cb is not None


@pytest.mark.asyncio
async def test_async_will_remove_from_hass_clears_callbacks(camera):
    removed: list = []
    camera._remove_push_cb = lambda: removed.append("push")
    camera._remove_stop_video_cb = lambda: removed.append("stop")
    camera._remove_state_cb = lambda: removed.append("state")
    camera.coordinator.async_stop_video = AsyncMock()

    await camera.async_will_remove_from_hass()

    assert "push" in removed
    assert "stop" in removed
    assert "state" in removed
    assert camera._remove_push_cb is None
    assert camera._remove_stop_video_cb is None
    assert camera._remove_state_cb is None
    camera.coordinator.async_stop_video.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_will_remove_from_hass_no_callbacks_no_error(camera):
    camera._remove_push_cb = None
    camera._remove_stop_video_cb = None
    camera._remove_state_cb = None
    camera.coordinator.async_stop_video = AsyncMock()
    await camera.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_async_video_state_changed(camera):
    camera.async_refresh_providers = AsyncMock()
    camera.async_write_ha_state = MagicMock()
    await camera._async_video_state_changed()
    camera.async_refresh_providers.assert_awaited_once()
    camera.async_write_ha_state.assert_called_once()


# ---------------------------------------------------------------------------
# ComelitIntercomCamera — _async_stop_ha_stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_ha_stream_noop_when_no_stream(camera):
    camera.stream = None
    await camera._async_stop_ha_stream()  # must not raise


@pytest.mark.asyncio
async def test_stop_ha_stream_stops_stream(camera):
    mock_stream = MagicMock()
    mock_stream.stop = AsyncMock()
    camera.stream = mock_stream
    await camera._async_stop_ha_stream()
    mock_stream.stop.assert_awaited_once()
    assert camera.stream is None


@pytest.mark.asyncio
async def test_stop_ha_stream_handles_stop_exception(camera):
    mock_stream = MagicMock()
    mock_stream.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
    camera.stream = mock_stream
    await camera._async_stop_ha_stream()  # must not raise
    assert camera.stream is None


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_camera_setup_entry_creates_external_and_intercom(camera):
    from custom_components.comelit_man.camera import async_setup_entry
    cam_model = CameraModel(id=1, name="Cam1", rtsp_url="rtsp://cam/stream")
    coordinator = MagicMock()
    coordinator._video_ready_event = asyncio.Event()
    coordinator.video_session = None
    coordinator.device_config = MagicMock(
        cameras=[cam_model],
        doors=[MagicMock()],
    )
    entry = MagicMock()
    entry.runtime_data = coordinator
    entry.entry_id = "entry_abc"
    added: list = []
    await async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))
    assert len(added) == 2
    assert isinstance(added[0], ComelitCamera)
    assert isinstance(added[1], ComelitIntercomCamera)


@pytest.mark.asyncio
async def test_camera_setup_entry_no_config_returns_early():
    from custom_components.comelit_man.camera import async_setup_entry
    coordinator = MagicMock()
    coordinator.device_config = None
    entry = MagicMock()
    entry.runtime_data = coordinator
    added: list = []
    await async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))
    assert len(added) == 0


@pytest.mark.asyncio
async def test_camera_setup_entry_skips_camera_without_rtsp():
    from custom_components.comelit_man.camera import async_setup_entry
    cam_no_url = CameraModel(id=2, name="NoCam", rtsp_url="")
    coordinator = MagicMock()
    coordinator._video_ready_event = asyncio.Event()
    coordinator.video_session = None
    coordinator.device_config = MagicMock(cameras=[cam_no_url], doors=[])
    entry = MagicMock()
    entry.runtime_data = coordinator
    entry.entry_id = "entry_abc"
    added: list = []
    await async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))
    assert len(added) == 0
