"""Unit tests for ComelitLocalCoordinator — no device or HA runtime needed."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.comelit_man.coordinator import ComelitLocalCoordinator
from custom_components.comelit_man.models import Camera, DeviceConfig, Door


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> DeviceConfig:
    return DeviceConfig(
        apt_address="00000001",
        doors=[Door(id=1, index=0, name="Front", apt_address="00000001", output_index=0)],
        cameras=[Camera(id=1, name="Cam1", rtsp_url="rtsp://cam")],
    )


def _make_coordinator(*, with_client: bool = False) -> ComelitLocalCoordinator:
    """Create a coordinator with all HA dependencies mocked out."""
    hass = MagicMock()
    coordinator = ComelitLocalCoordinator.__new__(ComelitLocalCoordinator)
    coordinator.hass = hass
    coordinator.host = "127.0.0.1"
    coordinator.port = 64100
    coordinator.token = "fake_token"
    coordinator.device_name = "Comelit Intercom"
    config_entry = MagicMock()
    config_entry.options = {"enable_notifications": True}
    coordinator.config_entry = config_entry
    if with_client:
        mock_client = MagicMock()
        mock_client.connected = True
        mock_client.get_channel = MagicMock(return_value=None)
        mock_client.remove_channel = MagicMock()
        mock_client.open_channel = AsyncMock()
        mock_client.send_binary = AsyncMock()
        mock_client.set_disconnect_callback = MagicMock()
        coordinator._client = mock_client
    else:
        coordinator._client = None
    coordinator._config = MagicMock()
    coordinator._video_session = None
    coordinator._vip_listener = None
    coordinator._video_stopped_by_user = False
    coordinator._video_start_lock = asyncio.Lock()
    coordinator._video_ready_event = asyncio.Event()
    coordinator._rtsp_server = None
    coordinator._rtsp_url = None
    coordinator._push_callbacks = {}
    coordinator._on_stop_video = {}
    coordinator._on_video_state_change = {}
    coordinator._keepalive_task = None
    coordinator._connection_lost = False
    coordinator._ctpp_init_ts = 0
    coordinator.async_request_refresh = AsyncMock()
    coordinator.logger = MagicMock()
    return coordinator


def _mock_client() -> MagicMock:
    """Create a minimal mock TCP client for use in coordinator setup tests."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.connected = True
    client.set_disconnect_callback = MagicMock()
    client.open_channel = AsyncMock()
    client.send_binary = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# request_video_stop / video_stopped_by_user
# ---------------------------------------------------------------------------


class TestRequestVideoStop:
    def test_flag_starts_false(self):
        coord = _make_coordinator()
        assert coord.video_stopped_by_user is False

    def test_request_video_stop_sets_flag(self):
        coord = _make_coordinator()
        coord.request_video_stop()
        assert coord.video_stopped_by_user is True

    @pytest.mark.asyncio
    async def test_async_start_video_resets_flag(self):
        """async_start_video(by_user=True) must clear the stopped-by-user flag."""
        coord = _make_coordinator(with_client=True)
        coord._video_stopped_by_user = True

        mock_session = MagicMock()
        mock_session.start = AsyncMock()

        with patch(
            "custom_components.comelit_man.coordinator.VideoCallSession",
            return_value=mock_session,
        ):
            await coord.async_start_video(auto_timeout=True, by_user=True)

        assert coord.video_stopped_by_user is False


# ---------------------------------------------------------------------------
# async_stop_video
# ---------------------------------------------------------------------------


class TestAsyncStopVideo:
    @pytest.mark.asyncio
    async def test_stop_video_stops_session(self):
        coord = _make_coordinator()
        mock_session = MagicMock()
        mock_session.stop = AsyncMock()
        coord._video_session = mock_session

        await coord.async_stop_video()

        mock_session.stop.assert_awaited_once()
        assert coord._video_session is None

    @pytest.mark.asyncio
    async def test_stop_video_clears_ready_event(self):
        """async_stop_video clears the _video_ready_event so stream_source re-waits."""
        coord = _make_coordinator()
        coord._video_ready_event.set()
        mock_session = MagicMock()
        mock_session.stop = AsyncMock()
        coord._video_session = mock_session

        await coord.async_stop_video()

        assert not coord._video_ready_event.is_set()

    @pytest.mark.asyncio
    async def test_stop_video_noop_when_no_session(self):
        """async_stop_video is safe to call when there is no active session."""
        coord = _make_coordinator()
        coord._video_session = None
        await coord.async_stop_video()  # must not raise


# ---------------------------------------------------------------------------
# async_start_video
# ---------------------------------------------------------------------------


class TestAsyncStartVideo:
    @pytest.mark.asyncio
    async def test_start_video_sets_session(self):
        """async_start_video stores the new session in _video_session."""
        coord = _make_coordinator(with_client=True)
        mock_session = MagicMock()
        mock_session.start = AsyncMock()

        with patch(
            "custom_components.comelit_man.coordinator.VideoCallSession",
            return_value=mock_session,
        ):
            await coord.async_start_video(auto_timeout=True)

        assert coord._video_session is mock_session

    @pytest.mark.asyncio
    async def test_start_video_fires_ready_event(self):
        """async_start_video sets _video_ready_event after session starts."""
        coord = _make_coordinator(with_client=True)
        mock_session = MagicMock()
        mock_session.start = AsyncMock()

        with patch(
            "custom_components.comelit_man.coordinator.VideoCallSession",
            return_value=mock_session,
        ):
            await coord.async_start_video(auto_timeout=True)

        assert coord._video_ready_event.is_set()

    @pytest.mark.asyncio
    async def test_start_video_drops_concurrent_call(self):
        """A second async_start_video while one is in progress is dropped, not queued."""
        coord = _make_coordinator(with_client=True)
        started = asyncio.Event()
        unblock = asyncio.Event()

        async def slow_start():
            started.set()
            await unblock.wait()

        mock_session = MagicMock()
        mock_session.start = AsyncMock(side_effect=slow_start)

        with patch(
            "custom_components.comelit_man.coordinator.VideoCallSession",
            return_value=mock_session,
        ):
            task1 = asyncio.create_task(coord.async_start_video())
            await started.wait()  # first call is inside the lock

            # Second call should be rejected immediately (lock is held)
            with pytest.raises(RuntimeError, match="already in progress"):
                await coord.async_start_video()

            unblock.set()
            await task1

    @pytest.mark.asyncio
    async def test_start_video_resets_stopped_flag(self):
        """async_start_video(by_user=True) clears _video_stopped_by_user before starting."""
        coord = _make_coordinator(with_client=True)
        coord._video_stopped_by_user = True
        mock_session = MagicMock()
        mock_session.start = AsyncMock()

        with patch(
            "custom_components.comelit_man.coordinator.VideoCallSession",
            return_value=mock_session,
        ):
            await coord.async_start_video(by_user=True)

        assert coord._video_stopped_by_user is False


# ---------------------------------------------------------------------------
# Callback registration — add_stop_video_callback / add_video_state_change_callback
# ---------------------------------------------------------------------------


class TestCallbackRegistration:
    @pytest.mark.asyncio
    async def test_stop_video_callback_called_on_stop(self):
        """Callbacks registered via add_stop_video_callback fire during async_stop_video."""
        coord = _make_coordinator()
        mock_session = MagicMock()
        mock_session.stop = AsyncMock()
        coord._video_session = mock_session

        fired = []
        async def cb():
            fired.append(True)

        coord.add_stop_video_callback(cb)
        await coord.async_stop_video()

        assert fired == [True]

    @pytest.mark.asyncio
    async def test_stop_video_callback_remove_works(self):
        """The remove callable returned by add_stop_video_callback prevents future calls."""
        coord = _make_coordinator()
        mock_session = MagicMock()
        mock_session.stop = AsyncMock()
        coord._video_session = mock_session

        fired = []
        async def cb():
            fired.append(True)

        remove = coord.add_stop_video_callback(cb)
        remove()
        await coord.async_stop_video()

        assert fired == []

    @pytest.mark.asyncio
    async def test_stop_video_callback_exception_does_not_abort_stop(self):
        """An exception in a stop-video callback must not prevent the session from stopping."""
        coord = _make_coordinator()
        mock_session = MagicMock()
        mock_session.stop = AsyncMock()
        coord._video_session = mock_session

        async def bad_cb():
            raise RuntimeError("callback error")

        coord.add_stop_video_callback(bad_cb)
        await coord.async_stop_video()  # must not raise

        mock_session.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_video_state_change_callback_called_after_start(self):
        """add_video_state_change_callback fires after async_start_video completes."""
        coord = _make_coordinator(with_client=True)
        mock_session = MagicMock()
        mock_session.start = AsyncMock()

        fired = []
        async def cb():
            fired.append(True)

        coord.add_video_state_change_callback(cb)

        with patch(
            "custom_components.comelit_man.coordinator.VideoCallSession",
            return_value=mock_session,
        ):
            await coord.async_start_video()

        assert fired == [True]

    @pytest.mark.asyncio
    async def test_video_state_change_callback_called_after_stop(self):
        """add_video_state_change_callback fires after async_stop_video completes."""
        coord = _make_coordinator()
        mock_session = MagicMock()
        mock_session.stop = AsyncMock()
        coord._video_session = mock_session

        fired = []
        async def cb():
            fired.append(True)

        coord.add_video_state_change_callback(cb)
        await coord.async_stop_video()

        assert fired == [True]

    @pytest.mark.asyncio
    async def test_video_state_change_callback_remove_works(self):
        """The remove callable prevents future firings."""
        coord = _make_coordinator(with_client=True)
        mock_session = MagicMock()
        mock_session.start = AsyncMock()

        fired = []
        async def cb():
            fired.append(True)

        remove = coord.add_video_state_change_callback(cb)
        remove()

        with patch(
            "custom_components.comelit_man.coordinator.VideoCallSession",
            return_value=mock_session,
        ):
            await coord.async_start_video()

        assert fired == []


# ---------------------------------------------------------------------------
# async_open_door dispatch
# ---------------------------------------------------------------------------


class TestAsyncOpenDoor:
    @pytest.mark.asyncio
    async def test_uses_video_session_when_active(self):
        """async_open_door delegates to video session when one is active."""
        coord = _make_coordinator(with_client=True)
        door = MagicMock()
        door.output_index = 0

        session = MagicMock()
        session.active = True
        session.async_open_door_on_ctpp = AsyncMock()
        coord._video_session = session
        coord._config.apt_address = "SB000006"
        coord._config.apt_subaddress = 1
        coord._config.caller_address = None

        await coord.async_open_door(door)

        session.async_open_door_on_ctpp.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uses_open_door_when_no_video_session(self):
        """async_open_door delegates to open_door with host/port/token/client/config/door."""
        coord = _make_coordinator(with_client=True)
        door = MagicMock()

        with patch(
            "custom_components.comelit_man.coordinator.open_door",
            new_callable=AsyncMock,
        ) as mock_open_door:
            await coord.async_open_door(door)

        mock_open_door.assert_awaited_once_with(
            coord.host, coord.port, coord.token, coord._client, coord._config, door
        )

    @pytest.mark.asyncio
    async def test_raises_when_not_connected(self):
        """async_open_door raises RuntimeError when client is None."""
        coord = _make_coordinator()
        coord._client = None
        door = MagicMock()

        with pytest.raises(RuntimeError, match="Not connected"):
            await coord.async_open_door(door)

    @pytest.mark.asyncio
    async def test_door_open_error_triggers_reauth_when_auth_cause(self):
        """async_open_door calls async_start_reauth when DoorOpenError has AuthenticationError cause."""
        from custom_components.comelit_man.exceptions import AuthenticationError, DoorOpenError
        coord = _make_coordinator(with_client=True)
        door = MagicMock()

        auth_err = AuthenticationError("expired token")
        door_err = DoorOpenError("door failed")
        door_err.__cause__ = auth_err

        with (
            patch(
                "custom_components.comelit_man.coordinator.open_door",
                new_callable=AsyncMock,
                side_effect=door_err,
            ),
            pytest.raises(DoorOpenError),
        ):
            await coord.async_open_door(door)

        coord.config_entry.async_start_reauth.assert_called_once()

    @pytest.mark.asyncio
    async def test_door_open_error_no_reauth_when_not_auth_cause(self):
        """DoorOpenError without AuthenticationError cause does not trigger reauth."""
        from custom_components.comelit_man.exceptions import DoorOpenError
        coord = _make_coordinator(with_client=True)
        door = MagicMock()

        door_err = DoorOpenError("generic failure")
        door_err.__cause__ = OSError("network error")

        with (
            patch(
                "custom_components.comelit_man.coordinator.open_door",
                new_callable=AsyncMock,
                side_effect=door_err,
            ),
            pytest.raises(DoorOpenError),
        ):
            await coord.async_open_door(door)

        coord.config_entry.async_start_reauth.assert_not_called()


# ---------------------------------------------------------------------------
# rtsp_url / rtsp_server properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_rtsp_url_property(self):
        coord = _make_coordinator()
        coord._rtsp_url = "rtsp://127.0.0.1:8557/live"
        assert coord.rtsp_url == "rtsp://127.0.0.1:8557/live"

    def test_rtsp_server_property(self):
        coord = _make_coordinator()
        mock_server = MagicMock()
        coord._rtsp_server = mock_server
        assert coord.rtsp_server is mock_server

    def test_video_session_property(self):
        coord = _make_coordinator()
        mock_session = MagicMock()
        coord._video_session = mock_session
        assert coord.video_session is mock_session

    def test_video_session_property_none(self):
        coord = _make_coordinator()
        coord._video_session = None
        assert coord.video_session is None


# ---------------------------------------------------------------------------
# _open_ctpp_channels
# ---------------------------------------------------------------------------


class TestOpenCtppChannels:
    @pytest.mark.asyncio
    async def test_success_returns_ts_and_sets_ctpp_init_ts(self):
        coord = _make_coordinator(with_client=True)
        config = _make_config()

        with patch(
            "custom_components.comelit_man.coordinator.ctpp_init_sequence",
            new_callable=AsyncMock,
        ):
            ts = await coord._open_ctpp_channels(coord._client, config)

        assert isinstance(ts, int)
        assert coord._ctpp_init_ts == ts


# ---------------------------------------------------------------------------
# async_setup — VIP listener paths
# ---------------------------------------------------------------------------


def _setup_patches(client, config, mock_rtsp, *, with_vip=False, mock_vip=None):
    """Return context managers for a full async_setup mock."""
    patches = [
        patch("custom_components.comelit_man.coordinator.IconaBridgeClient", return_value=client),
        patch("custom_components.comelit_man.coordinator.authenticate", new_callable=AsyncMock),
        patch("custom_components.comelit_man.coordinator.get_device_config", new_callable=AsyncMock, return_value=config),
        patch("custom_components.comelit_man.coordinator.register_push", new_callable=AsyncMock),
        patch("custom_components.comelit_man.coordinator.LocalRtspServer", return_value=mock_rtsp),
        patch("custom_components.comelit_man.coordinator.ComelitLocalCoordinator._start_keepalive"),
    ]
    if with_vip and mock_vip:
        patches.append(patch("custom_components.comelit_man.coordinator.VipEventListener", return_value=mock_vip))
    return patches


class TestCoordinatorSetupVIP:
    @pytest.mark.asyncio
    async def test_setup_with_vip_listener_started(self):
        """VIP listener is created and started when notifications enabled and CTPP succeeds."""
        coord = _make_coordinator()
        config = _make_config()
        client = _mock_client()
        mock_rtsp = MagicMock()
        mock_rtsp.start = AsyncMock(return_value="rtsp://127.0.0.1:8557/live")
        mock_vip = MagicMock()
        mock_vip.start = AsyncMock()

        ps = _setup_patches(client, config, mock_rtsp, with_vip=True, mock_vip=mock_vip)
        with (
            ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6],
            patch.object(coord, "_open_ctpp_channels", new_callable=AsyncMock, return_value=0x12000000),
        ):
            await coord.async_setup()

        assert coord._vip_listener is mock_vip
        mock_vip.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_setup_vip_failure_logged_but_setup_continues(self):
        """VIP listener start failure is caught; setup still completes."""
        coord = _make_coordinator()
        config = _make_config()
        client = _mock_client()
        mock_rtsp = MagicMock()
        mock_rtsp.start = AsyncMock(return_value="rtsp://127.0.0.1:8557/live")

        ps = _setup_patches(client, config, mock_rtsp)
        with (
            ps[0], ps[1], ps[2], ps[3], ps[4], ps[5],
            patch.object(coord, "_open_ctpp_channels", new_callable=AsyncMock, side_effect=RuntimeError("ctpp fail")),
        ):
            await coord.async_setup()

        assert coord._vip_listener is None
        assert coord._rtsp_server is not None

    @pytest.mark.asyncio
    async def test_setup_notifications_disabled_skips_vip(self):
        """VIP listener is not started when enable_notifications = False."""
        coord = _make_coordinator()
        coord.config_entry.options = {"enable_notifications": False}
        config = _make_config()
        client = _mock_client()
        mock_rtsp = MagicMock()
        mock_rtsp.start = AsyncMock(return_value="rtsp://127.0.0.1:8557/live")

        ps = _setup_patches(client, config, mock_rtsp)
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5]:
            await coord.async_setup()

        assert coord._vip_listener is None

    @pytest.mark.asyncio
    async def test_setup_starts_rtsp_server(self):
        """async_setup starts the RTSP server and stores url + reference."""
        coord = _make_coordinator()
        config = _make_config()
        client = _mock_client()
        mock_rtsp = MagicMock()
        mock_rtsp.start = AsyncMock(return_value="rtsp://127.0.0.1:8557/live")

        ps = _setup_patches(client, config, mock_rtsp)
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5]:
            await coord.async_setup()

        assert coord._rtsp_server is mock_rtsp
        assert coord._rtsp_url == "rtsp://127.0.0.1:8557/live"


# ---------------------------------------------------------------------------
# _reconnect — video session and VIP listener cleanup paths
# ---------------------------------------------------------------------------


class TestReconnectCleanup:
    @pytest.mark.asyncio
    async def test_reconnect_stops_active_video_session(self):
        """_reconnect stops and clears active video session."""
        coord = _make_coordinator(with_client=True)
        mock_session = MagicMock()
        mock_session.stop = AsyncMock()
        coord._video_session = mock_session
        coord._video_ready_event.set()

        new_client = _mock_client()
        config = _make_config()
        with (
            patch("custom_components.comelit_man.coordinator.IconaBridgeClient", return_value=new_client),
            patch("custom_components.comelit_man.coordinator.authenticate", new_callable=AsyncMock),
            patch("custom_components.comelit_man.coordinator.get_device_config", new_callable=AsyncMock, return_value=config),
            patch("custom_components.comelit_man.coordinator.register_push", new_callable=AsyncMock),
            patch.object(coord, "_start_keepalive"),
        ):
            await coord._reconnect()

        mock_session.stop.assert_awaited_once()
        assert coord._video_session is None
        assert not coord._video_ready_event.is_set()

    @pytest.mark.asyncio
    async def test_reconnect_marks_rtsp_not_ready_when_session_active(self):
        """_reconnect marks RTSP server not-ready and disconnects clients when session was active."""
        coord = _make_coordinator(with_client=True)
        mock_session = MagicMock()
        mock_session.stop = AsyncMock()
        coord._video_session = mock_session
        mock_rtsp = MagicMock()
        coord._rtsp_server = mock_rtsp

        new_client = _mock_client()
        config = _make_config()
        with (
            patch("custom_components.comelit_man.coordinator.IconaBridgeClient", return_value=new_client),
            patch("custom_components.comelit_man.coordinator.authenticate", new_callable=AsyncMock),
            patch("custom_components.comelit_man.coordinator.get_device_config", new_callable=AsyncMock, return_value=config),
            patch("custom_components.comelit_man.coordinator.register_push", new_callable=AsyncMock),
            patch.object(coord, "_start_keepalive"),
        ):
            await coord._reconnect()

        mock_rtsp.mark_not_ready.assert_called_once()
        mock_rtsp.disconnect_clients.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconnect_stops_vip_listener(self):
        """_reconnect stops and clears VIP listener."""
        coord = _make_coordinator(with_client=True)
        mock_vip = MagicMock()
        mock_vip.stop = AsyncMock()
        coord._vip_listener = mock_vip

        new_client = _mock_client()
        config = _make_config()
        with (
            patch("custom_components.comelit_man.coordinator.IconaBridgeClient", return_value=new_client),
            patch("custom_components.comelit_man.coordinator.authenticate", new_callable=AsyncMock),
            patch("custom_components.comelit_man.coordinator.get_device_config", new_callable=AsyncMock, return_value=config),
            patch("custom_components.comelit_man.coordinator.register_push", new_callable=AsyncMock),
            patch.object(coord, "_start_keepalive"),
        ):
            await coord._reconnect()

        mock_vip.stop.assert_awaited_once()
        assert coord._vip_listener is None

    @pytest.mark.asyncio
    async def test_reconnect_restarts_vip_listener_when_enabled(self):
        """_reconnect creates and starts a new VIP listener when notifications enabled."""
        coord = _make_coordinator(with_client=True)
        new_client = _mock_client()
        config = _make_config()
        mock_vip = MagicMock()
        mock_vip.start = AsyncMock()

        with (
            patch("custom_components.comelit_man.coordinator.IconaBridgeClient", return_value=new_client),
            patch("custom_components.comelit_man.coordinator.authenticate", new_callable=AsyncMock),
            patch("custom_components.comelit_man.coordinator.get_device_config", new_callable=AsyncMock, return_value=config),
            patch("custom_components.comelit_man.coordinator.register_push", new_callable=AsyncMock),
            patch.object(coord, "_open_ctpp_channels", new_callable=AsyncMock, return_value=0x12000000),
            patch("custom_components.comelit_man.coordinator.VipEventListener", return_value=mock_vip),
            patch.object(coord, "_start_keepalive"),
        ):
            await coord._reconnect()

        assert coord._vip_listener is mock_vip
        mock_vip.start.assert_awaited_once()


# ---------------------------------------------------------------------------
# async_shutdown — VIP listener and RTSP server teardown
# ---------------------------------------------------------------------------


class TestShutdownTeardown:
    @pytest.mark.asyncio
    async def test_shutdown_stops_vip_listener(self):
        coord = _make_coordinator()
        mock_vip = MagicMock()
        mock_vip.stop = AsyncMock()
        coord._vip_listener = mock_vip
        coord._client = None

        await coord.async_shutdown()

        mock_vip.stop.assert_awaited_once()
        assert coord._vip_listener is None

    @pytest.mark.asyncio
    async def test_shutdown_stops_rtsp_server(self):
        coord = _make_coordinator()
        mock_rtsp = MagicMock()
        mock_rtsp.stop = AsyncMock()
        coord._rtsp_server = mock_rtsp
        coord._rtsp_url = "rtsp://127.0.0.1:8557/live"
        coord._client = None

        await coord.async_shutdown()

        mock_rtsp.stop.assert_awaited_once()
        assert coord._rtsp_server is None
        assert coord._rtsp_url is None


# ---------------------------------------------------------------------------
# async_start_video — VIP listener pause + rtsp mark_ready
# ---------------------------------------------------------------------------


class TestStartVideoExtended:
    @pytest.mark.asyncio
    async def test_start_video_pauses_vip_listener(self):
        """async_start_video stops VIP listener task before starting session."""
        coord = _make_coordinator(with_client=True)
        mock_vip = MagicMock()
        mock_vip.stop_task = AsyncMock()
        coord._vip_listener = mock_vip

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        with patch("custom_components.comelit_man.coordinator.VideoCallSession", return_value=mock_session):
            await coord.async_start_video()

        mock_vip.stop_task.assert_awaited_once()
        assert coord._vip_listener is None

    @pytest.mark.asyncio
    async def test_start_video_marks_rtsp_ready(self):
        """async_start_video calls mark_ready on the RTSP server."""
        coord = _make_coordinator(with_client=True)
        mock_rtsp = MagicMock()
        coord._rtsp_server = mock_rtsp

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        with patch("custom_components.comelit_man.coordinator.VideoCallSession", return_value=mock_session):
            await coord.async_start_video()

        mock_rtsp.mark_ready.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_video_raises_when_stopped_by_user_and_not_by_user(self):
        """async_start_video drops auto-restart if user has stopped video."""
        coord = _make_coordinator(with_client=True)
        coord._video_stopped_by_user = True

        with pytest.raises(RuntimeError, match="Video was stopped by user"):
            await coord.async_start_video(by_user=False)

    @pytest.mark.asyncio
    async def test_start_video_reconnects_dead_client(self):
        """async_start_video reconnects when client is disconnected."""
        coord = _make_coordinator(with_client=True)
        coord._client.connected = False

        mock_session = MagicMock()
        mock_session.start = AsyncMock()

        with (
            patch.object(coord, "_reconnect", new_callable=AsyncMock) as mock_reconnect,
            patch("custom_components.comelit_man.coordinator.VideoCallSession", return_value=mock_session),
        ):
            await coord.async_start_video(by_user=True)
            mock_reconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_video_raises_when_no_config(self):
        coord = _make_coordinator(with_client=True)
        coord._config = None
        with pytest.raises(RuntimeError, match="Not configured"):
            await coord.async_start_video()

    @pytest.mark.asyncio
    async def test_start_video_returns_existing_when_lock_held_with_session(self):
        """When lock is already held AND a session exists, returns the existing session (line 368)."""
        coord = _make_coordinator(with_client=True)
        existing_session = MagicMock()
        coord._video_session = existing_session

        await coord._video_start_lock.acquire()
        try:
            result = await coord.async_start_video()
        finally:
            coord._video_start_lock.release()

        assert result is existing_session

    @pytest.mark.asyncio
    async def test_start_video_raises_not_connected_inside_lock(self):
        """RuntimeError when _client is None inside the lock (line 373)."""
        coord = _make_coordinator()
        coord._client = None
        coord._config = MagicMock()

        with pytest.raises(RuntimeError, match="Not connected"):
            await coord.async_start_video()

    @pytest.mark.asyncio
    async def test_start_video_raises_when_reconnect_fails(self):
        """RuntimeError wraps reconnect failure (lines 392-393)."""
        coord = _make_coordinator(with_client=True)
        coord._client.connected = False

        with (
            patch.object(coord, "_reconnect", new_callable=AsyncMock, side_effect=OSError("net")),
            pytest.raises(RuntimeError, match="Reconnect failed"),
        ):
            await coord.async_start_video(by_user=True)


# ---------------------------------------------------------------------------
# _on_video_call_end / _auto_restart_video
# ---------------------------------------------------------------------------


class TestVideoCallEnd:
    def test_on_video_call_end_skips_when_user_stopped(self):
        coord = _make_coordinator()
        coord._video_stopped_by_user = True
        coord._on_video_call_end()
        coord.config_entry.async_create_background_task.assert_not_called()

    def test_on_video_call_end_schedules_restart_when_not_user_stopped(self):
        coord = _make_coordinator()
        coord._video_stopped_by_user = False
        coord._on_video_call_end()
        coord.config_entry.async_create_background_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_restart_video_success(self):
        coord = _make_coordinator(with_client=True)
        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        with patch("custom_components.comelit_man.coordinator.VideoCallSession", return_value=mock_session):
            await coord._auto_restart_video()
        assert coord._video_session is mock_session

    @pytest.mark.asyncio
    async def test_auto_restart_video_skips_gracefully_on_runtime_error(self):
        """_auto_restart_video silently drops RuntimeError (expected stop-by-user case)."""
        coord = _make_coordinator(with_client=True)
        coord._video_stopped_by_user = True
        await coord._auto_restart_video()  # must not raise

    @pytest.mark.asyncio
    async def test_auto_restart_video_logs_general_exception(self):
        """_auto_restart_video logs non-RuntimeError exceptions (lines 464-465)."""
        coord = _make_coordinator(with_client=True)
        with patch.object(
            coord, "async_start_video", new_callable=AsyncMock, side_effect=ValueError("boom")
        ):
            await coord._auto_restart_video()  # must not raise

    @pytest.mark.asyncio
    async def test_auto_restart_video_logs_unexpected_exception(self):
        """Unexpected exception in auto-restart is logged but not re-raised."""
        coord = _make_coordinator(with_client=True)
        coord._config = None  # causes RuntimeError("Not configured")
        await coord._auto_restart_video()  # must not raise


# ---------------------------------------------------------------------------
# _start_keepalive / _cancel_keepalive
# ---------------------------------------------------------------------------


class TestKeepalive:
    @pytest.mark.asyncio
    async def test_start_keepalive_creates_task(self):
        coord = _make_coordinator()
        coord._start_keepalive()
        assert coord._keepalive_task is not None
        coord._keepalive_task.cancel()
        try:
            await coord._keepalive_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_cancel_keepalive_cancels_task(self):
        coord = _make_coordinator()
        coord._start_keepalive()
        task = coord._keepalive_task
        coord._cancel_keepalive()
        assert coord._keepalive_task is None
        # Yield once so the event loop processes the cancellation
        await asyncio.sleep(0)
        assert task.cancelled() or task.done()

    def test_cancel_keepalive_noop_when_no_task(self):
        coord = _make_coordinator()
        coord._keepalive_task = None
        coord._cancel_keepalive()  # must not raise

    @pytest.mark.asyncio
    async def test_start_keepalive_cancels_previous_task(self):
        coord = _make_coordinator()
        coord._start_keepalive()
        first_task = coord._keepalive_task
        coord._start_keepalive()
        assert coord._keepalive_task is not first_task
        coord._keepalive_task.cancel()
        try:
            await coord._keepalive_task
        except asyncio.CancelledError:
            pass


class TestKeepaliveLoopBody:
    @pytest.mark.asyncio
    async def test_keepalive_loop_sends_keepalive_and_exits_on_disconnect(self):
        """Keepalive loop body: sends keepalive then exits when client disconnects."""
        coord = _make_coordinator(with_client=True)
        coord._config = MagicMock()

        call_count = 0

        async def mock_sleep(_interval):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                coord._client.connected = False

        with (
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch(
                "custom_components.comelit_man.coordinator.send_push_keepalive",
                new_callable=AsyncMock,
            ) as mock_kp,
        ):
            await coord._keepalive_loop()

        mock_kp.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_keepalive_loop_exception_is_swallowed(self):
        """Keepalive failure is swallowed; loop exits when client disconnects."""
        coord = _make_coordinator(with_client=True)
        coord._config = MagicMock()

        call_count = 0

        async def mock_sleep(_interval):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                coord._client.connected = False

        with (
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch(
                "custom_components.comelit_man.coordinator.send_push_keepalive",
                new_callable=AsyncMock,
                side_effect=OSError("net error"),
            ),
        ):
            await coord._keepalive_loop()  # must not raise

    @pytest.mark.asyncio
    async def test_keepalive_loop_cancelled_error_propagates(self):
        """CancelledError from wait_for is re-raised by the loop (line 513)."""
        coord = _make_coordinator(with_client=True)
        coord._config = MagicMock()

        async def fast_sleep(_interval):
            pass

        async def raise_cancelled(*args, **kwargs):
            raise asyncio.CancelledError

        with (
            patch("asyncio.sleep", side_effect=fast_sleep),
            patch(
                "custom_components.comelit_man.coordinator.asyncio.wait_for",
                side_effect=raise_cancelled,
            ),
        ):
            with pytest.raises(asyncio.CancelledError):
                await coord._keepalive_loop()


# ---------------------------------------------------------------------------
# _ensure_vip_listener
# ---------------------------------------------------------------------------


class TestEnsureVipListener:
    @pytest.mark.asyncio
    async def test_noop_when_already_running(self):
        coord = _make_coordinator(with_client=True)
        existing_vip = MagicMock()
        coord._vip_listener = existing_vip

        await coord._ensure_vip_listener()

        assert coord._vip_listener is existing_vip

    @pytest.mark.asyncio
    async def test_noop_when_no_config(self):
        coord = _make_coordinator(with_client=True)
        coord._config = None
        await coord._ensure_vip_listener()
        assert coord._vip_listener is None

    @pytest.mark.asyncio
    async def test_noop_when_notifications_disabled(self):
        coord = _make_coordinator(with_client=True)
        coord.config_entry.options = {"enable_notifications": False}
        await coord._ensure_vip_listener()
        assert coord._vip_listener is None

    @pytest.mark.asyncio
    async def test_creates_and_starts_vip_listener(self):
        coord = _make_coordinator(with_client=True)
        coord._vip_listener = None
        coord._ctpp_init_ts = 0x12000000
        mock_vip = MagicMock()
        mock_vip.start = AsyncMock()

        with patch("custom_components.comelit_man.coordinator.VipEventListener", return_value=mock_vip):
            await coord._ensure_vip_listener()

        assert coord._vip_listener is mock_vip
        mock_vip.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_logged_not_raised(self):
        coord = _make_coordinator(with_client=True)
        coord._vip_listener = None

        with patch(
            "custom_components.comelit_man.coordinator.VipEventListener",
            side_effect=RuntimeError("fail"),
        ):
            await coord._ensure_vip_listener()  # must not raise


# ---------------------------------------------------------------------------
# async_stop_video — RTSP mark_not_ready + VIP ensure
# ---------------------------------------------------------------------------


class TestStopVideoExtended:
    @pytest.mark.asyncio
    async def test_stop_video_marks_rtsp_not_ready_and_disconnects(self):
        coord = _make_coordinator()
        mock_session = MagicMock()
        mock_session.stop = AsyncMock()
        coord._video_session = mock_session
        mock_rtsp = MagicMock()
        coord._rtsp_server = mock_rtsp

        await coord.async_stop_video()

        mock_rtsp.mark_not_ready.assert_called_once()
        mock_rtsp.disconnect_clients.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_video_calls_ensure_vip_listener(self):
        coord = _make_coordinator()
        mock_session = MagicMock()
        mock_session.stop = AsyncMock()
        coord._video_session = mock_session

        with patch.object(coord, "_ensure_vip_listener", new_callable=AsyncMock) as mock_ensure:
            await coord.async_stop_video()

        mock_ensure.assert_awaited_once()


# ---------------------------------------------------------------------------
# _on_client_disconnect
# ---------------------------------------------------------------------------


class TestOnClientDisconnect:
    def test_noop_when_client_is_none(self):
        coord = _make_coordinator()
        coord._client = None
        coord._on_client_disconnect()  # must not raise

    def test_schedules_refresh(self):
        coord = _make_coordinator(with_client=True)
        coord._connection_lost = False
        coord._on_client_disconnect()
        coord.config_entry.async_create_background_task.assert_called_once()
        assert coord._connection_lost is True

    def test_second_call_does_not_re_log(self):
        """Subsequent calls after connection_lost is set still schedule refresh."""
        coord = _make_coordinator(with_client=True)
        coord._connection_lost = True
        coord._on_client_disconnect()
        coord.config_entry.async_create_background_task.assert_called_once()


# ---------------------------------------------------------------------------
# _async_update_data — auth failure path
# ---------------------------------------------------------------------------


class TestUpdateDataAuthFail:
    @pytest.mark.asyncio
    async def test_auth_failure_creates_issue_and_raises(self):
        from custom_components.comelit_man.exceptions import AuthenticationError
        coord = _make_coordinator()
        coord._client = None

        with (
            patch.object(coord, "_reconnect", new_callable=AsyncMock, side_effect=AuthenticationError("expired")),
            patch("custom_components.comelit_man.coordinator.ir") as mock_ir,
            pytest.raises(Exception),  # ConfigEntryAuthFailed
        ):
            await coord._async_update_data()

        mock_ir.async_create_issue.assert_called_once()


# ---------------------------------------------------------------------------
# _notify_video_state_change — exception path
# ---------------------------------------------------------------------------


class TestNotifyVideoStateChange:
    @pytest.mark.asyncio
    async def test_exception_in_callback_does_not_abort(self):
        coord = _make_coordinator()
        fired: list = []

        async def bad_cb():
            raise RuntimeError("state error")

        async def good_cb():
            fired.append(True)

        coord.add_video_state_change_callback(bad_cb)
        coord.add_video_state_change_callback(good_cb)
        await coord._notify_video_state_change()

        assert fired == [True]
