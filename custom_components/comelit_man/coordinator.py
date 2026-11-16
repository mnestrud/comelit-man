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
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .auth import authenticate
from .channels import ChannelType
from .client import IconaBridgeClient
from .config_reader import get_device_config
from .const import (
    CONF_CAMERA_LABEL_1,
    CONF_CAMERA_LABEL_2,
    CONF_ENABLE_NOTIFICATIONS,
    DEFAULT_CAMERA_LABEL_1,
    DEFAULT_CAMERA_LABEL_2,
    DOMAIN,
)
from .ctpp import _CTR_INCR_BOTH, ctpp_init_sequence
from .door import open_door
from .exceptions import AuthenticationError, DoorOpenError
from .go2rtc_helper import get_go2rtc
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
# Hardware settling cooldown between video sessions (seconds).
RELAY_SETTLE_COOLDOWN = 1.5

# open_entrance choreography: how long to wait for the (new) camera's first
# video SSRC after a start/toggle, and how often to poll the active slot.
CAMERA_SWITCH_TIMEOUT = 12.0
CAMERA_WAIT_POLL = 0.25
# The panel's natural state routes camera 1: after its call lease expires
# (the same ~120 s window that produces missed_call) the relay returns
# there, so the selector display reverts to cam1 once the panel idles out.
CAMERA_IDLE_REVERT_DELAY = 120.0


def _assign_camera_slot(seen: dict[int, str], active: str | None, ssrc: int) -> str:
    """Map a video SSRC to a camera slot ("cam1"/"cam2").

    Pure function of the learned mapping and the active slot so the policy
    is easy to unit test. Rules:
    - Known SSRC keeps its slot (same camera re-selected).
    - First-ever SSRC takes cam1.
    - Unseen SSRC takes the slot opposite to the currently active one — the
      panel alternates cameras on every new call, and after a power cut both
      SSRCs change, so "the other one than before" is the best available
      guess for which physical camera is now streaming.
    """
    known = seen.get(ssrc)
    if known is not None:
        return known
    if not seen:
        return "cam1"
    if active == "cam1":
        return "cam2"
    if active == "cam2":
        return "cam1"
    used = set(seen.values())
    return "cam2" if "cam1" in used else "cam1"


class ComelitLocalCoordinator(DataUpdateCoordinator[DeviceConfig]):
    """Coordinator that manages the persistent connection and push notifications."""

    # DataUpdateCoordinator types this as ConfigEntry | None; this coordinator
    # is always constructed with one, so narrowing it here removes a dozen
    # union-attr suppressions at the use sites.
    config_entry: ComelitLocalConfigEntry

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
        # Camera selector tracking. The entrance panel routes two physical
        # cameras (and the door strike) through a selector relay that
        # advances on every new call; each camera encodes RTP with its own
        # SSRC, so the SSRC identifies which camera — and therefore which
        # door — is currently active. Slots are "cam1"/"cam2" and the
        # user-facing labels live in entry options.
        self._camera_slots: dict[int, str] = {}  # video ssrc -> slot
        self._camera_ssrc: int | None = None
        # Panel routing state. cam1 is the panel's natural/default camera:
        # fresh outbound calls always answer on it, and the relay returns
        # there when the panel idles out (field-observed 2026-11-16).
        self._active_camera_slot: str | None = "cam1"
        self._camera_slot_mono: float | None = None  # last slot change (monotonic)
        # Slot the first video SSRC of the session about to start should be
        # assigned ("cam1" for outbound starts, the advance for toggles).
        # Consumed by _on_video_ssrc on the session's first SSRC.
        self._camera_expectation: str | None = None
        self._last_video_stop_mono: float = 0.0  # hardware settling timestamp
        self._auto_restart_timestamps: list[float] = []  # rate-limit auto-restarts

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
        if self.config_entry.options.get(CONF_ENABLE_NOTIFICATIONS, True):
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

        if self.config_entry.options.get(CONF_ENABLE_NOTIFICATIONS, True):
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

    @property
    def active_camera_slot(self) -> str | None:
        """Return the currently active camera slot ("cam1"/"cam2"), or None."""
        return self._active_camera_slot

    @property
    def camera_ssrc(self) -> int | None:
        """Return the last video RTP SSRC seen, or None when not streaming."""
        return self._camera_ssrc

    @property
    def camera_slot_map(self) -> dict[int, str]:
        """Return the learned video-SSRC -> camera-slot mapping."""
        return dict(self._camera_slots)

    @property
    def camera_slot_changed_at(self) -> float | None:
        """Return the monotonic time of the last camera-slot change."""
        return self._camera_slot_mono

    def camera_labels(self) -> dict[str, str]:
        """Return the user-facing label for each camera slot (from options)."""
        return {
            "cam1": self.config_entry.options.get(CONF_CAMERA_LABEL_1) or DEFAULT_CAMERA_LABEL_1,
            "cam2": self.config_entry.options.get(CONF_CAMERA_LABEL_2) or DEFAULT_CAMERA_LABEL_2,
        }

    def slot_for_label(self, label: str) -> str | None:
        """Return the camera slot whose user label matches, or None."""
        for slot, slot_label in self.camera_labels().items():
            if slot_label == label:
                return slot
        return None

    def _on_video_ssrc(self, ssrc: int) -> None:
        """Track which camera is streaming from the video RTP SSRC.

        Called by the active session's RtpReceiver whenever the video SSRC
        changes (including the first video packet of a session). The panel
        re-arms its encoder per call, so SSRCs are effectively unique per
        session and the learned map almost never hits — the slot for a new
        SSRC comes from the session's origin expectation when armed
        (outbound starts answer on cam1; a toggle made while the previous
        call is still held advances the relay), falling back to the
        alternating guess for inbound rings, whose camera we cannot know
        in advance (the panel routes the relay to whichever panel called).
        """
        known = self._camera_slots.get(ssrc)
        if known is not None:
            new_slot = known
        elif self._camera_expectation is not None:
            new_slot = self._camera_expectation
        else:
            new_slot = _assign_camera_slot(self._camera_slots, self._active_camera_slot, ssrc)
        self._camera_expectation = None
        self._camera_ssrc = ssrc
        if new_slot != self._active_camera_slot:
            self._camera_slots[ssrc] = new_slot
            self._active_camera_slot = new_slot
            self._camera_slot_mono = time.monotonic()
            _LOGGER.info("Camera selector: active camera is now %s (ssrc=0x%08X)", new_slot, ssrc)
            self.config_entry.async_create_background_task(
                self.hass,
                self._notify_video_state_change(),
                "comelit-camera-slot-change",
            )

    def _arm_outbound_camera_expectation(self) -> None:
        """Arm the first-SSRC expectation for an outbound (non-toggle) start.

        The panel answers a fresh call on its natural camera (cam1) — the
        relay cannot be advanced from idle with a single call. The display
        is corrected to cam1 immediately so the dropdown matches what the
        user is about to see.
        """
        if self._camera_expectation is None:
            self._camera_expectation = "cam1"
        if self._active_camera_slot != "cam1":
            self._active_camera_slot = "cam1"
            self._camera_slot_mono = time.monotonic()
            _LOGGER.info("Camera selector: outbound start — panel answers on cam1 (natural state)")

    async def async_toggle_camera_selection(self) -> str | None:
        """Advance (or re-anchor) the entrance panel's camera/door routing.

        Panel model (field-observed 2026-11-16): a call made while the
        previous one is still held advances the selector relay to the other
        camera — that is how camera switching works. A fresh call from the
        panel's natural state answers on cam1 (the manual-start case); the
        relay cannot be advanced from idle with a single call.

        Returns the active slot after the toggle ("cam1"/"cam2"), or None
        if the new camera's SSRC hasn't been seen yet.
        """
        if not self._config:
            raise RuntimeError("Not configured")
        pre = self._active_camera_slot
        had_session = self._video_session is not None and self._video_session.active
        if self._video_session is not None:
            await self.async_stop_video()
        if had_session:
            # Call held live → the panel advances the relay to the other camera.
            self._camera_expectation = {"cam1": "cam2", "cam2": "cam1"}.get(pre)
        else:
            # From the natural state a fresh call answers on cam1.
            self._camera_expectation = "cam1" if pre in ("cam1", "cam2") else None
        await self.async_start_video(auto_timeout=True, by_user=True, force_restart=True)
        return self._active_camera_slot

    async def _wait_for_active_camera_slot(
        self,
        target: str | None = None,
        timeout: float = CAMERA_SWITCH_TIMEOUT,
    ) -> str | None:
        """Wait for the active camera slot to be known (and match target if given).

        The slot is assigned by _on_video_ssrc when the first video packet of
        the (new) session arrives. With ``target`` set, resolves only when the
        routed camera matches; returns None on timeout.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            slot = self._active_camera_slot
            if slot is not None and (target is None or slot == target):
                return slot
            await asyncio.sleep(CAMERA_WAIT_POLL)
        return self._active_camera_slot if target is None else None

    async def async_open_entrance(self, label: str, *, stop_after: bool = False) -> None:
        """Open the door routed to the entrance identified by a camera label.

        v2 choreography — make sure the panel's physical selector relay routes
        the requested camera/door pair before firing the door relay, so "abrir
        urbanización" can never strike the wrong door:

        1. Resolve ``label`` to a camera slot via the configured labels.
        2. Ensure a video session is running and its camera is identified.
        3. While the active slot differs from the target: advance the selector
           (one stop + force-restart call = one relay click) and wait for the
           new camera's SSRC. Bounded to 3 advances (two slots + margin) so a
           misbehaving panel cannot hammer the relay.
        4. Fire the door relay on the active call (0x1840/0x000D when video is
           flowing; the standard open path otherwise).
        5. ``stop_after`` ends the session; by default it stays on (its own
           120 s timeout applies) so you can keep watching the entrance.
        """
        if not self._config or not self._client:
            raise RuntimeError("Not connected")
        target = self.slot_for_label(label)
        if target is None:
            raise RuntimeError(f"Unknown entrance label: {label!r}")

        session = self._video_session
        if session is None or not session.active:
            await self.async_start_video(auto_timeout=True, by_user=True)
            if await self._wait_for_active_camera_slot(timeout=CAMERA_SWITCH_TIMEOUT) is None:
                raise RuntimeError(
                    "Camera did not start streaming — cannot determine the active entrance"
                )

        for attempt in range(3):
            if self._active_camera_slot == target:
                break
            _LOGGER.info(
                "open_entrance: advancing selector towards %s (attempt %d, active=%s)",
                label,
                attempt + 1,
                self._active_camera_slot,
            )
            await self.async_toggle_camera_selection()
            if await self._wait_for_active_camera_slot(target=target) is not None:
                break
        else:
            raise RuntimeError(
                f"Selector did not reach {label!r} — active camera is {self._active_camera_slot}"
            )

        doors = self._config.doors
        if not doors:
            raise RuntimeError("No doors configured on the panel")
        door = doors[0]
        if len(doors) > 1:
            matches = [d for d in doors if label.lower() in (d.name or "").lower()]
            if matches:
                door = matches[0]
            else:
                _LOGGER.warning(
                    "open_entrance: %d doors and none named %r — opening the first",
                    len(doors),
                    label,
                )
        await self.async_open_door(door)
        _LOGGER.info("open_entrance: door relay fired for %r (camera %s)", label, target)

        if stop_after:
            await self.async_stop_video()

    def _on_push_event(self, event: PushEvent) -> None:
        """Dispatch a push event to all registered callbacks."""
        for cb in list(self._push_callbacks):
            try:
                cb(event)
            except Exception:
                _LOGGER.exception("Error in push callback")

    async def async_open_door(self, door: Door) -> None:
        """Open a door.

        Two paths:

        1. Video active — a single 0x1840/0x000D on the video CTPP channel
           with the live session counter (upstream PROTOCOL.md §6, restored
           verbatim from 2af72a6 for field testing).
        2. No video — door.open_door(): reuses the VIP listener's CTPP
           channel when open (fast path) or opens a transient one, and runs
           the OPEN + CONFIRM → door_init → OPEN + CONFIRM sequence.
        """
        if not self._config or not self._client:
            raise RuntimeError("Not connected")
        if self._video_session and self._video_session.active:
            our_addr = f"{self._config.apt_address}{self._config.apt_subaddress}"
            entrance_addr = self._config.caller_address or our_addr
            await self._video_session.async_open_door_on_ctpp(our_addr, entrance_addr, door.output_index)
            return
        try:
            await open_door(self.host, self.port, self.token, self._client, self._config, door)
        except DoorOpenError as err:
            if isinstance(err.__cause__, AuthenticationError):
                self.config_entry.async_start_reauth(self.hass)
            raise

    async def async_start_video(
        self,
        auto_timeout: bool = True,
        by_user: bool = False,
        force_restart: bool = False,
    ) -> VideoCallSession:
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
            force_restart: Force tearing down and recreating an active session
                           (used by camera selector toggle).
        """
        if not self._config:
            raise RuntimeError("Not configured")

        if self._video_start_lock.locked():
            _LOGGER.debug("Video start already in progress — skipping duplicate call")
            if self._video_session:
                return self._video_session
            raise RuntimeError("Video start already in progress")

        # If video is already streaming and active, do not tear it down and restart.
        # Starting a second session when one is already flowing causes the physical
        # entrance panel relay to cycle unnecessarily and desynchronizes video.
        if not force_restart and self._video_session is not None and self._video_session.active:
            _LOGGER.info("Video session already active and flowing — skipping duplicate start")
            return self._video_session

        async with self._video_start_lock:
            if not force_restart and self._video_session is not None and self._video_session.active:
                return self._video_session

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

            # Enforce hardware settling cooldown so the physical relay doesn't get hammered
            elapsed = time.monotonic() - self._last_video_stop_mono
            if elapsed < RELAY_SETTLE_COOLDOWN:
                wait_time = RELAY_SETTLE_COOLDOWN - elapsed
                _LOGGER.debug("Hardware relay settling cooldown: waiting %.2fs", wait_time)
                await asyncio.sleep(wait_time)

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
            if not force_restart:
                # Outbound start (manual button, auto-restart, open_entrance):
                # the panel answers a fresh call on its natural camera (cam1)
                # — observed on every manual start. Arm BEFORE session.start():
                # the readiness gate awaits the first video NAL, so the SSRC
                # hook fires inside it. The toggle path (force_restart=True)
                # armed its own advance expectation.
                self._arm_outbound_camera_expectation()
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
                if session.rtp_receiver is not None:
                    # The readiness gate awaited the first video NAL, so the
                    # SSRC is already known — seed the slot tracking now and
                    # keep the hook for mid-session changes.
                    session.rtp_receiver.on_video_ssrc = self._on_video_ssrc
                    if session.rtp_receiver.video_ssrc is not None:
                        self._on_video_ssrc(session.rtp_receiver.video_ssrc)
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
                self._camera_expectation = None
                await self._ensure_vip_listener()
                raise

    def _on_video_call_end(self) -> None:
        """Called by VideoCallSession when the device sends CALL_END."""
        if self._video_stopped_by_user:
            return
        _LOGGER.debug("CALL_END received — scheduling session restart")
        self.config_entry.async_create_background_task(
            self.hass, self._auto_restart_video(), "comelit-auto-restart-video"
        )

    async def _auto_restart_video(self) -> None:
        """Restart video after CALL_END/timeout — but only while watched.

        Restarting unconditionally kept the camera cycling 120s sessions
        forever after every ring.  Someone actively viewing (an RTSP client
        — go2rtc holds one only while a WebRTC/stream consumer exists) gets
        seamless continuity; with no viewers the session ends cleanly and
        the VIP listener takes the CTPP channel back.

        Calls async_start_video() without by_user=True so the call is
        silently dropped if the user has stopped video in the meantime.
        RuntimeError from that path is caught here to avoid HA logging an
        unhandled task exception for a normal, expected situation.
        """
        now = time.monotonic()
        self._auto_restart_timestamps = [t for t in self._auto_restart_timestamps if now - t < 60.0]
        if len(self._auto_restart_timestamps) >= 3:
            _LOGGER.warning(
                "Auto-restart suppressed: 3 restarts occurred within 60s. "
                "Stopping video session to protect hardware relay."
            )
            await self.async_stop_video()
            return

        watchers = self._rtsp_server.client_count if self._rtsp_server else 0
        if watchers == 0:
            _LOGGER.info("Video session ended with no viewers — not restarting")
            await self.async_stop_video()
            return
        self._auto_restart_timestamps.append(now)
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
        self.config_entry.async_create_background_task(
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
            if session.rtp_receiver is not None:
                session.rtp_receiver.on_video_ssrc = self._on_video_ssrc
                if session.rtp_receiver.video_ssrc is not None:
                    self._on_video_ssrc(session.rtp_receiver.video_ssrc)
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
        name = f"comelit_man_{self.config_entry.entry_id}"
        # Bare URL — go2rtc negotiates the backchannel itself via the
        # `Require: ...backchannel` header on DESCRIBE; the old #backchannel=1
        # source flag is unnecessary with that negotiation in place.
        src = self._rtsp_url
        try:
            session, go2rtc_url = get_go2rtc(
                self.hass, async_get_clientsession(self.hass)
            )
            async with session.put(
                f"{go2rtc_url}/api/streams",
                params={"name": name, "src": src},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status >= 400:
                    # e.g. 401 when go2rtc's API has auth configured — a
                    # static entry in go2rtc.yaml then has to provide the
                    # stream; say so instead of logging false success.
                    _LOGGER.warning(
                        "go2rtc stream registration failed (HTTP %d) — add a static entry '%s: %s' to go2rtc.yaml",
                        resp.status,
                        name,
                        src,
                    )
                else:
                    _LOGGER.debug("Registered go2rtc stream: %s -> %s", name, src)
        except Exception:
            _LOGGER.debug("go2rtc unavailable — backchannel inactive (RTSP/HLS still works)")

    async def _deregister_go2rtc_stream(self) -> None:
        """Remove our stream registration from go2rtc on shutdown."""
        name = f"comelit_man_{self.config_entry.entry_id}"
        with contextlib.suppress(Exception):
            session, go2rtc_url = get_go2rtc(
                self.hass, async_get_clientsession(self.hass)
            )
            async with session.delete(
                f"{go2rtc_url}/api/streams",
                params={"name": name},
                timeout=aiohttp.ClientTimeout(total=5),
            ):
                pass
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
        if not self.config_entry.options.get(CONF_ENABLE_NOTIFICATIONS, True):
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
        self._last_video_stop_mono = time.monotonic()
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
        # A stale first-SSRC expectation must not leak into the next session.
        self._camera_expectation = None
        # Once the panel idles out (~120 s lease, the missed_call window) its
        # relay returns to cam1 — the selector display follows (fire-and-
        # forget; the task re-checks that no session is active before touching
        # the slot, so a toggle started meanwhile is not disturbed).
        self.config_entry.async_create_background_task(
            self.hass, self._camera_idle_revert(), "comelit-camera-idle-revert"
        )
        await self._notify_video_state_change()

    async def _camera_idle_revert(self) -> None:
        """Revert the selector display to cam1 once the panel idles out.

        The panel's natural state routes camera 1: fresh outbound calls
        always answer there, and the relay returns to it when the call
        lease expires (the same window that produces missed_call).
        """
        await asyncio.sleep(CAMERA_IDLE_REVERT_DELAY)
        if self._video_start_lock.locked():
            return
        if self._video_session is not None and self._video_session.active:
            return
        if self._active_camera_slot != "cam1":
            self._active_camera_slot = "cam1"
            self._camera_slot_mono = time.monotonic()
            _LOGGER.info("Camera selector: panel idle — display reverts to cam1 (natural state)")
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
        self.config_entry.async_create_background_task(
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

        # _reconnect() repopulates _config or raises; a None here would be a
        # logic error rather than a runtime condition.
        if self._config is None:
            raise UpdateFailed("Reconnect completed without a device configuration")
        return self._config


type ComelitLocalConfigEntry = ConfigEntry[ComelitLocalCoordinator]
