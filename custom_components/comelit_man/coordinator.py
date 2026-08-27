"""DataUpdateCoordinator for the Comelit Local integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .auth import authenticate
from .channels import ChannelType
from .client import IconaBridgeClient
from .config_reader import get_device_config
from .const import CONF_ENABLE_NOTIFICATIONS, DOMAIN
from .ctpp import _CTR_INCR_BOTH, ctpp_init_sequence
from .door import open_door
from .exceptions import AuthenticationError, DoorOpenError
from .models import DeviceConfig, Door, PushEvent
from .push import register_push, send_push_keepalive
from .rtsp_server import LocalRtspServer
from .video_call import VideoCallSession
from .vip_listener import VipEventListener

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(seconds=30)

# Retransmits of the same 0x18C0 ring carry the same ring_ts; a genuine new
# ring gets a fresh ts, so exact-key dedup never suppresses a real ring.
RING_DEDUP_WINDOW = 120.0
# A device 0x1840/0x0000 (idle) counts as a missed call only when it follows
# a ring this recently; otherwise it is video-teardown / CTPP-init tail.
MISSED_CALL_WINDOW = 45.0


class ComelitLocalCoordinator(DataUpdateCoordinator[DeviceConfig]):
    """Coordinator that manages the persistent connection and push notifications."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ComelitLocalConfigEntry,
        host: str,
        port: int,
        token: str,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.host = host
        self.port = port
        self.token = token
        self.device_name = entry.title
        self._client: IconaBridgeClient | None = None
        self._config: DeviceConfig | None = None
        self._video_session: VideoCallSession | None = None
        self._video_stopped_by_user: bool = False
        # Prevents concurrent async_start_video calls from racing each other.
        # The device can only handle one CTPP negotiation at a time; a second
        # concurrent call would conflict and fail ~35s later with a UDPM timeout.
        self._video_start_lock: asyncio.Lock = asyncio.Lock()
        # Fires when a video session becomes ready — allows stream_source()
        # to wait briefly instead of returning None while CTPP is in flight.
        self._video_ready_event: asyncio.Event = asyncio.Event()
        self._rtsp_server: LocalRtspServer | None = None
        self._rtsp_url: str | None = None
        self._vip_listener: VipEventListener | None = None
        # LE32 counter sent in the last CTPP init on the shared connection;
        # VIP listener needs it to derive outgoing ACK timestamps
        # (ack_ts = init_ts + 0x01010000, PCAP-verified).
        self._ctpp_init_ts: int = 0
        self._keepalive_task: asyncio.Task[None] | None = None
        # Tracks whether we were connected on the last health-check so
        # disconnect / reconnect are logged exactly once per transition.
        self._connection_lost: bool = False
        # Last JPEG snapshot captured when passive inbound video started.
        # Set before the ring event fires; never overwritten by outbound video.
        self._last_ring_snapshot: bytes | None = None
        # Ring dedup + missed-call state.  Lives here (not in the VIP
        # listener) because the listener is recreated after every video
        # session — listener-local state would be wiped while the device
        # is still retransmitting the ring.
        self._recent_rings: dict[tuple[str, int], float] = {}
        self._last_ring_mono: float | None = None
        self._inbound_answered: bool = False
        # Caller of the passive inbound session's ring; when the session
        # ends with _inbound_answered still False, missed_call fires
        # (outcome-based — the device-signal path in _on_call_idle can't
        # cover the standard flow because passive video holds the CTPP
        # channel far past the 45s window).
        self._pending_inbound_ring: str | None = None
        # Use an insertion-ordered dict to track callbacks (value is always None).
        # This avoids ValueError on removal and preserves iteration order.
        self._push_callbacks: dict[Callable[[PushEvent], None], None] = {}
        # Async callbacks invoked at the top of async_stop_video, before any
        # RTSP client disconnect.  Lets the camera entity tear down HA's
        # Stream worker gracefully so its container_packets iterator ends
        # cleanly instead of raising "Stream ended; no additional packets"
        # on an EOF from our forced socket close.
        self._on_stop_video: dict[Callable[[], Awaitable[None]], None] = {}
        # Async callbacks invoked after video session becomes ready or is
        # fully torn down.  The camera entity uses this to write a fresh
        # HA state (is_streaming True/False) so the frontend card reacts
        # — without it, picture-entity locks to the transport it picked
        # at first stream_source() call and never upgrades from MJPEG.
        self._on_video_state_change: dict[Callable[[], Awaitable[None]], None] = {}

    @property
    def device_config(self) -> DeviceConfig | None:
        """Return the current device configuration."""
        return self._config

    @property
    def rtsp_url(self) -> str | None:
        """Return the persistent RTSP URL (available after setup)."""
        return self._rtsp_url

    @property
    def rtsp_server(self) -> LocalRtspServer | None:
        """Return the persistent RTSP server instance."""
        return self._rtsp_server

    async def _open_ctpp_channels(self, client: IconaBridgeClient, config: DeviceConfig) -> int:
        """Open CTPP + CSPB channels and run the full init handshake.

        Called at setup and reconnect when notifications are enabled. When
        notifications are disabled, CTPP is opened lazily by door/video.

        Returns the init_ts used in the handshake so the VIP listener can
        derive its outgoing ACK timestamps from the same value.
        """
        our_addr = f"{config.apt_address}{config.apt_subaddress}"
        ctpp = await client.open_channel("CTPP", ChannelType.UAUT, extra_data=our_addr)
        await client.open_channel("CSPB", ChannelType.UAUT)
        ts = int(time.time()) & 0xFFFFFFFF
        await ctpp_init_sequence(
            client,
            ctpp,
            config.apt_address,
            config.apt_subaddress,
            our_addr,
            ts,
        )
        self._ctpp_init_ts = ts
        _LOGGER.info(
            "CTPP channels opened for VIP events (address=%s, ts=0x%08X)",
            our_addr,
            ts,
        )
        return ts

    async def async_setup(self) -> None:
        """Connect, authenticate, fetch config, and register for push."""
        client = IconaBridgeClient(self.host, self.port)
        await client.connect()
        try:
            await authenticate(client, self.token)
            self._config = await get_device_config(client)
            await register_push(client, self._config, self._on_push_event)
        except Exception:
            await client.disconnect()
            raise

        self._client = client
        client.set_disconnect_callback(self._on_client_disconnect)

        # Start VIP event listener for doorbell ring detection, unless disabled.
        # The PUSH channel is one-shot FCM registration; actual call events
        # arrive as binary VIP messages on the CTPP channel.
        if self.config_entry.options.get(CONF_ENABLE_NOTIFICATIONS, True):  # type: ignore[union-attr]
            try:
                init_ts = await self._open_ctpp_channels(client, self._config)
                vip = VipEventListener(
                    client,
                    self._config,
                    self._on_push_event,
                    init_ts=init_ts,
                    on_inbound_ring=self._on_inbound_ring,
                    on_call_idle=self._on_call_idle,
                )
                await vip.start()
                self._vip_listener = vip
            except Exception:
                _LOGGER.warning("Failed to start VIP event listener", exc_info=True)
        else:
            _LOGGER.info("VIP event listener disabled via options")

        self._start_keepalive()

        # Start persistent RTSP server so go2rtc can connect immediately
        if not self._rtsp_server:
            rtsp = LocalRtspServer()
            self._rtsp_url = await rtsp.start()
            self._rtsp_server = rtsp
            _LOGGER.info("Persistent RTSP server started: %s", self._rtsp_url)
            await self._register_go2rtc_stream()

        self.async_set_updated_data(self._config)
        _LOGGER.info(
            "Comelit setup complete: %d doors, %d cameras",
            len(self._config.doors),
            len(self._config.cameras),
        )

    async def _reconnect(self) -> None:
        """Tear down old connection and re-establish everything."""
        self._cancel_keepalive()
        # Stop any active video session before disconnecting — a concurrent
        # session.start() holds a reference to the old client and will hang
        # for READ_TIMEOUT (30s) waiting for channel opens that will never
        # arrive once the TCP socket is closed.
        if self._video_session:
            with contextlib.suppress(Exception):
                await self._video_session.stop(reason="reconnect")
            self._video_session = None
            self._video_ready_event.clear()
            if self._rtsp_server:
                self._rtsp_server.mark_not_ready()
                self._rtsp_server.disconnect_clients()

        if self._vip_listener:
            with contextlib.suppress(Exception):
                await self._vip_listener.stop()
            self._vip_listener = None

        old_client = self._client
        self._client = None
        if old_client:
            try:
                await old_client.disconnect()
            except Exception:
                _LOGGER.debug("Error disconnecting old client", exc_info=True)

        client = IconaBridgeClient(self.host, self.port)
        try:
            await client.connect()
            await authenticate(client, self.token)
            self._config = await get_device_config(client)
            await register_push(client, self._config, self._on_push_event)
        except Exception:
            # Clean up the new client if setup fails partway through
            with contextlib.suppress(Exception):
                await client.disconnect()
            raise

        self._client = client
        client.set_disconnect_callback(self._on_client_disconnect)

        if self.config_entry.options.get(CONF_ENABLE_NOTIFICATIONS, True):  # type: ignore[union-attr]
            try:
                init_ts = await self._open_ctpp_channels(client, self._config)
                vip = VipEventListener(
                    client,
                    self._config,
                    self._on_push_event,
                    init_ts=init_ts,
                    on_inbound_ring=self._on_inbound_ring,
                    on_call_idle=self._on_call_idle,
                )
                await vip.start()
                self._vip_listener = vip
            except Exception:
                _LOGGER.warning("Failed to start VIP listener on reconnect", exc_info=True)

        self._start_keepalive()
        self._connection_lost = False
        ir.async_delete_issue(self.hass, DOMAIN, "auth_failed")
        _LOGGER.info("Comelit reconnected successfully")

    async def async_shutdown(self) -> None:
        """Disconnect from the device."""
        self._cancel_keepalive()
        await self.async_stop_video()
        if self._vip_listener:
            with contextlib.suppress(Exception):
                await self._vip_listener.stop()
            self._vip_listener = None
        if self._rtsp_server:
            await self._deregister_go2rtc_stream()
            with contextlib.suppress(Exception):
                await self._rtsp_server.stop()
            self._rtsp_server = None
            self._rtsp_url = None
        if self._client:
            await self._client.disconnect()
            self._client = None

    def add_push_callback(self, callback: Callable[[PushEvent], None]) -> Callable[[], None]:
        """Register a push event callback. Returns a callable that removes it."""
        self._push_callbacks[callback] = None

        def _remove() -> None:
            self._push_callbacks.pop(callback, None)

        return _remove

    def add_stop_video_callback(self, callback: Callable[[], Awaitable[None]]) -> Callable[[], None]:
        """Register an async callback invoked when video is stopped."""
        self._on_stop_video[callback] = None

        def _remove() -> None:
            self._on_stop_video.pop(callback, None)

        return _remove

    def add_video_state_change_callback(self, callback: Callable[[], Awaitable[None]]) -> Callable[[], None]:
        """Register an async callback invoked after video becomes ready or stops."""
        self._on_video_state_change[callback] = None

        def _remove() -> None:
            self._on_video_state_change.pop(callback, None)

        return _remove

    async def _notify_video_state_change(self) -> None:
        """Fire all registered video state-change callbacks."""
        for cb in list(self._on_video_state_change):
            try:
                await cb()
            except Exception:
                _LOGGER.exception("Error in video state change callback")

    def _on_push_event(self, event: PushEvent) -> None:
        """Dispatch a push event to all registered callbacks."""
        for cb in list(self._push_callbacks):
            try:
                cb(event)
            except Exception:
                _LOGGER.exception("Error in push callback")

    async def async_open_door(self, door: Door) -> None:
        """Open a door.

        Three paths depending on what CTPP channel is currently open:

        1. Video active — send a single 0x1840/0x000D on the video CTPP channel
           (PCAP-verified Android app behaviour; no new channel or 6-step sequence).
        2. VIP listener has CTPP open (notifications ON, no video) — reuse it,
           fire OPEN_DOOR + CONFIRM directly (~30ms, no init overhead).
        3. No CTPP open (notifications OFF) — open a transient CTPP channel,
           run full init, send commands, close channel.
        """
        if not self._config or not self._client:
            raise RuntimeError("Not connected")
        if self._video_session and self._video_session.active:
            our_addr = f"{self._config.apt_address}{self._config.apt_subaddress}"
            entrance_addr = self._config.caller_address or our_addr
            await self._video_session.async_open_door_on_ctpp(our_addr, entrance_addr, door.output_index)
        else:
            try:
                await open_door(self.host, self.port, self.token, self._client, self._config, door)
            except DoorOpenError as err:
                if isinstance(err.__cause__, AuthenticationError):
                    self.config_entry.async_start_reauth(self.hass)  # type: ignore[union-attr]
                raise

    async def async_start_video(self, auto_timeout: bool = True, by_user: bool = False) -> VideoCallSession:
        """Start a video call session.

        Concurrent calls are dropped — the device can only negotiate one
        CTPP session at a time and a second concurrent start would conflict
        with the first and fail ~35 s later with a UDPM timeout.

        Args:
            auto_timeout: stop the session after VIDEO_SESSION_TIMEOUT seconds.
            by_user: True when called from an explicit user action (button press).
                     False for auto-restarts from CALL_END / timeout callbacks.
                     Auto-restarts are silently dropped if the user has
                     since stopped video (prevents a stale async_create_task
                     from overriding a user stop and causing an infinite
                     go2rtc reconnect loop).
        """
        if not self._config:
            raise RuntimeError("Not configured")

        if self._video_start_lock.locked():
            _LOGGER.debug("Video start already in progress — skipping duplicate call")
            if self._video_session:
                return self._video_session
            raise RuntimeError("Video start already in progress")

        async with self._video_start_lock:
            if not self._client:
                raise RuntimeError("Not connected")

            # Drop auto-restarts that arrive after the user has stopped video.
            # Race: _on_video_call_end schedules async_start_video() as a task;
            # the user may stop video before the task executes.  Without this
            # check the stale task would reset _video_stopped_by_user and call
            # mark_ready(), causing go2rtc to reconnect into a dead stream.
            if self._video_stopped_by_user and not by_user:
                _LOGGER.debug("Skipping auto-restart — video was stopped by user")
                raise RuntimeError("Video was stopped by user — not auto-restarting")

            # If the TCP connection died (120s receive-loop timeout) before the
            # health-check interval had a chance to reconnect, reconnect now so
            # we don't start a session on a dead socket and wait 30s for UDPM
            # to time out.
            if not self._client.connected:
                _LOGGER.info("Client disconnected — reconnecting before video start")
                try:
                    await self._reconnect()
                except Exception as err:
                    raise RuntimeError(f"Reconnect failed: {err}") from err

            self._video_stopped_by_user = False
            await self.async_stop_video()

            # Pause the VIP listener task so it doesn't consume CTPP messages
            # meant for the video session. The CTPP channel itself stays open
            # and will be reused by the video session directly (no rename needed).
            # The listener task restarts in async_stop_video via _ensure_vip_listener.
            if self._vip_listener:
                with contextlib.suppress(Exception):
                    await self._vip_listener.stop_task()
                self._vip_listener = None

            t0 = time.monotonic()
            _LOGGER.info("Video session starting (CTPP setup)")
            session = VideoCallSession(
                self._client,
                self._config,
                auto_timeout=auto_timeout,
                rtsp_server=self._rtsp_server,
                on_call_end=self._on_video_call_end,
                on_timeout=self._on_video_call_end,
                on_ring=self._on_ring_during_video,
            )
            # Publish the session ONLY after start() has completed its
            # readiness gate (first real NAL queued).  Publishing earlier
            # lets HA's stream worker open the RTSP URL while CTPP is
            # still negotiating — it probes a video-less stream, stalls,
            # and takes ~20 s extra to recover once real NALs finally
            # arrive.  The trade-off is a cosmetic "camera does not
            # support play stream service" error logged by Lovelace at
            # the ~2 s mark, because `stream_source()` returns None while
            # CTPP is in flight.  go2rtc's WebRTC path queries the URL
            # through a different code path and is not affected, so the
            # user-visible latency stays at ~3 s.
            try:
                await session.start()
                _LOGGER.info("Video session ready in %.1fs", time.monotonic() - t0)
                if self._rtsp_server and session.rtp_receiver:
                    session.rtp_receiver.attach_backchannel_queue(self._rtsp_server.backchannel_queue)
                self._video_session = session
                self._video_ready_event.set()
                # Unblock PLAY handlers that have been waiting inside the RTSP
                # server for video to actually flow.  Any stream_worker that
                # reconnected during the CTPP handshake is stalled on PLAY
                # (our server holds 200 OK until mark_ready); releasing it here
                # means it transitions straight to reading frames instead of
                # erroring on an empty stream and taking a 10 s HA backoff.
                if self._rtsp_server:
                    self._rtsp_server.mark_ready()
                await self._notify_video_state_change()
                return session
            except Exception:
                await self._ensure_vip_listener()
                raise

    def _on_video_call_end(self) -> None:
        """Called by VideoCallSession when the device sends CALL_END."""
        if self._video_stopped_by_user:
            return
        _LOGGER.debug("CALL_END received — scheduling session restart")
        self.config_entry.async_create_background_task(  # type: ignore[union-attr]
            self.hass, self._auto_restart_video(), "comelit-auto-restart-video"
        )

    async def _auto_restart_video(self) -> None:
        """Auto-restart video after CALL_END or timeout.

        Calls async_start_video() without by_user=True so the call is
        silently dropped if the user has stopped video in the meantime.
        RuntimeError from that path is caught here to avoid HA logging an
        unhandled task exception for a normal, expected situation.
        """
        try:
            await self.async_start_video()
        except RuntimeError as err:
            _LOGGER.debug("Auto-restart skipped: %s", err)
        except Exception:
            _LOGGER.warning("Auto-restart failed", exc_info=True)

    def _on_inbound_ring(self, entrance_addr: str, ring_ts: int) -> None:
        """Called by VIP listener when device initiates a ring (PREFIX_CALL_INIT).

        Dedups device retransmits by (entrance, ring_ts), then schedules
        async_start_inbound_video as a background task so the inbound
        signaling sequence runs without blocking the VIP listener loop.
        """
        now = time.monotonic()
        key = (entrance_addr, ring_ts)
        seen = self._recent_rings.get(key)
        if seen is not None and now - seen < RING_DEDUP_WINDOW:
            _LOGGER.debug(
                "Inbound ring retransmit ignored (entrance=%s ring_ts=0x%08X)",
                entrance_addr,
                ring_ts,
            )
            return
        self._recent_rings = {k: t for k, t in self._recent_rings.items() if now - t < RING_DEDUP_WINDOW}
        self._recent_rings[key] = now
        self._last_ring_mono = now
        self._inbound_answered = False
        _LOGGER.debug("Inbound ring: entrance=%s ring_ts=0x%08X", entrance_addr, ring_ts)
        self.config_entry.async_create_background_task(  # type: ignore[union-attr]
            self.hass,
            self.async_start_inbound_video(entrance_addr, ring_ts),
            "comelit-inbound-video",
        )

    def _on_ring_during_video(self, entrance_addr: str, ring_ts: int) -> None:
        """Ring forwarded by the video session's CTPP monitor (listener stopped).

        Fires the ring event without touching the running session — passive
        behavior: video keeps flowing, other stations keep ringing. Shares
        the (entrance, ring_ts) dedup with _on_inbound_ring so device
        retransmits fire at most once.
        """
        now = time.monotonic()
        key = (entrance_addr, ring_ts)
        seen = self._recent_rings.get(key)
        if seen is not None and now - seen < RING_DEDUP_WINDOW:
            return
        self._recent_rings[key] = now
        self._last_ring_mono = now
        self._inbound_answered = False
        self._pending_inbound_ring = entrance_addr
        # Video is already flowing, so a warm frame makes the snapshot free.
        session = self._video_session
        if session is not None and session.rtp_receiver is not None:
            frame = session.rtp_receiver.latest_frame
            if frame is not None:
                self._last_ring_snapshot = frame
        _LOGGER.info("Ring during active video (entrance=%s ring_ts=0x%08X)", entrance_addr, ring_ts)
        self._on_push_event(
            PushEvent(
                event_type="ring",
                apt_address=entrance_addr,
                timestamp=time.time(),
            )
        )

    def _on_call_idle(self, addresses: list[str]) -> None:
        """Called by VIP listener on a device 0x1840/0x0000 (idle) frame.

        Fires missed_call only when the frame follows a recent unanswered
        ring and no video session is running — the same frame also arrives
        as a video-teardown tail and at CTPP init after a device reboot.
        """
        if self._video_session is not None:
            return
        if self._inbound_answered:
            return
        if self._last_ring_mono is None or time.monotonic() - self._last_ring_mono > MISSED_CALL_WINDOW:
            return
        self._last_ring_mono = None
        caller = addresses[0] if addresses else ""
        _LOGGER.info("Missed call detected (caller=%s)", caller)
        self._on_push_event(
            PushEvent(
                event_type="missed_call",
                apt_address=caller,
                timestamp=time.time(),
            )
        )

    async def async_start_inbound_video(self, entrance_addr: str, ring_ts: int) -> None:
        """Start passive video for a device-initiated ring (does not answer).

        On success, fires the ring event after video is ready so automations
        see the camera stream already flowing when the event triggers. Other
        stations keep ringing until answer_inbound() runs (Answer button).
        On failure, fires missed_call and restores the VIP listener.
        """
        if not self._config:
            return
        if self._video_start_lock.locked():
            _LOGGER.debug("Inbound: video start already in progress — skipping ring")
            return

        async with self._video_start_lock:
            if not self._client:
                return
            self._video_stopped_by_user = False
            await self.async_stop_video()

            if self._vip_listener:
                with contextlib.suppress(Exception):
                    await self._vip_listener.stop_task()
                self._vip_listener = None

            session = VideoCallSession(
                self._client,
                self._config,
                auto_timeout=True,
                rtsp_server=self._rtsp_server,
                on_call_end=self._on_video_call_end,
                on_timeout=self._on_video_call_end,
                on_ring=self._on_ring_during_video,
            )
            try:
                renewal_ack_ts = (self._ctpp_init_ts + _CTR_INCR_BOTH) & 0xFFFFFFFF
                await session.start_inbound(entrance_addr, ring_ts, renewal_ack_ts=renewal_ack_ts)
            except Exception:
                _LOGGER.warning("Inbound call answer failed", exc_info=True)
                # This path fires its own missed_call — clear the ring
                # recency so a later device idle frame doesn't double-fire.
                self._last_ring_mono = None
                self._on_push_event(
                    PushEvent(
                        event_type="missed_call",
                        apt_address=entrance_addr,
                        timestamp=time.time(),
                    )
                )
                await self._ensure_vip_listener()
                return

            if self._rtsp_server and session.rtp_receiver:
                session.rtp_receiver.attach_backchannel_queue(self._rtsp_server.backchannel_queue)
            self._video_session = session
            self._video_ready_event.set()
            if self._rtsp_server:
                self._rtsp_server.mark_ready()
            await self._notify_video_state_change()
            # Capture snapshot before firing ring so the image entity is ready
            # when automations react to the event.
            if session.rtp_receiver is not None:
                snapshot = session.rtp_receiver.latest_frame
                if snapshot is None:
                    with contextlib.suppress(Exception):
                        snapshot = await asyncio.wait_for(session.rtp_receiver.get_jpeg_frame(), timeout=2.0)
                if snapshot is not None:
                    self._last_ring_snapshot = snapshot
            # Fire ring AFTER video is flowing so automations see the stream
            self._pending_inbound_ring = entrance_addr
            self._on_push_event(
                PushEvent(
                    event_type="ring",
                    apt_address=entrance_addr,
                    timestamp=time.time(),
                )
            )
            _LOGGER.info("Inbound video ready, ring fired (entrance=%s)", entrance_addr)

    async def async_answer_inbound(self) -> None:
        """Answer an active passive inbound call — send answer signals, start audio."""
        if self._video_session and self._video_session.active:
            await self._video_session.answer_inbound()
            self._inbound_answered = True
            self._pending_inbound_ring = None

    async def _register_go2rtc_stream(self) -> None:
        """Register our RTSP stream with go2rtc, enabling backchannel support.

        go2rtc must be running (bundled in HA OS/Container/Supervised).
        Failures are logged at debug level and do not block setup — the
        integration still works without go2rtc (RTSP/HLS only, no WebRTC).
        """
        if not self._rtsp_url:
            return
        name = f"comelit_man_{self.config_entry.entry_id}"  # type: ignore[union-attr]
        src = f"{self._rtsp_url}#backchannel=1"
        try:
            async with aiohttp.ClientSession() as session:
                await session.put(
                    "http://127.0.0.1:1984/api/streams",
                    params={"name": name, "src": src},
                    timeout=aiohttp.ClientTimeout(total=5),
                )
            _LOGGER.debug("Registered go2rtc stream: %s -> %s", name, src)
        except Exception:
            _LOGGER.debug("go2rtc unavailable — backchannel inactive (RTSP/HLS still works)")

    async def _deregister_go2rtc_stream(self) -> None:
        """Remove our stream registration from go2rtc on shutdown."""
        name = f"comelit_man_{self.config_entry.entry_id}"  # type: ignore[union-attr]
        with contextlib.suppress(Exception):
            async with aiohttp.ClientSession() as session:
                await session.delete(
                    "http://127.0.0.1:1984/api/streams",
                    params={"name": name},
                    timeout=aiohttp.ClientTimeout(total=5),
                )
            _LOGGER.debug("Deregistered go2rtc stream: %s", name)

    @property
    def last_ring_snapshot(self) -> bytes | None:
        """Return the JPEG snapshot from the most recent inbound ring."""
        return self._last_ring_snapshot

    @property
    def video_stopped_by_user(self) -> bool:
        """Return True if the user explicitly stopped video (not CALL_END)."""
        return self._video_stopped_by_user

    def request_video_stop(self) -> None:
        """Mark that the user explicitly requested video to stop."""
        self._video_stopped_by_user = True

    def _start_keepalive(self) -> None:
        """Start the background keepalive task (cancels any previous one)."""
        self._cancel_keepalive()
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    def _cancel_keepalive(self) -> None:
        """Cancel the keepalive task if running."""
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
        self._keepalive_task = None

    async def _keepalive_loop(self) -> None:
        """Send a periodic push-info probe to keep the TCP connection alive.

        The Comelit device sleeps when idle and stops sending TCP traffic.
        Without this probe, the receive-loop 120s timeout fires and triggers
        a full reconnect cycle.  Re-sending push-info every 90s causes the
        device to respond with a JSON ACK, resetting the idle timer.

        If the device is genuinely unreachable (half-open socket), send_json
        raises within 10s (ProtocolError timeout) — the receive-loop will
        also detect the dead connection shortly after and trigger reconnect.
        """
        KEEPALIVE_INTERVAL = 90
        KEEPALIVE_TIMEOUT = 10.0

        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            if not self._client or not self._client.connected:
                return
            if not self._config:
                return
            try:
                await asyncio.wait_for(
                    send_push_keepalive(self._client, self._config),
                    timeout=KEEPALIVE_TIMEOUT,
                )
                _LOGGER.debug("Keepalive OK")
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.debug("Keepalive failed — connection may be dead", exc_info=True)
                # Don't force-reconnect here; the receive-loop will detect the
                # dead socket and set client.connected = False within seconds,
                # which the coordinator's health-check will pick up.

    async def _ensure_vip_listener(self) -> None:
        """Start VIP listener if enabled and not already running.

        Reuses the init_ts stored from the most recent _open_ctpp_channels
        call so the restarted listener's outgoing ACKs match the counter
        state the device already has for this CTPP channel.
        """
        if self._vip_listener or not self._config or not self._client:
            return
        if not self.config_entry.options.get(CONF_ENABLE_NOTIFICATIONS, True):  # type: ignore[union-attr]
            return
        try:
            vip = VipEventListener(
                self._client,
                self._config,
                self._on_push_event,
                init_ts=self._ctpp_init_ts,
                on_inbound_ring=self._on_inbound_ring,
                on_call_idle=self._on_call_idle,
            )
            await vip.start()
            self._vip_listener = vip
            _LOGGER.debug("VIP event listener restarted")
        except Exception:
            _LOGGER.warning("Failed to restart VIP listener", exc_info=True)

    async def async_stop_video(self) -> None:
        """Stop the active video call session.

        Snapshots _video_session and clears it immediately so a concurrent
        async_stop_video call can't also try to stop the same session
        (previous behaviour raced and crashed with AttributeError when the
        first stop cleared the attribute while the second was awaiting a
        stop-callback).
        """
        session = self._video_session
        if session is None:
            return
        self._video_session = None
        self._video_ready_event.clear()

        # Outcome-based missed call: a passive inbound session is ending and
        # the Answer button was never pressed — the call was missed. Fires
        # here because every session teardown path (timeout, auto-restart,
        # user stop, replacement by a new ring) funnels through this method.
        pending = self._pending_inbound_ring
        self._pending_inbound_ring = None
        if pending is not None and not self._inbound_answered:
            _LOGGER.info("Missed call: passive session ended unanswered (caller=%s)", pending)
            self._on_push_event(
                PushEvent(
                    event_type="missed_call",
                    apt_address=pending,
                    timestamp=time.time(),
                )
            )

        # Tear HA's Stream worker down gracefully FIRST, before any
        # forced RTSP client disconnect.  Stream.stop() joins the
        # worker thread, so its container closes cleanly; without
        # this, disconnect_clients() triggers an EOF mid-read and
        # HA logs "Stream ended; no additional packets" plus a 10 s
        # backoff before the next Start can recover.
        for cb in list(self._on_stop_video):
            try:
                await cb()
            except Exception:
                _LOGGER.exception("Error in stop-video callback")

        await session.stop(reason="user stopped")
        # Block future PLAYs until the next session is ready, and
        # kick any remaining RTSP clients (e.g. go2rtc) so they
        # reconnect fresh against a stream that already has video.
        if self._rtsp_server:
            self._rtsp_server.mark_not_ready()
            self._rtsp_server.disconnect_clients()
        # Restart VIP listener now that video released the CTPP slot.
        # Skip if we're inside async_start_video (lock already held) —
        # start_video will stop VIP again immediately anyway.
        if not self._video_start_lock.locked():
            await self._ensure_vip_listener()
        await self._notify_video_state_change()

    @property
    def video_session(self) -> VideoCallSession | None:
        """Return the active video call session, if any."""
        return self._video_session

    def _on_client_disconnect(self) -> None:
        """Called by the TCP client when the connection drops unexpectedly.

        Schedules an immediate coordinator refresh so _async_update_data runs
        within milliseconds and triggers reconnect, instead of waiting for the
        next 30-second polling interval.
        """
        if self._client is None:
            return  # already shut down
        if not self._connection_lost:
            _LOGGER.warning("Comelit device disconnected — attempting reconnect")
            self._connection_lost = True
        self.config_entry.async_create_background_task(  # type: ignore[union-attr]
            self.hass, self.async_request_refresh(), "comelit-reconnect-refresh"
        )

    async def _async_update_data(self) -> DeviceConfig:
        """Health-check the connection; reconnect if needed."""
        if self._client and self._client.connected and self._config:
            return self._config

        # Connection lost or no config — attempt reconnect.
        # One-shot warning: _on_client_disconnect may have already fired it;
        # set the flag here too for cases where the socket died silently.
        if not self._connection_lost:
            _LOGGER.warning("Comelit device disconnected, attempting reconnect")
            self._connection_lost = True

        try:
            await self._reconnect()
        except AuthenticationError as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                "auth_failed",
                is_fixable=False,
                is_persistent=True,
                severity=ir.IssueSeverity.ERROR,
                translation_key="auth_failed",
                translation_placeholders={"name": self.device_name},
            )
            raise ConfigEntryAuthFailed("Authentication failed — update the token") from err
        except Exception as err:
            raise UpdateFailed(f"Reconnect failed: {err}") from err

        return self._config  # type: ignore[return-value]


type ComelitLocalConfigEntry = ConfigEntry[ComelitLocalCoordinator]
