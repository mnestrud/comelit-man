"""Video call signaling via TCP to trigger UDP video streaming."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from .channels import Channel, ChannelType
from .client import IconaBridgeClient
from .const import DOMAIN
from .ctpp import ctpp_init_sequence
from .exceptions import VideoCallError
from .models import DeviceConfig
from .protocol import (
    encode_answer_peer,
    encode_call_accepted,
    encode_call_ack,
    encode_call_init,
    encode_call_response_ack,
    encode_door_open_during_video,
    encode_rtpc2_ready,
    encode_rtpc_link,
    encode_video_config,
)
from .rtp_receiver import RtpReceiver
from .rtsp_server import LocalRtspServer

_LOGGER = logging.getLogger(__name__)

VIDEO_RESPONSE_TIMEOUT = 5.0  # device can be slow to respond to CTPP signaling
VIDEO_SESSION_TIMEOUT = 120.0
VIDEO_READY_TIMEOUT = 6.0  # max wait for first media packet after signaling

# CTPP message counter increment constants (from PCAP analysis)
# Bytes [4-5] in CTPP body encode two independent sub-counters:
#   byte[4] increments by 1 → adds 0x00010000 to the LE32 timestamp field
#   byte[5] increments by 1 → adds 0x01000000 to the LE32 timestamp field
_CTR_INCR_BYTE4 = 0x00010000  # only byte[4] increments
_CTR_INCR_BYTE5 = 0x01000000  # only byte[5] increments
_CTR_INCR_BOTH = 0x01010000  # both byte[4] and byte[5] increment


def _transform_device_ts(dev_ts: int) -> int:
    """Derive our 0x1800 ACK timestamp from the device's message timestamp.

    PCAP-verified: every client 0x1800 ACK uses this transform of the
    device's LE32 timestamp, not an independent counter.
    """
    rb = bytearray(struct.pack("<I", dev_ts))
    rb[0] |= 0x80
    rb[2], rb[3] = rb[3], (rb[2] + 1) & 0xFF
    return int(struct.unpack("<I", bytes(rb))[0])


class VideoCallSession:
    """Manages the TCP signaling and UDP video for a video call.

    Uses the coordinator's shared IconaBridgeClient so video signaling
    and VIP event listening coexist on a single TCP connection. The
    sequence (from PCAP analysis):

    1. Open CTPP channel with apt address (client already authenticated)
    2. Send CTPP init + call initiation
    3. ACK device responses, wait for call acceptance
    4. Open UDPM channel (trailing_byte=1) — extract token from response
    5. Send codec negotiation
    6. Open 2x RTPC channels (trailing_byte=1)
    7. Send RTPC link (using RTPC1 request_id)
    8. Send video config trigger (using RTPC2 request_id)
    9. Start RTP receiver with dynamic IDs from channel setup
    10. Auto-timeout after ~120s
    """

    # Channel names opened by start() — cleaned up on stop without
    # disconnecting the shared client.
    _VIDEO_CHANNEL_NAMES = ("CTPP", "CSPB", "UDPM", "RTPC", "RTPC2", "RTPC_DEVICE", "RTPC_DEVICE_REEST")

    def __init__(
        self,
        client: IconaBridgeClient,
        config: DeviceConfig,
        auto_timeout: bool = True,
        rtsp_server: LocalRtspServer | None = None,
        on_call_end: Callable[[], None] | None = None,
        on_timeout: Callable[[], None] | None = None,
        on_ring: Callable[[str, int], None] | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._auto_timeout = auto_timeout
        self._external_rtsp = rtsp_server is not None
        self._on_call_end = on_call_end
        self._on_timeout = on_timeout
        # Invoked with (entrance_addr, ring_ts) when a 0x18C0 ring arrives
        # while this session holds the CTPP channel (the VIP listener is
        # stopped then, so without forwarding the ring would vanish).
        self._on_ring = on_ring
        self._rtp_receiver: RtpReceiver | None = None
        self._rtsp_server: LocalRtspServer | None = rtsp_server
        self._timeout_task: asyncio.Task[None] | None = None
        self._tcp_task: asyncio.Task[None] | None = None
        self._ctpp_task: asyncio.Task[None] | None = None
        self._active = False
        self._device_rtpc_req_id: int = 0
        # True when this session opened CTPP itself (notifications OFF).
        # False when reusing the coordinator-opened channel (notifications ON).
        # Determines whether _cleanup removes CTPP/CSPB from the client registry.
        self._owns_ctpp: bool = False
        # Shared CTPP counter — used for outbound messages (door open, answer peer).
        # NOT updated by ACKs: ACKs use _transform_device_ts, not this counter.
        # Protected by _ctpp_lock so concurrent senders don't collide on the wire.
        self._call_counter: int = 0
        self._ctpp_lock: asyncio.Lock = asyncio.Lock()
        # Inbound-only: device RTPC placeholder registered during start_inbound().
        # Kept until answer_inbound() waits for it to open.
        self._inbound_device_rtpc: Channel | None = None
        # Set by answer_inbound() before sending peer+call_accepted; the CTPP monitor
        # routes non-CALL_END 0x1840 messages here so the drain loop can ACK them
        # with the correct transform format instead of the monitor's own ACK path.
        self._answer_handoff: asyncio.Queue[bytes] | None = None

    @property
    def active(self) -> bool:
        """Return True if the video session is currently active."""
        return self._active

    @property
    def rtp_receiver(self) -> RtpReceiver | None:
        """Return the RTP receiver for getting video frames."""
        return self._rtp_receiver

    @property
    def rtsp_server(self) -> LocalRtspServer | None:
        """Return the RTSP server for go2rtc stream_source."""
        return self._rtsp_server

    def _ts(self) -> int:
        """Return current timestamp for CTPP messages."""
        return int(time.time()) & 0xFFFFFFFF

    # ------------------------------------------------------------------
    # CTPP signaling helpers (shared by start() and _inline_reestablish)
    # ------------------------------------------------------------------

    async def _run_codec_exchange(
        self,
        client: IconaBridgeClient,
        ctpp: Channel,
        our_addr: str,
        entrance_addr: str,
        call_counter: int,
    ) -> int:
        """Drive the codec exchange until call_accepted (0x0002). Returns counter."""
        for i in range(10):
            try:
                async with asyncio.timeout(VIDEO_RESPONSE_TIMEOUT):
                    resp = await client.read_response(ctpp)
            except TimeoutError:
                break
            if len(resp) < 2:
                break
            msg_type = struct.unpack_from("<H", resp, 0)[0]
            action = struct.unpack_from(">H", resp, 6)[0] if len(resp) >= 8 else 0
            _LOGGER.debug(
                "Codec exchange %d: type=0x%04X action=0x%04X our_counter=0x%08X",
                i,
                msg_type,
                action,
                call_counter,
            )
            if msg_type in (0x1860, 0x1800):
                continue
            if msg_type == 0x1840:
                if action == 0x0008:
                    call_counter += _CTR_INCR_BOTH
                    await client.send_binary(
                        ctpp,
                        encode_call_response_ack(our_addr, entrance_addr, call_counter),
                    )
                elif action == 0x0002:
                    call_counter += _CTR_INCR_BYTE5
                    await client.send_binary(
                        ctpp,
                        encode_call_response_ack(our_addr, entrance_addr, call_counter),
                    )
                    _LOGGER.debug("Codec exchange complete (call accepted)")
                    return call_counter
                else:
                    call_counter += _CTR_INCR_BYTE4
                    await client.send_binary(
                        ctpp,
                        encode_call_response_ack(our_addr, entrance_addr, call_counter),
                    )
        _LOGGER.warning("Codec exchange did not reach call-accepted state")
        return call_counter

    async def _ack_device_rtpc_link(
        self,
        client: IconaBridgeClient,
        ctpp: Channel,
        our_addr: str,
        entrance_addr: str,
        call_counter: int,
    ) -> int:
        """Read and ACK the device's RTPC link (action 0x000A). Returns counter.

        PCAP-verified: during initial start the device sends 0x1840/0x000A;
        during inline re-establishment it sends 0x1860/0x000A instead.
        Both are accepted.
        """
        for _ in range(5):
            try:
                async with asyncio.timeout(VIDEO_RESPONSE_TIMEOUT):
                    resp = await client.read_response(ctpp)
            except TimeoutError:
                break
            if len(resp) < 2:
                break
            msg_type = struct.unpack_from("<H", resp, 0)[0]
            action = struct.unpack_from(">H", resp, 6)[0] if len(resp) >= 8 else 0
            if msg_type in (0x1840, 0x1860) and action == 0x000A:
                call_counter += _CTR_INCR_BYTE5
                await client.send_binary(
                    ctpp,
                    encode_call_response_ack(our_addr, entrance_addr, call_counter),
                )
                return call_counter
            if msg_type == 0x1800:
                continue
        return call_counter

    async def start(self) -> RtpReceiver:
        """Execute the full TCP signaling sequence and start UDP receiver.

        The signaling flow matches the real Android app (from PCAP analysis):
        1. Auth → open CTPP + CSPB → CTPP init → ACK device responses
        2. Call init → open UDPM → START UDP CONTROL
        3. Wait for call ACK → codec ack → codec exchange (with UDP running)
        4. Open 2x RTPC → RTPC link → video config → device RTPC → start media
        """
        client = self._client

        try:
            apt_addr = self._config.apt_address
            apt_sub = self._config.apt_subaddress
            # our_addr = full address of the HA/app unit (apt_address + apt_subaddress)
            # This appears as the FIRST address in all CTPP video messages (PCAP-verified).
            our_addr = f"{apt_addr}{apt_sub}"
            # entrance_addr = the entrance panel address from entrance-address-book.
            # This appears as the SECOND address in call-phase messages (PCAP-verified).
            # For init-phase ACKs, we use apt_addr (without sub) as second address.
            entrance_addr = self._config.caller_address or our_addr
            if not self._config.caller_address:
                _LOGGER.warning(
                    "entrance-address-book is empty — using our_addr as entrance_addr. "
                    "Video call may fail if device requires a distinct entrance address."
                )

            # Step 1: Open CTPP + CSPB channels (PCAP shows both are needed).
            # CRITICAL: Use ChannelType.UAUT (type=7) for ALL channels — the real
            # Android app uses type=7 for everything. Using CTPP=16 may cause the
            # device to handle video calls incorrectly.
            #
            # The coordinator opens and initialises CTPP at setup when notifications
            # are enabled; this session reuses it directly. When notifications are
            # disabled, no CTPP exists yet — open and init it here, and take
            # ownership so _cleanup closes it when the session ends.
            ctpp = client.get_channel("CTPP")
            if ctpp is not None:
                self._owns_ctpp = False
                _LOGGER.debug(
                    "Reusing coordinator CTPP channel (server_id=%d) — skipping ctpp_init (already registered)",
                    ctpp.server_channel_id,
                )
                if client.get_channel("CSPB") is None:
                    await client.open_channel("CSPB", ChannelType.UAUT)
                # Use a fresh timestamp; call phase needs a different session ID
                # from the VIP init phase (bytes 2-3 of the CTPP timestamp).
                init_ts = self._ts()
            else:
                self._owns_ctpp = True
                ctpp = await client.open_channel("CTPP", ChannelType.UAUT, extra_data=our_addr)
                await client.open_channel("CSPB", ChannelType.UAUT)
                # Step 2: CTPP init + ACK pair (only needed on a fresh channel)
                init_ts = self._ts()
                await ctpp_init_sequence(
                    client,
                    ctpp,
                    apt_addr,
                    apt_sub,
                    our_addr,
                    init_ts,
                    response_timeout=VIDEO_RESPONSE_TIMEOUT,
                )

            # PCAP shows phone proceeds directly to call init after sending ACKs.

            # Register placeholder for device's RTPC channel early — the device
            # opens its own RTPC DURING codec exchange (~4.8s after connect),
            # before we even open our RTPC1/RTPC2 channels. Must be registered
            # here so _dispatch captures it immediately. With the request_id==0
            # filter in _dispatch, this placeholder will NOT steal RTPC1/RTPC2
            # open responses (those go through the request_id!=0 path).
            device_rtpc = client.register_placeholder_channel("RTPC_DEVICE")

            # Step 3: Send call init — uses a new "session" timestamp
            # (PCAP shows call phase uses different session from init phase)
            # PCAP-verified: call_init uses (our_addr, entrance_addr).
            #
            # CRITICAL: The device uses bytes[2-3] of the CTPP body as a
            # "session ID". Init and call phases MUST have different session IDs
            # (different low 16 bits of the timestamp). Since both init_ts and
            # call_ts are generated from int(time.time()) within the same second,
            # they'll be identical. We add 1 to the low byte to force a different
            # session ID while keeping the same counter starting point (high 16 bits).
            call_ts = (init_ts + 1) & 0xFFFFFFFF
            call_init = encode_call_init(our_addr, entrance_addr, call_ts)
            await client.send_binary(ctpp, call_init)

            # Step 4: Open UDPM immediately after call init (PCAP order)
            udpm = await client.open_channel("UDPM", ChannelType.UAUT, trailing_byte=1)
            udpm_token = 0x0000
            if len(udpm.open_response_body) >= 18:
                udpm_token = struct.unpack_from("<H", udpm.open_response_body, 16)[0]
                _LOGGER.debug("UDPM token: 0x%04X", udpm_token)

            # PCAP-verified: control_req_id = UDPM server_channel_id (device-assigned).
            control_req_id = udpm.server_channel_id
            receiver = RtpReceiver(
                client.host,
                client.port,
                control_req_id=control_req_id,
                media_req_id=0,  # set later after RTPC2 opens
                udpm_token=udpm_token,
            )
            if self._rtsp_server and self._external_rtsp:
                # Reuse coordinator-owned persistent RTSP server
                self._rtsp_server.reset()
                rtsp_server = self._rtsp_server
            else:
                # Standalone mode (tests) — create and own our own server
                rtsp_server = LocalRtspServer()
                await rtsp_server.start()
                self._rtsp_server = rtsp_server
            receiver.attach_rtsp_queues(
                rtsp_server.nal_queue,
                rtsp_server.audio_queue,
                rtp_queue=rtsp_server.rtp_queue,
            )

            # Open UDP socket + send 2 discovery packets so the device knows
            # our UDP port before video config. Start keepalive immediately so
            # the device doesn't time out during the codec exchange / RTPC setup
            # (which can take 10+ seconds). The PCAP shows keepalives sent
            # throughout the entire session, not just after video starts.
            await receiver.start_control()
            receiver.start_keepalive()
            self._rtp_receiver = receiver

            # Step 5: Wait for device ACK of call init, then send codec msg
            # CRITICAL: Each side maintains its OWN counter independently.
            # PCAP shows client uses call_ts-based counter that increments
            # by 0x10000 per message sent, while device has a completely
            # different counter. We must NEVER adopt the device's counter.
            call_counter = call_ts

            try:
                async with asyncio.timeout(VIDEO_RESPONSE_TIMEOUT):
                    resp1 = await client.read_response(ctpp)
                if len(resp1) >= 6:
                    dev_counter = struct.unpack_from("<I", resp1, 2)[0]
                    _LOGGER.debug(
                        "Call response: %d bytes, dev_counter=0x%08X, our_counter=0x%08X",
                        len(resp1),
                        dev_counter,
                        call_counter,
                    )
            except TimeoutError:
                pass

            # Send codec msg with our own incremented counter.
            # PCAP-verified: only +0x00010000 between call_init and codec
            # (byte[4] increments by 1, byte[5] stays).
            call_counter += _CTR_INCR_BYTE4
            codec_ack = encode_call_ack(our_addr, entrance_addr, call_counter)
            await client.send_binary(ctpp, codec_ack)

            # Step 6: Handle codec exchange (shared helper).
            call_counter = await self._run_codec_exchange(client, ctpp, our_addr, entrance_addr, call_counter)

            # Step 7: Open 2 RTPC channels (PCAP shows phone opens both)
            # RTPC1 is used for the link message, RTPC2 for video media
            rtpc1 = await client.open_channel("RTPC", ChannelType.UAUT, trailing_byte=1)
            rtpc2 = await client.open_channel(
                "RTPC2",
                ChannelType.UAUT,
                trailing_byte=1,
                wire_name="RTPC",
            )
            # PCAP-verified: media_req_id = RTPC2 server_channel_id (device-assigned).
            # In PCAP: RTPC2 server_channel_id=0x606E (= UDPM server_channel_id + 2).
            media_req_id = rtpc2.server_channel_id
            _LOGGER.debug(
                "RTPC channels: rtpc1=0x%04X, rtpc2(media)=0x%04X",
                rtpc1.request_id,
                media_req_id,
            )

            # Step 8: Send RTPC link (references RTPC1)
            # PCAP shows RTPC link reuses the last counter (no increment).
            # Must use server_channel_id (device-assigned), not local request_id.
            rtpc_link = encode_rtpc_link(our_addr, entrance_addr, rtpc1.server_channel_id, call_counter)
            await client.send_binary(ctpp, rtpc_link)
            _LOGGER.debug("Sent RTPC link, our_counter=0x%08X", call_counter)

            # Step 8b: Send video config IMMEDIATELY after RTPC link — BEFORE waiting
            # for device RTPC. PCAP shows the Android app sends VIDEO_CONFIG as message
            # #17 while device opens its own RTPC at #18. If we wait for device RTPC
            # first, the device doesn't enter the correct state for HANGUP/ZERO recovery.
            # PCAP: call_counter +0x00010000 (byte[4] +1) for video config DATA message.
            call_counter += _CTR_INCR_BYTE4
            vid_config = encode_video_config(our_addr, entrance_addr, media_req_id, call_counter)
            await client.send_binary(ctpp, vid_config)
            _LOGGER.debug("Sent video config (before device RTPC), our_counter=0x%08X", call_counter)

            # Step 9: Now wait for device to open its own RTPC channel, then ACK
            # its CTPP RTPC link message.
            # PCAP sequence after phone's RTPC link + video config:
            #   device CHAN_OPEN (RTPC) → auto-handled by dispatcher
            #   device CTPP 0x1840/0x000A (device's RTPC link)
            #   phone ACK 0x1800 → call_counter +0x01000000 (only byte[5] +1)
            #   device ACK 0x1800

            # Wait for device to open its own RTPC channel
            try:
                await asyncio.wait_for(device_rtpc.open_event.wait(), timeout=VIDEO_RESPONSE_TIMEOUT)
                _LOGGER.debug("Device opened RTPC: 0x%04X", device_rtpc.server_channel_id)
            except TimeoutError:
                _LOGGER.warning("Device RTPC channel not received within timeout")
                raise VideoCallError(
                    translation_domain=DOMAIN,
                    translation_key="video_rtpc_not_received",
                ) from None

            # Read and ACK device's CTPP RTPC link (0x1840/0x000A)
            call_counter = await self._ack_device_rtpc_link(client, ctpp, our_addr, entrance_addr, call_counter)

            # Step 9b: Send initial HANGUP/ZERO (0x1840/0x0000) to signal "call accepted".
            # PCAP (PCAPdroid_06_Mar_23_28_05): app sends this ~3s after video setup.
            # Device ACKs with 0x1800/0x0000. This tells the device we're ready.
            # The 30s lease timer runs from here; CALL_END arrives ~30s later.
            # Session restart on CALL_END is handled in _ctpp_monitor_loop.
            call_counter += _CTR_INCR_BYTE4
            hangup_zero = encode_call_response_ack(our_addr, entrance_addr, call_counter, prefix=0x1840)
            await client.send_binary(ctpp, hangup_zero)
            _LOGGER.debug("Sent initial HANGUP/ZERO (call accepted), counter=0x%08X", call_counter)

            # Step 10: Set media req_id and start decoder immediately.
            receiver.set_media_req_id(media_req_id)
            await receiver.start_media()

            # Step 10b: Start TCP video reader for RTPC2 BEFORE the CTPP
            # monitor so no firmware variant that streams over TCP loses
            # initial frames.  Start the CTPP monitor immediately after so
            # any device 0x1840 keepalives arriving during the readiness
            # wait are ACKed instead of lingering in the channel buffer.
            self._call_counter = call_counter
            self._tcp_task = asyncio.create_task(self._tcp_video_loop(client, rtpc2, receiver))
            self._ctpp_task = asyncio.create_task(
                self._ctpp_monitor_loop(
                    client,
                    ctpp,
                    our_addr,
                    entrance_addr,
                    call_counter,
                    rtpc1.server_channel_id,
                    media_req_id,
                )
            )
            self._active = True

            # Step 10d: Readiness gate — wait until the first real NAL has
            # been queued before reporting the session as ready.  Without
            # this, `Video ready in 1.5s` logs before a single video packet
            # has arrived and downstream clients see a silent stream.
            try:
                async with asyncio.timeout(VIDEO_READY_TIMEOUT):
                    await receiver.wait_for_first_video()
                got_media = True
            except TimeoutError:
                got_media = False
            if not got_media:
                udp = receiver.udp_media_packet_count
                tcp = receiver.tcp_media_packet_count
                _LOGGER.warning(
                    "No media received within %.1fs (udp=%d tcp=%d) — "
                    "signaling succeeded but device is not sending RTP. "
                    "Check NAT/firewall for UDP, or firmware may need TCP.",
                    VIDEO_READY_TIMEOUT,
                    udp,
                    tcp,
                )
            else:
                _LOGGER.info(
                    "Video flowing via %s transport",
                    "TCP" if receiver.tcp_media_packet_count else "UDP",
                )

            # Step 10e: Answer sequence — only runs AFTER video is flowing
            # so it cannot delay the first frame.  Fires 0x1840/0x0070 which
            # the device needs to transition into the "call answered" state;
            # audio RTPC follows at the next renewal cycle.
            if got_media:
                asyncio.create_task(  # noqa: RUF006
                    self._run_answer_sequence(
                        client,
                        ctpp,
                        our_addr,
                        entrance_addr,
                        apt_addr,
                        call_counter,
                        media_req_id,
                    )
                )

            _LOGGER.debug(
                "RTP receiver fully started: control=0x%04X, media=0x%04X, udpm_token=0x%04X",
                control_req_id,
                media_req_id,
                udpm_token,
            )

            # Step 11: Auto-timeout (skipped when stream handles lifecycle)
            if self._auto_timeout:
                self._timeout_task = asyncio.create_task(self._auto_timeout_loop())

            _LOGGER.info(
                "Video call session started: our_addr=%s entrance=%s",
                our_addr,
                entrance_addr,
            )
            return receiver

        except Exception as e:
            await self._cleanup()
            raise VideoCallError(
                translation_domain=DOMAIN,
                translation_key="video_call_failed",
            ) from e

    async def stop(self, reason: str = "user request") -> None:
        """Stop the video session and clean up."""
        _LOGGER.info("Stopping video call session (%s)", reason)
        await self._cleanup()

    async def _cleanup(self) -> None:
        """Clean up all resources.

        Tasks are cancelled with a 2s timeout on each await. Without the
        timeout, awaiting a cancelled task stuck on a dead TCP connection can
        freeze the event loop for 30-40s (observed on Python 3.14/aarch64).
        """
        self._active = False
        self._inbound_device_rtpc = None
        self._answer_handoff = None

        for task_attr in ("_timeout_task", "_tcp_task", "_ctpp_task"):
            task = getattr(self, task_attr)
            setattr(self, task_attr, None)
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await asyncio.wait([task], timeout=2.0)

        receiver, self._rtp_receiver = self._rtp_receiver, None
        if receiver:
            with contextlib.suppress(Exception):
                await receiver.stop()

        if self._rtsp_server and not self._external_rtsp:
            with contextlib.suppress(Exception):
                await self._rtsp_server.stop()
            self._rtsp_server = None

        # Release our channels back to the shared client but do NOT disconnect —
        # the coordinator owns the TCP connection and other consumers (VIP
        # listener, door open, PUSH) are still using it.
        # CTPP and CSPB are only removed when this session opened them itself
        # (notifications OFF). When the coordinator opened them, they outlive
        # the video session so the VIP listener can reattach afterwards.
        client = self._client
        if client:
            for name in self._VIDEO_CHANNEL_NAMES:
                if name in ("CTPP", "CSPB") and not self._owns_ctpp:
                    continue
                client.remove_channel(name)

    @staticmethod
    async def _tcp_video_loop(
        client: IconaBridgeClient,
        rtpc2: Channel,
        receiver: RtpReceiver,
    ) -> None:
        """Read TCP RTP packets from RTPC2 and feed to receiver.

        The device sends RTP directly over TCP on the RTPC2 channel.
        The client strips the ICONA header before queuing, so the queued
        body is raw RTP starting with 0x80 (RTP version 2).
        """
        try:
            while receiver.running:
                try:
                    async with asyncio.timeout(2.0):
                        data = await client.read_response(rtpc2)
                except TimeoutError:
                    continue
                if len(data) >= 12:
                    receiver.receive_tcp_rtp(data)
        except asyncio.CancelledError:
            pass
        except Exception:
            _LOGGER.debug("TCP video loop error", exc_info=True)

    async def _ctpp_monitor_loop(
        self,
        client: IconaBridgeClient,
        ctpp: Channel,
        our_addr: str,
        entrance_addr: str,
        call_counter: int,
        rtpc1_server_id: int,
        media_req_id: int,
    ) -> None:
        """Read and ACK incoming CTPP messages during the active video session.

        The device sends periodic 0x1840 messages throughout the call:
        - 0x0000: keepalive — ACK with bare 0x1800
        - 0x0003 / sub=0x0000: CALL_END — device lease timer expired; perform
            inline re-establishment (same TCP connection, no session restart).
        - 0x0003 / sub=0x000E: CALL_END triggered by door-open relay activation.
            PCAP-verified (camera_feed_with_open_door_local.pcap): same renewal
            sequence as the periodic CALL_END — NOT a bare ACK.
        0x1800 device ACKs are silently ignored.
        """
        try:
            while self._active:
                try:
                    async with asyncio.timeout(2.0):
                        resp = await client.read_response(ctpp)
                except TimeoutError:
                    continue
                if len(resp) < 2:
                    continue
                msg_type = struct.unpack_from("<H", resp, 0)[0]
                action = struct.unpack_from(">H", resp, 6)[0] if len(resp) >= 8 else 0
                sub = struct.unpack_from(">H", resp, 8)[0] if len(resp) >= 10 else 0
                if msg_type == 0x1840:
                    if action == 0x0003:
                        # CALL_END (sub=0x0000 = timer, sub=0x000E = door-open triggered)
                        _LOGGER.debug(
                            "CTPP monitor: CALL_END received (sub=0x%04X) — re-establishing",
                            sub,
                        )
                        call_end_ts = struct.unpack_from("<I", resp, 2)[0] if len(resp) >= 6 else 0
                        try:
                            async with self._ctpp_lock:
                                call_counter = await self._inline_reestablish(
                                    client,
                                    ctpp,
                                    our_addr,
                                    entrance_addr,
                                    rtpc1_server_id,
                                    media_req_id,
                                    call_counter,
                                    call_end_ts=call_end_ts,
                                )
                                self._call_counter = call_counter
                            _LOGGER.debug("CTPP monitor: re-established, lease renewed")
                        except Exception:
                            _LOGGER.warning(
                                "CTPP monitor: inline re-establish failed — falling back to full session restart",
                                exc_info=True,
                            )
                            self._active = False
                            if self._on_call_end:
                                self._on_call_end()
                            return
                    else:
                        # Keepalive (0x0000) or any other non-CALL_END 0x1840.
                        # If answer_inbound() is draining, forward to its handoff queue
                        # so it can ACK 0x000A/0x000E with the correct transform format.
                        # Otherwise ACK with transform(device_ts) — PCAP-verified: every
                        # client 0x1800 ACK uses this format, not a counter.
                        if self._answer_handoff is not None:
                            await self._answer_handoff.put(resp)
                        else:
                            ack_ts = _transform_device_ts(struct.unpack_from("<I", resp, 2)[0])
                            await client.send_binary(ctpp, encode_call_response_ack(our_addr, entrance_addr, ack_ts))
                            _LOGGER.debug(
                                "CTPP monitor: ACKed 0x1840/0x%04X (sub=0x%04X, transform)",
                                action,
                                sub,
                            )
                elif msg_type == 0x1860:
                    # Device 0x1860 messages during an active session (e.g.
                    # 0x000A RTPC link that _ack_device_rtpc_link missed, or
                    # other device-initiated messages) — ACK with transform(device_ts).
                    ack_ts = _transform_device_ts(struct.unpack_from("<I", resp, 2)[0])
                    await client.send_binary(ctpp, encode_call_response_ack(our_addr, entrance_addr, ack_ts))
                    _LOGGER.debug("CTPP monitor: ACKed 0x1860/0x%04X (transform)", action)
                    if action == 0x0001:  # IN_ALERTING — a ring arrived mid-call
                        self._forward_ring(resp)
                elif msg_type == 0x1800:
                    pass  # device ACK — no response needed
                elif msg_type == 0x18C0:
                    # A new ring while this session holds CTPP. Do NOT ACK —
                    # we have no valid counter state for the new call; the
                    # device retransmits briefly then stops. Forward it so
                    # the ring event still fires (observed dropped 2026-08-27).
                    self._forward_ring(resp)
                else:
                    _LOGGER.debug(
                        "CTPP monitor: unexpected type=0x%04X (%d bytes)",
                        msg_type,
                        len(resp),
                    )
        except asyncio.CancelledError:
            pass
        except Exception:
            _LOGGER.debug("CTPP monitor loop error", exc_info=True)

    def _forward_ring(self, data: bytes) -> None:
        """Forward a mid-call ring to the coordinator without touching the session.

        The coordinator dedups by (entrance, ring_ts) — device retransmits of
        the same ring share ring_ts, so each ring fires at most once. The
        running session is left alone (passive behavior: video keeps flowing,
        other stations keep ringing).
        """
        if self._on_ring is None:
            return
        # Local import: vip_listener imports _transform_device_ts from this
        # module, so a top-level import here would be circular.
        from .vip_listener import parse_ctpp_message

        msg = parse_ctpp_message(data)
        if msg is None:
            return
        addresses = msg.get("addresses", [])
        entrance_addr = addresses[0] if addresses else ""
        # DEBUG: fires once per device retransmit (~5x per ring); the
        # coordinator logs the deduped ring once at INFO.
        _LOGGER.debug(
            "Ring during active video call (entrance=%s ring_ts=0x%08X)",
            entrance_addr,
            msg["timestamp"],
        )
        try:
            self._on_ring(entrance_addr, msg["timestamp"])
        except Exception:
            _LOGGER.exception("Error in mid-call ring callback")

    async def _inline_reestablish(
        self,
        client: IconaBridgeClient,
        ctpp: Channel,
        our_addr: str,
        entrance_addr: str,
        rtpc1_server_id: int,
        media_req_id: int,
        call_counter: int,
        call_end_ts: int = 0,
    ) -> int:
        """Perform inline re-establishment after CALL_END, returning updated counter.

        Full media session restart on the same TCP/CTPP connection — verified
        working in commit efc75d91 on the dedicated-connection architecture and
        confirmed to work identically on the shared-connection architecture.

        Sequence (from working reference implementation):
        1. ACK CALL_END with transform(device_ts) — PCAP-verified format
        2. CTPP init + ACK pair (resets device-side session state)
        3. New call_init + codec exchange (reuse existing RTPC channels)
        4. RTPC_LINK + VIDEO_CONFIG
        5. Wait for device RTPC, ACK its link
        6. HANGUP/ZERO (0x1840/0x0000) — signals "call accepted" to device
        7. Renewal peer/accept (0x1860/0x0070) — triggers audio RTPC reopening
        8. Drain stale RTSP queues
        """
        apt_addr = our_addr[:-1]
        apt_sub = int(our_addr[-1])

        # 1. ACK CALL_END — use transform(device_ts) per PCAP; fall back to
        # counter increment if caller did not provide the device timestamp.
        ack_ts = _transform_device_ts(call_end_ts) if call_end_ts else (call_counter + _CTR_INCR_BYTE5) & 0xFFFFFFFF
        await client.send_binary(
            ctpp,
            encode_call_response_ack(our_addr, entrance_addr, ack_ts),
        )

        # 2. CTPP init + ACK pair
        init_ts = self._ts()
        await ctpp_init_sequence(
            client,
            ctpp,
            apt_addr,
            apt_sub,
            our_addr,
            init_ts,
            response_timeout=VIDEO_RESPONSE_TIMEOUT,
        )

        # 3. Placeholder for device's new RTPC channel
        device_rtpc = client.register_placeholder_channel("RTPC_DEVICE_REEST")

        # 4. Call init + codec ACK + codec exchange (resets call counter to new ts)
        call_ts = (init_ts + 1) & 0xFFFFFFFF
        call_counter = call_ts
        await client.send_binary(ctpp, encode_call_init(our_addr, entrance_addr, call_ts))
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(VIDEO_RESPONSE_TIMEOUT):
                await client.read_response(ctpp)
        call_counter += _CTR_INCR_BYTE4
        await client.send_binary(ctpp, encode_call_ack(our_addr, entrance_addr, call_counter))
        call_counter = await self._run_codec_exchange(client, ctpp, our_addr, entrance_addr, call_counter)

        # 5. RTPC_LINK + VIDEO_CONFIG (reuse existing RTPC channels)
        await client.send_binary(
            ctpp,
            encode_rtpc_link(our_addr, entrance_addr, rtpc1_server_id, call_counter),
        )
        call_counter += _CTR_INCR_BYTE4
        await client.send_binary(
            ctpp,
            encode_video_config(our_addr, entrance_addr, media_req_id, call_counter),
        )

        # 6. Wait for device RTPC and ACK its link
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(device_rtpc.open_event.wait(), timeout=VIDEO_RESPONSE_TIMEOUT)
        call_counter = await self._ack_device_rtpc_link(client, ctpp, our_addr, entrance_addr, call_counter)

        # 7. HANGUP/ZERO — signals "call accepted" to device (required for renewal)
        call_counter += _CTR_INCR_BYTE4
        await client.send_binary(
            ctpp,
            encode_call_response_ack(our_addr, entrance_addr, call_counter, prefix=0x1840),
        )

        # 8. Renewal peer/accept (0x1860/0x0070) — triggers audio RTPC reopening
        call_counter += _CTR_INCR_BYTE4
        await client.send_binary(
            ctpp,
            encode_answer_peer(our_addr, entrance_addr, call_counter, renewal=True),
        )
        _LOGGER.debug("Re-establish: sent renewal peer/accept (0x1860/0x0070)")

        # 9. Drain stale RTSP queues while keeping RTP seq/ts monotonic
        if self._rtsp_server:
            self._rtsp_server.reset(renewal=True)

        _LOGGER.debug("Re-establish: done, counter=0x%08X", call_counter)
        return call_counter

    async def _run_answer_sequence(
        self,
        client: IconaBridgeClient,
        ctpp: Channel,
        our_addr: str,
        entrance_addr: str,
        apt_addr: str,
        call_counter: int,
        media_req_id: int,
    ) -> None:
        """Run answer sequence as a background task (fire-and-forget)."""
        try:
            await self._send_answer_sequence(
                client,
                ctpp,
                our_addr,
                entrance_addr,
                apt_addr,
                call_counter,
                media_req_id,
            )
        except Exception:
            _LOGGER.warning("Answer sequence failed — continuing with video only", exc_info=True)

    async def _send_answer_sequence(
        self,
        client: IconaBridgeClient,
        ctpp: Channel,
        our_addr: str,
        entrance_addr: str,
        apt_addr: str,
        call_counter: int,
        media_req_id: int,
    ) -> None:
        """Send peer/accept (0x1840/0x0070) to signal "call answered".

        PCAP-verified: the Android app sends a single 0x1840/0x0070 message after
        video starts. Tested against real device: the device does NOT send PCMA audio
        in response to this message on HA-initiated calls — audio only flows during
        inbound calls triggered by a visitor pressing the doorbell. The RTSP audio
        plumbing is kept in place for when that flow is added.

        Uses _ctpp_lock and self._call_counter (not the stale call_counter
        parameter) so the counter is in sync with keepalive ACKs that
        _ctpp_monitor_loop may have sent during the 6s readiness wait.
        """
        async with self._ctpp_lock:
            self._call_counter += _CTR_INCR_BYTE4
            await client.send_binary(
                ctpp,
                encode_answer_peer(our_addr, entrance_addr, self._call_counter),
            )
        _LOGGER.info("Answer peer/accept (0x70) sent")

    async def start_inbound(self, entrance_addr: str, ring_ts: int, renewal_ack_ts: int = 0) -> RtpReceiver:  # noqa: C901
        """Execute the passive inbound setup sequence (PCAP2-verified, steps 1-12).

        Called when the device initiates a ring (PREFIX_CALL_INIT). Establishes
        the media path and starts video WITHOUT signaling the call as answered.
        Other phones remain ringing. Call answer_inbound() to actually answer.
        """
        client = self._client
        try:
            apt_addr = self._config.apt_address
            apt_sub = self._config.apt_subaddress
            our_addr = f"{apt_addr}{apt_sub}"
            # All inbound CTPP messages use our_base_addr as callee (PCAP2-verified).
            our_base_addr = apt_addr

            # Derive fresh_ts from ring_ts via device's proprietary transform (PCAP2-verified).
            _rb = bytearray(struct.pack("<I", ring_ts))
            _rb[0] |= 0x80
            _rb[2], _rb[3] = _rb[3], (_rb[2] + 1) & 0xFF
            fresh_ts = struct.unpack("<I", bytes(_rb))[0]

            ctpp = client.get_channel("CTPP")
            if ctpp is None:
                raise VideoCallError(
                    translation_domain=DOMAIN,
                    translation_key="video_call_failed",
                )

            self._inbound_device_rtpc = client.register_placeholder_channel("RTPC_DEVICE")

            # Step 1: ACK ring with fresh_ts
            await client.send_binary(ctpp, encode_call_response_ack(our_addr, our_base_addr, fresh_ts))

            # Steps 2-4 burst: RTPC OPEN + UDPM OPEN + codec ACK within 8ms.
            # PCAP shows all three sent before device ACKs any channel; codec ACK
            # must arrive while channels are still in flight or device ignores it.
            codec_ack = encode_call_ack(our_addr, our_base_addr, fresh_ts, codec_param=0x07)
            rtpc1_task = asyncio.create_task(client.open_channel("RTPC", ChannelType.UAUT, trailing_byte=1))
            await asyncio.sleep(0)
            udpm_task = asyncio.create_task(client.open_channel("UDPM", ChannelType.UAUT, trailing_byte=1))
            await asyncio.sleep(0)
            await client.send_binary(ctpp, codec_ack)

            rtpc1 = await rtpc1_task
            udpm = await udpm_task
            udpm_token = 0
            if len(udpm.open_response_body) >= 18:
                udpm_token = struct.unpack_from("<H", udpm.open_response_body, 16)[0]

            # Step 5: RTP receiver (UDP control + keepalive; media_req_id set after RTPC2)
            control_req_id = udpm.server_channel_id
            receiver = RtpReceiver(
                client.host,
                client.port,
                control_req_id=control_req_id,
                media_req_id=0,
                udpm_token=udpm_token,
            )
            if self._rtsp_server and self._external_rtsp:
                self._rtsp_server.reset()
                rtsp_server = self._rtsp_server
            else:
                rtsp_server = LocalRtspServer()
                await rtsp_server.start()
                self._rtsp_server = rtsp_server
            receiver.attach_rtsp_queues(
                rtsp_server.nal_queue,
                rtsp_server.audio_queue,
                rtp_queue=rtsp_server.rtp_queue,
            )
            await receiver.start_control()
            receiver.start_keepalive()
            self._rtp_receiver = receiver

            # Step 6: Wait for device response bundle (drain until non-ring response).
            # Device sends 0x18C0/0x0029 retransmits; ACK them with fresh_ts.
            try:
                async with asyncio.timeout(10.0):
                    while True:
                        data = await ctpp.response_queue.get()
                        if len(data) < 2:
                            continue
                        msg_type = struct.unpack_from("<H", data, 0)[0]
                        action = struct.unpack_from(">H", data, 6)[0] if len(data) >= 8 else 0
                        if msg_type == 0x18C0 and action == 0x0029:
                            await client.send_binary(ctpp, encode_call_response_ack(our_addr, our_base_addr, fresh_ts))
                            continue
                        if msg_type == 0x1860 and action == 0x0010 and renewal_ack_ts:
                            await client.send_binary(
                                ctpp, encode_call_response_ack(our_addr, our_base_addr, renewal_ack_ts)
                            )
                            await client.send_binary(
                                ctpp, encode_call_response_ack(our_addr, our_base_addr, renewal_ack_ts, prefix=0x1820)
                            )
                            continue
                        if msg_type != 0x18C0:
                            break
            except TimeoutError:
                _LOGGER.warning("start_inbound: no device bundle within 10s — proceeding")

            # Drain any remaining bundle messages before sending ACK2
            await asyncio.sleep(0.05)
            while not ctpp.response_queue.empty():
                try:
                    _fd = ctpp.response_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if renewal_ack_ts and len(_fd) >= 8:
                    _ft = struct.unpack_from("<H", _fd, 0)[0]
                    _fa = struct.unpack_from(">H", _fd, 6)[0]
                    if _ft == 0x1860 and _fa == 0x0010:
                        await client.send_binary(
                            ctpp, encode_call_response_ack(our_addr, our_base_addr, renewal_ack_ts)
                        )
                        await client.send_binary(
                            ctpp, encode_call_response_ack(our_addr, our_base_addr, renewal_ack_ts, prefix=0x1820)
                        )

            # Step 7: ACK2 (fresh_ts + B5) + RTPC2 open simultaneously (PCAP2-verified)
            call_counter = (fresh_ts + _CTR_INCR_BYTE5) & 0xFFFFFFFF
            await client.send_binary(ctpp, encode_call_response_ack(our_addr, our_base_addr, call_counter))
            rtpc2_task = asyncio.create_task(
                client.open_channel("RTPC2", ChannelType.UAUT, trailing_byte=1, wire_name="RTPC")
            )
            await asyncio.sleep(0)

            # Step 8: Codec ACK retransmit using same counter as ACK2 (PCAP2-verified)
            await client.send_binary(ctpp, encode_call_ack(our_addr, our_base_addr, call_counter, codec_param=0x07))

            rtpc2 = await rtpc2_task
            media_req_id = rtpc2.server_channel_id
            receiver.set_media_req_id(media_req_id)
            await receiver.start_media()

            # Step 9: RTPC2-ready (+B4) — purpose unknown; required by device (PCAP2)
            call_counter = (call_counter + _CTR_INCR_BYTE4) & 0xFFFFFFFF
            await client.send_binary(ctpp, encode_rtpc2_ready(our_addr, our_base_addr, call_counter))

            # Step 10: RTPC link (+B4)
            call_counter = (call_counter + _CTR_INCR_BYTE4) & 0xFFFFFFFF
            await client.send_binary(
                ctpp, encode_rtpc_link(our_addr, our_base_addr, rtpc1.server_channel_id, call_counter)
            )

            # Steps 11-12: Video config (320x240 for inbound, PCAP2-verified) + 3s retransmit
            call_counter = (call_counter + _CTR_INCR_BYTE4) & 0xFFFFFFFF
            vid_cfg = encode_video_config(our_addr, our_base_addr, media_req_id, call_counter, width=320, height=240)
            await client.send_binary(ctpp, vid_cfg)
            await asyncio.sleep(3.0)
            call_counter = (call_counter + _CTR_INCR_BYTE4) & 0xFFFFFFFF
            await client.send_binary(
                ctpp,
                encode_video_config(our_addr, our_base_addr, media_req_id, call_counter, width=320, height=240),
            )
            await asyncio.sleep(0.4)

            # Steps 13-17 (peer, call_accepted, drain, device RTPC, audio) are deferred
            # to answer_inbound() — called explicitly when the user presses "Answer".
            # This keeps the call passive (other phones still ring) until the user decides.

            # Start TCP media router — device sends video (RTPC2) + audio (RTPC1) via TCP
            self._tcp_task = asyncio.create_task(self._tcp_inbound_media_router(client, rtpc1, rtpc2, receiver))
            self._call_counter = call_counter
            self._ctpp_task = asyncio.create_task(
                self._ctpp_monitor_loop(
                    client,
                    ctpp,
                    our_addr,
                    our_base_addr,
                    call_counter,
                    rtpc1.server_channel_id,
                    media_req_id,
                )
            )
            self._active = True

            try:
                async with asyncio.timeout(VIDEO_READY_TIMEOUT):
                    await receiver.wait_for_first_video()
                _LOGGER.info("Inbound video ready (passive): our_addr=%s entrance=%s", our_addr, entrance_addr)
            except TimeoutError:
                _LOGGER.warning(
                    "start_inbound: no video within %.1fs — signaling succeeded but device not sending RTP",
                    VIDEO_READY_TIMEOUT,
                )

            if self._auto_timeout:
                self._timeout_task = asyncio.create_task(self._auto_timeout_loop())

            return receiver

        except Exception as e:
            await self._cleanup()
            raise VideoCallError(
                translation_domain=DOMAIN,
                translation_key="video_call_failed",
            ) from e

    async def answer_inbound(self) -> None:
        """Answer an active passive inbound call (steps 13-17).

        Sends peer + call_accepted to signal the call as answered (displacing
        other phones), drains the device's RTPC-link/peer ACKs with the correct
        transform(device_ts) format, waits for the device RTPC channel to open,
        then starts the audio sender.

        Must be called after start_inbound() has returned with video flowing.
        """
        if not self._active or not self._rtp_receiver:
            _LOGGER.warning("answer_inbound: no active inbound session — cannot answer")
            return
        if self._device_rtpc_req_id:
            return  # already answered
        ctpp = self._client.get_channel("CTPP")
        if ctpp is None:
            _LOGGER.warning("answer_inbound: CTPP channel not found")
            return
        our_addr = f"{self._config.apt_address}{self._config.apt_subaddress}"
        our_base = self._config.apt_address

        # Set handoff queue BEFORE sending peer+call_accepted so the CTPP
        # monitor routes any 0x1840 messages (including 0x000A/0x000E) here
        # instead of ACKing them itself.  Cleared in the finally block.
        self._answer_handoff = asyncio.Queue()
        try:
            async with self._ctpp_lock:
                self._call_counter = (self._call_counter + _CTR_INCR_BYTE4) & 0xFFFFFFFF
                await self._client.send_binary(
                    ctpp, encode_answer_peer(our_addr, our_base, self._call_counter, inbound=True)
                )
                self._call_counter = (self._call_counter + _CTR_INCR_BYTE4) & 0xFFFFFFFF
                await self._client.send_binary(ctpp, encode_call_accepted(our_addr, our_base, self._call_counter))
            _LOGGER.info("Answer: peer + call_accepted sent")

            # Drain 0x000A (rtpc_link) + 0x000E (peer) ACKs from the handoff
            # queue using transform(device_ts) — PCAP-verified format required
            # for the device to open its RTPC channel.
            acked_rtpc_link = acked_peer = False
            deadline = asyncio.get_running_loop().time() + VIDEO_RESPONSE_TIMEOUT
            while not (acked_rtpc_link and acked_peer):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    data = await asyncio.wait_for(self._answer_handoff.get(), timeout=remaining)
                except TimeoutError:
                    break
                if len(data) < 8:
                    continue
                msg_type = struct.unpack_from("<H", data, 0)[0]
                action = struct.unpack_from(">H", data, 6)[0]
                if msg_type == 0x1840 and action in (0x000A, 0x000E):
                    dev_ts = struct.unpack_from("<I", data, 2)[0]
                    await self._client.send_binary(
                        ctpp,
                        encode_call_response_ack(our_addr, our_base, _transform_device_ts(dev_ts)),
                    )
                    acked_rtpc_link |= action == 0x000A
                    acked_peer |= action == 0x000E
        finally:
            self._answer_handoff = None

        if self._inbound_device_rtpc is not None:
            try:
                await asyncio.wait_for(self._inbound_device_rtpc.open_event.wait(), timeout=VIDEO_RESPONSE_TIMEOUT)
                self._device_rtpc_req_id = self._inbound_device_rtpc.server_channel_id
                _LOGGER.debug("Answer: device RTPC=0x%04X", self._device_rtpc_req_id)
            except TimeoutError:
                _LOGGER.warning("answer_inbound: device RTPC not opened within timeout — audio unavailable")

        if self._rtp_receiver and self._device_rtpc_req_id:
            self._rtp_receiver.start_audio_sender(self._device_rtpc_req_id)
            _LOGGER.info("Inbound answered — audio started (req_id=0x%04X)", self._device_rtpc_req_id)

    @staticmethod
    async def _tcp_inbound_media_router(
        client: IconaBridgeClient,
        rtpc1: Channel,
        rtpc2: Channel,
        receiver: RtpReceiver,
    ) -> None:
        """Route TCP RTP from RTPC1 (audio) and RTPC2 (video) into receiver.

        On inbound calls the device streams both tracks over TCP (not UDP).
        The client strips the ICONA header, so queued data is raw RTP.
        """
        try:
            while receiver.running:
                for ch in (rtpc1, rtpc2):
                    try:
                        data = ch.response_queue.get_nowait()
                        if len(data) >= 12:
                            receiver.receive_tcp_rtp(data)
                    except asyncio.QueueEmpty:
                        pass
                await asyncio.sleep(0.001)
        except asyncio.CancelledError:
            pass
        except Exception:
            _LOGGER.debug("TCP inbound media router error", exc_info=True)

    async def async_open_door_on_ctpp(self, our_addr: str, entrance_addr: str, relay_index: int) -> None:
        """Open a door by sending 0x1840/0x000D on the active video CTPP channel.

        PCAP-verified: the Android app sends this single message on the existing
        video CTPP channel — no separate channel open, no 6-step sequence.
        The device ACKs with 0x1800/0x0000 and the relay activates.
        """
        ctpp = self._client.get_channel("CTPP")
        if ctpp is None or not self._active:
            raise RuntimeError("No active video CTPP channel")
        async with self._ctpp_lock:
            self._call_counter += _CTR_INCR_BYTE4
            payload = encode_door_open_during_video(our_addr, entrance_addr, self._call_counter, relay_index)
            await self._client.send_binary(ctpp, payload)
        _LOGGER.info(
            "Door open sent on video CTPP (relay=%d, counter=0x%08X)",
            relay_index,
            self._call_counter,
        )

    async def _auto_timeout_loop(self) -> None:
        """Automatically stop the session after VIDEO_SESSION_TIMEOUT."""
        try:
            await asyncio.sleep(VIDEO_SESSION_TIMEOUT)
            _LOGGER.info("Video session timed out after %ds", VIDEO_SESSION_TIMEOUT)
            await self._cleanup()
            if self._on_timeout:
                self._on_timeout()
        except asyncio.CancelledError:
            pass
