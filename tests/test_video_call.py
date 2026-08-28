"""Unit tests for VideoCallSession — no device needed."""

from __future__ import annotations

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.comelit_man.exceptions import VideoCallError
from custom_components.comelit_man.video_call import (
    _CTR_INCR_BOTH,
    _CTR_INCR_BYTE4,
    _CTR_INCR_BYTE5,
    VideoCallSession,
)


class TestCounterIncrementConstants:
    def test_ctr_incr_both_equals_byte4_plus_byte5(self):
        assert _CTR_INCR_BOTH == _CTR_INCR_BYTE4 + _CTR_INCR_BYTE5

    def test_ctr_incr_byte4_is_correct(self):
        assert _CTR_INCR_BYTE4 == 0x00010000

    def test_ctr_incr_byte5_is_correct(self):
        assert _CTR_INCR_BYTE5 == 0x01000000


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_called_even_when_rtp_receiver_stop_raises(self):
        """_cleanup must still clean up channels even if rtp_receiver.stop() raises.

        VideoCallSession no longer owns the TCP connection — it uses the
        coordinator's shared client and must NOT disconnect it.  Instead it
        calls remove_channel() for each video channel name.
        """
        from custom_components.comelit_man.video_call import VideoCallSession

        session = VideoCallSession.__new__(VideoCallSession)
        session._active = True
        session._timeout_task = None
        session._tcp_task = None
        session._ctpp_task = None
        session._rtsp_server = None
        session._external_rtsp = False
        session._owns_ctpp = True  # session owns CTPP → cleanup must remove it

        mock_receiver = MagicMock()
        mock_receiver.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
        session._rtp_receiver = mock_receiver

        mock_client = MagicMock()
        mock_client.remove_channel = MagicMock()
        session._client = mock_client

        # Should not raise
        await session._cleanup()

        # disconnect must NOT be called — the coordinator owns the connection
        mock_client.disconnect.assert_not_called()
        # Each video channel name must be removed
        removed = {call.args[0] for call in mock_client.remove_channel.call_args_list}
        assert "CTPP" in removed
        assert "UDPM" in removed
        assert session._active is False
        assert session._rtp_receiver is None

    @pytest.mark.asyncio
    async def test_cleanup_cancels_timeout_task(self):
        """_cleanup must cancel the timeout task."""
        session = VideoCallSession.__new__(VideoCallSession)
        session._active = True
        session._rtp_receiver = None
        session._client = None
        session._tcp_task = None
        session._ctpp_task = None
        session._rtsp_server = None
        session._external_rtsp = False

        cancelled = asyncio.Event()

        async def long_task():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        session._timeout_task = asyncio.create_task(long_task())
        await asyncio.sleep(0)  # let the task start before cleanup cancels it

        await session._cleanup()

        assert cancelled.is_set()
        assert session._timeout_task is None

    @pytest.mark.asyncio
    async def test_cleanup_cancels_ctpp_task(self):
        """_cleanup must cancel the ctpp monitor task."""
        session = VideoCallSession.__new__(VideoCallSession)
        session._active = True
        session._rtp_receiver = None
        session._client = None
        session._tcp_task = None
        session._timeout_task = None
        session._rtsp_server = None
        session._external_rtsp = False

        cancelled = asyncio.Event()

        async def long_task():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        session._ctpp_task = asyncio.create_task(long_task())
        await asyncio.sleep(0)

        await session._cleanup()

        assert cancelled.is_set()
        assert session._ctpp_task is None

    @pytest.mark.asyncio
    async def test_cleanup_is_idempotent(self):
        """Calling _cleanup twice must not raise."""
        session = VideoCallSession.__new__(VideoCallSession)
        session._active = True
        session._timeout_task = None
        session._tcp_task = None
        session._ctpp_task = None
        session._rtp_receiver = None
        session._client = None
        session._rtsp_server = None
        session._external_rtsp = False

        await session._cleanup()
        await session._cleanup()  # should not raise

    @pytest.mark.asyncio
    async def test_stop_callable_when_inactive(self):
        """stop() must not raise even when the session was never active."""
        session = VideoCallSession.__new__(VideoCallSession)
        session._active = False
        session._timeout_task = None
        session._tcp_task = None
        session._ctpp_task = None
        session._rtp_receiver = None
        session._client = None
        session._rtsp_server = None
        session._external_rtsp = False

        await session.stop()  # should not raise


class TestCtppMonitorLoop:
    """Tests for the CTPP monitor loop that ACKs device messages during a call."""

    def _make_session(self) -> VideoCallSession:
        session = VideoCallSession.__new__(VideoCallSession)
        session._active = True
        session._timeout_task = None
        session._tcp_task = None
        session._ctpp_task = None
        session._rtp_receiver = None
        session._client = None
        session._rtsp_server = None
        session._external_rtsp = False
        session._ctpp_lock = asyncio.Lock()
        session._call_counter = 0
        session._answer_handoff = None
        session._on_ring = None
        return session

    @pytest.mark.asyncio
    async def test_ctpp_keepalive_is_acked(self):
        """0x1840/0x0000 keepalive is ACKed with 0x1800 using transform(device_ts)."""
        import struct

        from custom_components.comelit_man.video_call import _transform_device_ts

        session = self._make_session()

        sent_data = []
        DEV_TS = 0x12345678
        mock_client = MagicMock()
        keepalive_body = struct.pack("<H", 0x1840) + struct.pack("<I", DEV_TS) + struct.pack(">H", 0x0000)

        call_count = 0

        async def mock_read_response(channel):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return keepalive_body
            session._active = False  # stop after first message
            raise TimeoutError()

        mock_client.read_response = mock_read_response
        mock_client.send_binary = AsyncMock(side_effect=lambda ch, data: sent_data.append(data))

        mock_ctpp = MagicMock()

        await session._ctpp_monitor_loop(
            mock_client,
            mock_ctpp,
            "SB0000061",
            "SB100001",
            0x10000000,
            rtpc1_server_id=0xABCD,
            media_req_id=0x1234,
        )

        assert len(sent_data) == 1
        prefix = struct.unpack_from("<H", sent_data[0], 0)[0]
        assert prefix == 0x1800
        # ACK timestamp must be transform(device_ts), not a counter
        ack_ts = struct.unpack_from("<I", sent_data[0], 2)[0]
        assert ack_ts == _transform_device_ts(DEV_TS)
        # self._call_counter must NOT be updated by an ACK
        assert session._call_counter == 0

    @pytest.mark.asyncio
    async def test_ctpp_call_end_triggers_inline_reestablish(self):
        """0x1840/0x0003 CALL_END should trigger _inline_reestablish, not stop session."""
        import struct

        session = self._make_session()

        mock_client = MagicMock()
        call_end_body = struct.pack("<H", 0x1840) + struct.pack("<I", 0x12345678) + struct.pack(">H", 0x0003)
        call_count = 0

        async def mock_read_response(channel):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return call_end_body
            session._active = False
            raise TimeoutError()

        mock_client.read_response = mock_read_response
        mock_client.send_binary = AsyncMock()

        reestablish_called = False

        async def mock_reestablish(*args, **kwargs):
            nonlocal reestablish_called
            reestablish_called = True
            return 0x10000000  # return updated counter

        session._inline_reestablish = mock_reestablish

        await session._ctpp_monitor_loop(
            mock_client,
            MagicMock(),
            "SB0000061",
            "SB100001",
            0x10000000,
            rtpc1_server_id=0xABCD,
            media_req_id=0x1234,
        )

        assert reestablish_called
        # Session should still be active after successful re-establishment
        # (loop ends only because mock_read_response set _active=False on next iteration)

    @pytest.mark.asyncio
    async def test_ctpp_call_end_reestablish_failure_keeps_loop_running(self):
        """If _inline_reestablish raises, a warning is logged but the loop keeps running."""
        import struct

        session = self._make_session()

        mock_client = MagicMock()
        call_end_body = struct.pack("<H", 0x1840) + struct.pack("<I", 0x12345678) + struct.pack(">H", 0x0003)
        call_count = 0

        async def mock_read_response(channel):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return call_end_body
            session._active = False
            raise TimeoutError()

        mock_client.read_response = mock_read_response
        mock_client.send_binary = AsyncMock()

        async def failing_reestablish(*args, **kwargs):
            raise RuntimeError("re-establish failed")

        session._inline_reestablish = failing_reestablish

        # Must not raise; loop exits on next read returning None
        await session._ctpp_monitor_loop(
            mock_client,
            MagicMock(),
            "SB0000061",
            "SB100001",
            0x10000000,
            rtpc1_server_id=0xABCD,
            media_req_id=0x1234,
        )

    @pytest.mark.asyncio
    async def test_ctpp_call_end_sub_000E_triggers_reestablish(self):
        """0x1840/0x0003/sub=0x000E (door-open triggered CALL_END) must
        trigger inline re-establish — same path as the periodic timer CALL_END.

        PCAP-verified: the device sends this sub-code when a door-open relay
        activates during video; the renewal sequence is required to keep video
        alive, just like a timer-triggered CALL_END (sub=0x0000).
        """
        import struct

        session = self._make_session()

        mock_client = MagicMock()
        call_end_body = (
            struct.pack("<H", 0x1840)
            + struct.pack("<I", 0xDEADBEEF)
            + struct.pack(">H", 0x0003)
            + struct.pack(">H", 0x000E)
            + b"\x00" * 8
        )
        call_count = 0

        async def mock_read_response(channel):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return call_end_body
            session._active = False
            raise TimeoutError()

        mock_client.read_response = mock_read_response
        mock_client.send_binary = AsyncMock()

        reestablish_called = False

        async def mock_reestablish(*args, **kwargs):
            nonlocal reestablish_called
            reestablish_called = True
            return 0x10000000

        session._inline_reestablish = mock_reestablish

        await session._ctpp_monitor_loop(
            mock_client,
            MagicMock(),
            "SB0000061",
            "SB100001",
            0x10000000,
            rtpc1_server_id=0xABCD,
            media_req_id=0x1234,
        )

        assert reestablish_called

    @pytest.mark.asyncio
    async def test_ctpp_device_acks_are_ignored(self):
        """0x1800 device ACKs should not trigger any response."""
        import struct

        session = self._make_session()

        sent_data = []

        mock_client = MagicMock()
        call_count = 0

        async def mock_read_response(channel):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return struct.pack("<H", 0x1800) + struct.pack("<I", 0x12345678) + struct.pack(">H", 0x0000)
            session._active = False
            raise TimeoutError()

        mock_client.read_response = mock_read_response
        mock_client.send_binary = AsyncMock(side_effect=lambda ch, data: sent_data.append(data))

        await session._ctpp_monitor_loop(
            mock_client,
            MagicMock(),
            "SB0000061",
            "SB100001",
            0x10000000,
            rtpc1_server_id=0xABCD,
            media_req_id=0x1234,
        )

        assert len(sent_data) == 0  # no response to device ACKs

    @pytest.mark.asyncio
    async def test_ctpp_0x1860_message_is_bare_acked(self):
        """0x1860 messages in the monitor loop must be ACKed with transform(device_ts).

        PCAP-verified: device sends 0x1860/0x000A during renewal; if
        _ack_device_rtpc_link missed it, the monitor loop must still ACK it
        using the correct transform format, not a counter.
        """
        import struct

        from custom_components.comelit_man.video_call import _transform_device_ts

        session = self._make_session()
        sent_data = []
        DEV_TS = 0xCAFEBABE

        mock_client = MagicMock()
        call_count = 0

        async def mock_read_response(channel):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return struct.pack("<H", 0x1860) + struct.pack("<I", DEV_TS) + struct.pack(">H", 0x000A)
            session._active = False
            raise TimeoutError()

        mock_client.read_response = mock_read_response
        mock_client.send_binary = AsyncMock(side_effect=lambda ch, data: sent_data.append(data))

        await session._ctpp_monitor_loop(
            mock_client,
            MagicMock(),
            "SB0000061",
            "SB100001",
            0x10000000,
            rtpc1_server_id=0xABCD,
            media_req_id=0x1234,
        )

        assert len(sent_data) == 1
        prefix = struct.unpack_from("<H", sent_data[0], 0)[0]
        assert prefix == 0x1800, f"Expected 0x1800 ACK, got 0x{prefix:04X}"
        ack_ts = struct.unpack_from("<I", sent_data[0], 2)[0]
        assert ack_ts == _transform_device_ts(DEV_TS)


class TestAckDeviceRtpcLink:
    """Tests for _ack_device_rtpc_link — accepts both 0x1840 and 0x1860 prefixes."""

    def _make_session(self) -> VideoCallSession:
        session = VideoCallSession.__new__(VideoCallSession)
        session._active = True
        session._timeout_task = None
        session._tcp_task = None
        session._ctpp_task = None
        session._rtp_receiver = None
        session._client = None
        session._rtsp_server = None
        session._external_rtsp = False
        session._ctpp_lock = asyncio.Lock()
        session._call_counter = 0
        return session

    @pytest.mark.asyncio
    async def test_accepts_0x1840_rtpc_link(self):
        """ACKs device's 0x1840/0x000A (initial-start RTPC link)."""
        import struct

        session = self._make_session()
        sent_data = []

        mock_client = MagicMock()
        call_count = 0

        async def mock_read_response(channel):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return struct.pack("<H", 0x1840) + struct.pack("<I", 0x11223344) + struct.pack(">H", 0x000A)
            raise TimeoutError()

        mock_client.read_response = mock_read_response
        mock_client.send_binary = AsyncMock(side_effect=lambda ch, data: sent_data.append(data))

        result = await session._ack_device_rtpc_link(mock_client, MagicMock(), "SB0000061", "SB100001", 0x10000000)

        assert len(sent_data) == 1
        prefix = struct.unpack_from("<H", sent_data[0], 0)[0]
        assert prefix == 0x1800

    @pytest.mark.asyncio
    async def test_accepts_0x1860_rtpc_link(self):
        """ACKs device's 0x1860/0x000A (renewal RTPC link, PCAP-verified).

        During inline re-establishment the device sends RTPC link with prefix
        0x1860 instead of 0x1840. The function must accept both and ACK them
        identically.
        """
        import struct

        session = self._make_session()
        sent_data = []

        mock_client = MagicMock()
        call_count = 0

        async def mock_read_response(channel):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return struct.pack("<H", 0x1860) + struct.pack("<I", 0x11223344) + struct.pack(">H", 0x000A)
            raise TimeoutError()

        mock_client.read_response = mock_read_response
        mock_client.send_binary = AsyncMock(side_effect=lambda ch, data: sent_data.append(data))

        result = await session._ack_device_rtpc_link(mock_client, MagicMock(), "SB0000061", "SB100001", 0x10000000)

        assert len(sent_data) == 1, "Device 0x1860/0x000A RTPC link was not ACKed"
        prefix = struct.unpack_from("<H", sent_data[0], 0)[0]
        assert prefix == 0x1800

    @pytest.mark.asyncio
    async def test_skips_0x1800_and_waits_for_000A(self):
        """0x1800 device ACKs before the RTPC link are skipped, not mistaken for it."""
        import struct

        session = self._make_session()
        sent_data = []

        mock_client = MagicMock()
        call_count = 0

        async def mock_read_response(channel):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return struct.pack("<H", 0x1800) + struct.pack("<I", 0) + struct.pack(">H", 0)
            if call_count == 2:
                return struct.pack("<H", 0x1860) + struct.pack("<I", 0x99887766) + struct.pack(">H", 0x000A)
            raise TimeoutError()

        mock_client.read_response = mock_read_response
        mock_client.send_binary = AsyncMock(side_effect=lambda ch, data: sent_data.append(data))

        await session._ack_device_rtpc_link(mock_client, MagicMock(), "SB0000061", "SB100001", 0x10000000)

        assert len(sent_data) == 1
        prefix = struct.unpack_from("<H", sent_data[0], 0)[0]
        assert prefix == 0x1800


class TestInlineReestablish:
    """Tests for _inline_reestablish — the CALL_END renewal sequence."""

    @pytest.mark.asyncio
    async def test_reestablish_sends_rtpc_link_and_video_config(self):
        """_inline_reestablish must send RTPC_LINK (0x1840/0x000A) followed
        by VIDEO_CONFIG (0x1840 prefix) to re-establish the media session.
        """
        import struct

        session = VideoCallSession.__new__(VideoCallSession)
        session._active = True
        session._timeout_task = None
        session._tcp_task = None
        session._ctpp_task = None
        session._rtp_receiver = None
        session._external_rtsp = False
        session._ctpp_lock = asyncio.Lock()
        session._call_counter = 0
        session._rtsp_server = None

        our_addr = "SB0000061"
        entrance_addr = "SB100001"
        rtpc1_server_id = 0xABCD
        media_req_id = 0x1234

        sent_data = []
        read_count = 0

        # Minimal device response sequence for _inline_reestablish:
        # ctpp_init_sequence reads up to 2 responses; call_init reads 1;
        # _run_codec_exchange reads until 0x0002 (call accepted).
        def make_0x1840(action: int) -> bytes:
            return struct.pack("<H", 0x1840) + struct.pack("<I", 0xDEADBEEF) + struct.pack(">H", action)

        def make_0x1800() -> bytes:
            return struct.pack("<H", 0x1800) + struct.pack("<I", 0) + struct.pack(">H", 0)

        # Response sequence:
        # [0-1] ctpp_init_sequence drain (2 reads)
        # [2]   call_init ACK read
        # [3]   codec exchange: 0x0002 = call accepted
        # [4+]  _ack_device_rtpc_link: returns None (timeout suppressed)
        responses = [
            make_0x1800(),  # ctpp_init drain 1
            make_0x1800(),  # ctpp_init drain 2
            make_0x1840(0x0001),  # call_init ACK (any action)
            make_0x1840(0x0002),  # codec exchange: call accepted
            None,  # _ack_device_rtpc_link timeout
        ]

        async def mock_read_response(channel):
            nonlocal read_count
            if read_count < len(responses):
                resp = responses[read_count]
                read_count += 1
                if resp is None:
                    raise TimeoutError()
                return resp
            raise TimeoutError()

        mock_client = MagicMock()
        mock_client.read_response = mock_read_response
        mock_client.send_binary = AsyncMock(side_effect=lambda ch, data: sent_data.append(data))

        # register_placeholder_channel must return a channel with an open_event
        placeholder = MagicMock()
        placeholder.open_event = asyncio.Event()
        placeholder.open_event.set()  # simulate device opened it immediately
        placeholder.server_channel_id = 0x9999
        mock_client.register_placeholder_channel = MagicMock(return_value=placeholder)

        mock_ctpp = MagicMock()

        # Provide a fixed timestamp so we can compute expected messages
        fixed_ts = 0x01020304
        session._ts = lambda: fixed_ts

        await session._inline_reestablish(
            mock_client,
            mock_ctpp,
            our_addr,
            entrance_addr,
            rtpc1_server_id,
            media_req_id,
            call_counter=0x00010000,
        )

        sent_prefixes = [struct.unpack_from("<H", d, 0)[0] for d in sent_data]
        sent_actions = [struct.unpack_from(">H", d, 6)[0] if len(d) >= 8 else 0 for d in sent_data]

        # RTPC_LINK: prefix 0x1840, action 0x000A
        ACTION_RTPC_LINK = 0x000A
        rtpc_link_messages = [
            d
            for d in sent_data
            if len(d) >= 8
            and struct.unpack_from("<H", d, 0)[0] == 0x1840
            and struct.unpack_from(">H", d, 6)[0] == ACTION_RTPC_LINK
        ]
        assert rtpc_link_messages, "No RTPC_LINK message (0x1840/0x000A) was sent"

        # VIDEO_CONFIG: prefix 0x1840 (encode_video_config)
        # Verify it matches what encode_video_config would produce (not _resp).
        # encode_video_config uses prefix 0x1840; encode_video_config_resp uses 0x1860.
        video_config_messages = [
            d
            for d in sent_data
            if len(d) >= 8
            and struct.unpack_from("<H", d, 0)[0] == 0x1840
            and struct.unpack_from(">H", d, 6)[0] not in (ACTION_RTPC_LINK, 0x0000, 0x0070)
        ]
        assert video_config_messages, "No VIDEO_CONFIG message (0x1840) was sent during renewal"


# ---------------------------------------------------------------------------
# Group A — async_open_door_on_ctpp
# ---------------------------------------------------------------------------


class TestAsyncOpenDoorOnCtpp:
    """Tests for async_open_door_on_ctpp — door open on the active video CTPP."""

    def _make_session(self) -> VideoCallSession:
        session = VideoCallSession.__new__(VideoCallSession)
        session._active = True
        session._call_counter = 0x10000000
        session._ctpp_lock = asyncio.Lock()
        mock_client = MagicMock()
        mock_client.send_binary = AsyncMock()
        session._client = mock_client
        return session

    @pytest.mark.asyncio
    async def test_happy_path_sends_door_open(self):
        """Happy path: sends door-open payload and increments counter."""
        session = self._make_session()
        session._client.get_channel = MagicMock(return_value=MagicMock())

        await session.async_open_door_on_ctpp("SB0000061", "SB100001", 0)

        session._client.send_binary.assert_called_once()
        assert session._call_counter == 0x10000000 + _CTR_INCR_BYTE4

    @pytest.mark.asyncio
    async def test_raises_when_ctpp_none(self):
        """Raises RuntimeError when CTPP channel is not open."""
        session = self._make_session()
        session._client.get_channel = MagicMock(return_value=None)

        with pytest.raises(RuntimeError, match="No active video CTPP channel"):
            await session.async_open_door_on_ctpp("SB0000061", "SB100001", 0)

    @pytest.mark.asyncio
    async def test_raises_when_not_active(self):
        """Raises RuntimeError when the session is not active."""
        session = self._make_session()
        session._active = False
        session._client.get_channel = MagicMock(return_value=MagicMock())

        with pytest.raises(RuntimeError, match="No active video CTPP channel"):
            await session.async_open_door_on_ctpp("SB0000061", "SB100001", 0)


# ---------------------------------------------------------------------------
# Group B — _auto_timeout_loop
# ---------------------------------------------------------------------------


class TestAutoTimeoutLoop:
    """Tests for _auto_timeout_loop — session auto-stop after VIDEO_SESSION_TIMEOUT."""

    def _make_session(self, on_timeout=None) -> VideoCallSession:
        session = VideoCallSession.__new__(VideoCallSession)
        session._active = True
        session._timeout_task = None
        session._tcp_task = None
        session._ctpp_task = None
        session._rtp_receiver = None
        session._rtsp_server = None
        session._external_rtsp = False
        session._client = None
        session._on_timeout = on_timeout
        return session

    @pytest.mark.asyncio
    async def test_timeout_fires_cleanup_and_on_timeout(self):
        """When sleep completes, _cleanup runs and on_timeout callback fires."""
        called = []
        session = self._make_session(on_timeout=lambda: called.append(True))

        with patch("custom_components.comelit_man.video_call.VIDEO_SESSION_TIMEOUT", 0):
            await session._auto_timeout_loop()

        assert called == [True]
        assert session._active is False

    @pytest.mark.asyncio
    async def test_cancelled_error_exits_cleanly(self):
        """CancelledError during sleep exits without raising."""
        session = self._make_session()
        task = asyncio.create_task(session._auto_timeout_loop())
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert task.done()


# ---------------------------------------------------------------------------
# Group C — _run_codec_exchange branches
# ---------------------------------------------------------------------------


class TestRunCodecExchange:
    """Tests for _run_codec_exchange branches not hit by inline_reestablish tests."""

    @staticmethod
    def _make_client(responses: list) -> MagicMock:
        mock_client = MagicMock()
        it = iter(responses)

        async def mock_read(channel):
            val = next(it, None)
            if val is None:
                raise TimeoutError()
            return val

        mock_client.read_response = mock_read
        mock_client.send_binary = AsyncMock()
        return mock_client

    @pytest.mark.asyncio
    async def test_breaks_on_timeout(self):
        """Timeout (no response) breaks the loop and returns the counter unchanged."""
        session = VideoCallSession.__new__(VideoCallSession)
        mock_client = self._make_client([None])

        result = await session._run_codec_exchange(mock_client, MagicMock(), "SB0000061", "SB100001", 0x1234)

        assert result == 0x1234
        mock_client.send_binary.assert_not_called()

    @pytest.mark.asyncio
    async def test_continues_on_0x1800(self):
        """0x1800 device ACKs are silently skipped; no send_binary called."""
        import struct

        session = VideoCallSession.__new__(VideoCallSession)
        skip_msg = struct.pack("<H", 0x1800) + struct.pack("<I", 0) + struct.pack(">H", 0)
        mock_client = self._make_client([skip_msg, None])

        await session._run_codec_exchange(mock_client, MagicMock(), "SB0000061", "SB100001", 0)

        mock_client.send_binary.assert_not_called()

    @pytest.mark.asyncio
    async def test_action_0x0008_sends_ack_with_ctr_incr_both(self):
        """0x1840/0x0008 sends an ACK (counter += _CTR_INCR_BOTH)."""
        import struct

        session = VideoCallSession.__new__(VideoCallSession)
        msg_0008 = struct.pack("<H", 0x1840) + struct.pack("<I", 0) + struct.pack(">H", 0x0008)
        msg_accept = struct.pack("<H", 0x1840) + struct.pack("<I", 0) + struct.pack(">H", 0x0002)
        mock_client = self._make_client([msg_0008, msg_accept])

        await session._run_codec_exchange(mock_client, MagicMock(), "SB0000061", "SB100001", 0)

        # ACK for 0x0008 + ACK for 0x0002 (call accepted)
        assert mock_client.send_binary.call_count == 2

    @pytest.mark.asyncio
    async def test_else_branch_sends_ack_for_unknown_action(self):
        """Unknown 0x1840 action hits the else branch and sends an ACK."""
        import struct

        session = VideoCallSession.__new__(VideoCallSession)
        unknown_msg = struct.pack("<H", 0x1840) + struct.pack("<I", 0) + struct.pack(">H", 0x0099)
        mock_client = self._make_client([unknown_msg, None])

        await session._run_codec_exchange(mock_client, MagicMock(), "SB0000061", "SB100001", 0)

        assert mock_client.send_binary.call_count == 1


# ---------------------------------------------------------------------------
# Group D — _tcp_video_loop exception paths
# ---------------------------------------------------------------------------


class TestTcpVideoLoop:
    """Tests for _tcp_video_loop CancelledError and generic exception paths."""

    @pytest.mark.asyncio
    async def test_cancelled_error_exits_silently(self):
        """CancelledError inside the loop exits without raising."""
        mock_client = MagicMock()

        async def mock_read(channel, timeout=2.0):
            raise asyncio.CancelledError()

        mock_client.read_response = mock_read
        mock_receiver = MagicMock()
        mock_receiver.running = True

        await VideoCallSession._tcp_video_loop(mock_client, MagicMock(), mock_receiver)

    @pytest.mark.asyncio
    async def test_generic_exception_logs_and_exits(self):
        """Generic exception inside the loop logs debug and exits without raising."""
        mock_client = MagicMock()

        async def mock_read(channel, timeout=2.0):
            raise RuntimeError("network error")

        mock_client.read_response = mock_read
        mock_receiver = MagicMock()
        mock_receiver.running = True

        await VideoCallSession._tcp_video_loop(mock_client, MagicMock(), mock_receiver)

    @pytest.mark.asyncio
    async def test_valid_data_forwarded_to_receiver(self):
        """Valid RTP data (>= 12 bytes) is forwarded to receiver.receive_tcp_rtp."""
        mock_client = MagicMock()
        call_count = 0
        valid_rtp = bytes(20)  # 20 zero bytes >= 12

        async def mock_read(channel, timeout=2.0):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return valid_rtp
            raise asyncio.CancelledError()

        mock_client.read_response = mock_read
        mock_receiver = MagicMock()
        mock_receiver.running = True

        await VideoCallSession._tcp_video_loop(mock_client, MagicMock(), mock_receiver)

        mock_receiver.receive_tcp_rtp.assert_called_once_with(valid_rtp)


# ---------------------------------------------------------------------------
# Group E — _ctpp_monitor_loop rare paths
# ---------------------------------------------------------------------------


class TestCtppMonitorLoopRarePaths:
    """Tests for _ctpp_monitor_loop paths not covered in TestCtppMonitorLoop."""

    def _make_session(self, on_call_end=None) -> VideoCallSession:
        session = VideoCallSession.__new__(VideoCallSession)
        session._active = True
        session._timeout_task = None
        session._tcp_task = None
        session._ctpp_task = None
        session._rtp_receiver = None
        session._client = None
        session._rtsp_server = None
        session._external_rtsp = False
        session._ctpp_lock = asyncio.Lock()
        session._call_counter = 0
        session._answer_handoff = None
        session._on_ring = None
        session._on_call_end = on_call_end
        return session

    @pytest.mark.asyncio
    async def test_unexpected_msg_type_is_logged(self):
        """Unknown msg_type hits the else debug-log branch; no send_binary."""
        import struct

        session = self._make_session()
        mock_client = MagicMock()
        call_count = 0

        async def mock_read(channel):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return struct.pack("<H", 0x9999) + struct.pack("<I", 0) + struct.pack(">H", 0)
            session._active = False
            raise TimeoutError()

        mock_client.read_response = mock_read
        mock_client.send_binary = AsyncMock()

        await session._ctpp_monitor_loop(
            mock_client,
            MagicMock(),
            "SB0000061",
            "SB100001",
            0,
            rtpc1_server_id=0xABCD,
            media_req_id=0x1234,
        )

        mock_client.send_binary.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_call_end_callback_fires_when_reestablish_fails(self):
        """on_call_end is called and the loop exits when _inline_reestablish raises."""
        import struct

        fired = []
        session = self._make_session(on_call_end=lambda: fired.append(True))
        mock_client = MagicMock()
        call_count = 0

        call_end_body = struct.pack("<H", 0x1840) + struct.pack("<I", 0) + struct.pack(">H", 0x0003)

        async def mock_read(channel):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return call_end_body
            raise TimeoutError()

        mock_client.read_response = mock_read
        mock_client.send_binary = AsyncMock()

        async def failing_reestablish(*args, **kwargs):
            raise RuntimeError("re-establish failed")

        session._inline_reestablish = failing_reestablish

        await session._ctpp_monitor_loop(
            mock_client,
            MagicMock(),
            "SB0000061",
            "SB100001",
            0,
            rtpc1_server_id=0xABCD,
            media_req_id=0x1234,
        )

        assert fired == [True]
        assert session._active is False


# ---------------------------------------------------------------------------
# Group F0 — _send_answer_sequence (single peer/accept message)
# ---------------------------------------------------------------------------


class TestSendAnswerSequence:
    """Tests for _send_answer_sequence — single 0x1840/0x0070 peer/accept message.

    Live device test confirmed: sending more messages (video_reconfig, config_ack)
    does not trigger audio on HA-initiated calls. Device only sends PCMA during
    inbound calls triggered by a visitor pressing the doorbell.
    """

    @staticmethod
    def _make_session(initial_counter: int = 0x01000000) -> VideoCallSession:
        session = VideoCallSession.__new__(VideoCallSession)
        session._ctpp_lock = asyncio.Lock()
        session._call_counter = initial_counter
        return session

    @pytest.mark.asyncio
    async def test_sends_exactly_one_message(self):
        """Exactly one send_binary call — the peer/accept message."""
        session = self._make_session()
        sent: list[bytes] = []
        mock_client = MagicMock()
        mock_client.send_binary = AsyncMock(side_effect=lambda ch, data: sent.append(data))

        await session._send_answer_sequence(mock_client, MagicMock(), "SB0000061", "SB100001", "SB000006", 0, 0x1234)

        assert len(sent) == 1, f"Expected 1 message, got {len(sent)}"

    @pytest.mark.asyncio
    async def test_message_is_peer_accept_0x0070(self):
        """The single message must be peer/accept (action 0x0070 at bytes[8:10])."""
        import struct

        session = self._make_session()
        sent: list[bytes] = []
        mock_client = MagicMock()
        mock_client.send_binary = AsyncMock(side_effect=lambda ch, data: sent.append(data))

        await session._send_answer_sequence(mock_client, MagicMock(), "SB0000061", "SB100001", "SB000006", 0, 0x1234)

        # encode_answer_peer format: inner_len at bytes[6:8], action at bytes[8:10]
        action = struct.unpack_from(">H", sent[0], 8)[0]
        assert action == 0x0070, f"Expected peer/accept action 0x0070, got 0x{action:04X}"

    @pytest.mark.asyncio
    async def test_message_uses_0x1840_prefix(self):
        """Initial (non-renewal) peer/accept uses prefix 0x1840."""
        import struct

        session = self._make_session()
        sent: list[bytes] = []
        mock_client = MagicMock()
        mock_client.send_binary = AsyncMock(side_effect=lambda ch, data: sent.append(data))

        await session._send_answer_sequence(mock_client, MagicMock(), "SB0000061", "SB100001", "SB000006", 0, 0x1234)

        prefix = struct.unpack_from("<H", sent[0], 0)[0]
        assert prefix == 0x1840, f"Expected prefix 0x1840, got 0x{prefix:04X}"

    @pytest.mark.asyncio
    async def test_counter_increments_once(self):
        """_call_counter must increment by exactly one _CTR_INCR_BYTE4."""
        session = self._make_session(initial_counter=0x00500000)
        mock_client = MagicMock()
        mock_client.send_binary = AsyncMock()

        await session._send_answer_sequence(mock_client, MagicMock(), "SB0000061", "SB100001", "SB000006", 0, 0x1234)

        assert session._call_counter == 0x00500000 + _CTR_INCR_BYTE4

    @pytest.mark.asyncio
    async def test_uses_entrance_addr_as_callee(self):
        """Peer/accept must address entrance_addr as the callee."""
        session = self._make_session()
        sent: list[bytes] = []
        mock_client = MagicMock()
        mock_client.send_binary = AsyncMock(side_effect=lambda ch, data: sent.append(data))

        await session._send_answer_sequence(mock_client, MagicMock(), "SB0000061", "SB100001", "SB000006", 0, 0x1234)

        assert b"SB100001\x00\x00" in sent[0], "Peer/accept must use entrance_addr as callee"

    @pytest.mark.asyncio
    async def test_uses_live_call_counter_not_stale_param(self):
        """Must use self._call_counter (updated by monitor loop), not the stale call_counter param."""
        session = self._make_session(initial_counter=0x00900000)
        sent: list[bytes] = []
        mock_client = MagicMock()
        mock_client.send_binary = AsyncMock(side_effect=lambda ch, data: sent.append(data))

        # Pass a stale call_counter that differs from session._call_counter
        stale_counter = 0x00100000
        await session._send_answer_sequence(
            mock_client, MagicMock(), "SB0000061", "SB100001", "SB000006", stale_counter, 0x1234
        )

        import struct

        embedded_ts = struct.unpack_from("<I", sent[0], 2)[0]
        expected_ts = 0x00900000 + _CTR_INCR_BYTE4
        assert embedded_ts == expected_ts, (
            f"Embedded timestamp 0x{embedded_ts:08X} should be based on live counter "
            f"0x{expected_ts:08X}, not stale param 0x{stale_counter + _CTR_INCR_BYTE4:08X}"
        )


# ---------------------------------------------------------------------------
# Group F — _run_answer_sequence wrapper exception
# ---------------------------------------------------------------------------


class TestRunAnswerSequenceWrapper:
    """Tests for _run_answer_sequence — fire-and-forget wrapper that swallows exceptions."""

    @pytest.mark.asyncio
    async def test_exception_from_send_is_swallowed(self):
        """Exception in _send_answer_sequence is caught and logged, not re-raised."""
        session = VideoCallSession.__new__(VideoCallSession)
        session._ctpp_lock = asyncio.Lock()
        session._call_counter = 0

        async def failing_send(*args, **kwargs):
            raise RuntimeError("send failed")

        session._send_answer_sequence = failing_send

        # Must not raise
        await session._run_answer_sequence(
            MagicMock(),
            MagicMock(),
            "SB0000061",
            "SB100001",
            "SB000006",
            0x10000000,
            0x1234,
        )


# ---------------------------------------------------------------------------
# Group G — _cleanup with _owns_ctpp=False
# ---------------------------------------------------------------------------


class TestCleanupCtppSkip:
    """Tests for _cleanup when _owns_ctpp=False — coordinator CTPP must be preserved."""

    @pytest.mark.asyncio
    async def test_ctpp_and_cspb_not_removed_when_not_owned(self):
        """CTPP and CSPB are skipped in remove_channel when the session did not open them."""
        session = VideoCallSession.__new__(VideoCallSession)
        session._active = True
        session._timeout_task = None
        session._tcp_task = None
        session._ctpp_task = None
        session._rtp_receiver = None
        session._rtsp_server = None
        session._external_rtsp = False
        session._owns_ctpp = False

        mock_client = MagicMock()
        mock_client.remove_channel = MagicMock()
        session._client = mock_client

        await session._cleanup()

        removed = {call.args[0] for call in mock_client.remove_channel.call_args_list}
        assert "CTPP" not in removed
        assert "CSPB" not in removed
        assert "UDPM" in removed


# ---------------------------------------------------------------------------
# Group H — start() branches
# ---------------------------------------------------------------------------


class TestStart:
    """Tests for VideoCallSession.start() — remaining uncovered branches.

    Coverage targets (18 stmts):
      line 225   — caller_address=None warning
      lines 241-251 — reuse existing CTPP (owns_ctpp=False, skip ctpp_init)
      lines 247-248 — open CSPB when CTPP reused but CSPB missing
      lines 308-309 — reuse external RTSP server (reset instead of create)
      lines 415-417 — device RTPC timeout → VideoCallError
      lines 468-470 — wait_for_first_video=False → no-media warning
      line 502     — auto_timeout=True creates timeout task
      lines 510-512 — outer except wraps any error as VideoCallError
    """

    @staticmethod
    def _make_mocks(
        *,
        caller_address: str | None = "SB100001",
        ctpp_exists: bool = False,
        cspb_exists: bool = True,
        device_rtpc_timeout: bool = False,
        no_media: bool = False,
    ):
        """Return (config, mock_client, mock_receiver, mock_rtsp) for start()."""
        import struct

        config = MagicMock()
        config.apt_address = "SB000006"
        config.apt_subaddress = "1"
        config.caller_address = caller_address

        ctpp_ch = MagicMock()
        ctpp_ch.server_channel_id = 0x0010
        cspb_ch = MagicMock() if cspb_exists else None

        def get_channel(name):
            if name == "CTPP":
                return ctpp_ch if ctpp_exists else None
            if name == "CSPB":
                return cspb_ch
            return None

        channel_id_counter = [0x0011]

        async def mock_open_channel(name, channel_type, extra_data=None, trailing_byte=0, wire_name=None):
            ch = MagicMock()
            ch.server_channel_id = channel_id_counter[0]
            channel_id_counter[0] += 1
            ch.open_response_body = b"\x00" * 20
            return ch

        placeholder = MagicMock()
        placeholder.open_event = asyncio.Event()
        if not device_rtpc_timeout:
            placeholder.open_event.set()
        placeholder.server_channel_id = 0xAAAA

        # Reads consumed by start() in order:
        # 1. call-init ACK (resp1, line 336)
        # 2. codec exchange until 0x0002 (line 353)
        # 3. _ack_device_rtpc_link (0x000A, line 423)
        # 4+ None → _ctpp_monitor_loop exits
        responses = [
            b"\x00" * 10,
            struct.pack("<H", 0x1840) + struct.pack("<I", 0) + struct.pack(">H", 0x0002),
            struct.pack("<H", 0x1840) + struct.pack("<I", 0) + struct.pack(">H", 0x000A),
        ]
        resp_iter = iter(responses)

        async def mock_read(channel):
            val = next(resp_iter, None)
            if val is None:
                raise TimeoutError()
            return val

        mock_client = MagicMock()
        mock_client.host = "192.168.1.1"
        mock_client.port = 64100
        mock_client.get_channel = MagicMock(side_effect=get_channel)
        mock_client.open_channel = mock_open_channel
        mock_client.send_binary = AsyncMock()
        mock_client.read_response = mock_read
        mock_client.register_placeholder_channel = MagicMock(return_value=placeholder)

        mock_receiver = MagicMock()
        mock_receiver.running = False  # _tcp_video_loop exits immediately
        mock_receiver.start_control = AsyncMock()
        mock_receiver.start_media = AsyncMock()
        mock_receiver.stop = AsyncMock()
        if no_media:
            mock_receiver.wait_for_first_video = AsyncMock(side_effect=TimeoutError())
        else:
            mock_receiver.wait_for_first_video = AsyncMock()
        mock_receiver.udp_media_packet_count = 0
        mock_receiver.tcp_media_packet_count = 0

        mock_rtsp = MagicMock()
        mock_rtsp.start = AsyncMock()
        mock_rtsp.stop = AsyncMock()
        mock_rtsp.nal_queue = asyncio.Queue()
        mock_rtsp.audio_queue = asyncio.Queue()
        mock_rtsp.rtp_queue = asyncio.Queue()

        return config, mock_client, mock_receiver, mock_rtsp

    def _make_session(self, config, mock_client, *, auto_timeout: bool = False, rtsp_server=None) -> VideoCallSession:
        """Build a VideoCallSession from mocks, bypassing __init__."""
        session = VideoCallSession.__new__(VideoCallSession)
        session._client = mock_client
        session._config = config
        session._auto_timeout = auto_timeout
        session._external_rtsp = rtsp_server is not None
        session._rtsp_server = rtsp_server
        session._active = False
        session._owns_ctpp = False
        session._timeout_task = None
        session._tcp_task = None
        session._ctpp_task = None
        session._rtp_receiver = None
        session._call_counter = 0
        session._ctpp_lock = asyncio.Lock()
        session._on_call_end = None
        session._on_timeout = None
        return session

    @pytest.mark.asyncio
    async def test_caller_address_none_uses_our_addr_as_entrance(self):
        """When caller_address is None a warning is logged and start() still succeeds.

        Coverage: line 225 (_LOGGER.warning when entrance-address-book is empty).
        """
        config, mock_client, mock_receiver, mock_rtsp = self._make_mocks(caller_address=None)
        session = self._make_session(config, mock_client)
        mock_ctpp_init = AsyncMock()

        with (
            patch("custom_components.comelit_man.video_call.RtpReceiver", return_value=mock_receiver),
            patch("custom_components.comelit_man.video_call.LocalRtspServer", return_value=mock_rtsp),
            patch("custom_components.comelit_man.video_call.ctpp_init_sequence", mock_ctpp_init),
        ):
            result = await session.start()

        assert result is mock_receiver
        assert session.active is True
        await session.stop()

    @pytest.mark.asyncio
    async def test_reuses_existing_ctpp_skips_ctpp_init(self):
        """When a CTPP channel already exists start() reuses it without re-running ctpp_init.

        Coverage: lines 241-251 (reuse-CTPP branch, owns_ctpp=False).
        """
        config, mock_client, mock_receiver, mock_rtsp = self._make_mocks(ctpp_exists=True)
        session = self._make_session(config, mock_client)
        mock_ctpp_init = AsyncMock()

        with (
            patch("custom_components.comelit_man.video_call.RtpReceiver", return_value=mock_receiver),
            patch("custom_components.comelit_man.video_call.LocalRtspServer", return_value=mock_rtsp),
            patch("custom_components.comelit_man.video_call.ctpp_init_sequence", mock_ctpp_init),
        ):
            result = await session.start()

        mock_ctpp_init.assert_not_called()
        assert session._owns_ctpp is False
        assert result is mock_receiver
        await session.stop()

    @pytest.mark.asyncio
    async def test_opens_cspb_when_missing_and_ctpp_reused(self):
        """When CTPP is reused but CSPB is missing start() opens CSPB.

        Coverage: lines 247-248 (open CSPB sub-branch inside reuse path).
        """
        config, mock_client, mock_receiver, mock_rtsp = self._make_mocks(ctpp_exists=True, cspb_exists=False)
        session = self._make_session(config, mock_client)

        opened: list[str] = []
        original_open = mock_client.open_channel

        async def tracking_open(name, channel_type, extra_data=None, trailing_byte=0, wire_name=None):
            opened.append(name)
            return await original_open(
                name,
                channel_type,
                extra_data=extra_data,
                trailing_byte=trailing_byte,
                wire_name=wire_name,
            )

        mock_client.open_channel = tracking_open

        with (
            patch("custom_components.comelit_man.video_call.RtpReceiver", return_value=mock_receiver),
            patch("custom_components.comelit_man.video_call.LocalRtspServer", return_value=mock_rtsp),
        ):
            await session.start()

        assert "CSPB" in opened
        await session.stop()

    @pytest.mark.asyncio
    async def test_reuses_external_rtsp_server(self):
        """When _external_rtsp=True start() resets the existing RTSP server instead of creating one.

        Coverage: lines 308-309 (reuse coordinator RTSP branch).
        """
        config, mock_client, mock_receiver, mock_rtsp = self._make_mocks()
        session = self._make_session(config, mock_client, rtsp_server=mock_rtsp)
        mock_ctpp_init = AsyncMock()

        with (
            patch("custom_components.comelit_man.video_call.RtpReceiver", return_value=mock_receiver),
            patch("custom_components.comelit_man.video_call.ctpp_init_sequence", mock_ctpp_init),
        ):
            result = await session.start()

        mock_rtsp.reset.assert_called_once()
        mock_rtsp.start.assert_not_called()
        assert result is mock_receiver
        await session.stop()

    @pytest.mark.asyncio
    async def test_rtpc_timeout_raises_video_call_error(self):
        """TimeoutError waiting for device RTPC open event → VideoCallError.

        Coverage: lines 415-417, 510-512.
        """
        config, mock_client, mock_receiver, mock_rtsp = self._make_mocks(device_rtpc_timeout=True)
        session = self._make_session(config, mock_client)
        mock_ctpp_init = AsyncMock()

        with (
            patch("custom_components.comelit_man.video_call.RtpReceiver", return_value=mock_receiver),
            patch("custom_components.comelit_man.video_call.LocalRtspServer", return_value=mock_rtsp),
            patch("custom_components.comelit_man.video_call.ctpp_init_sequence", mock_ctpp_init),
            patch("custom_components.comelit_man.video_call.VIDEO_RESPONSE_TIMEOUT", 0.001),
        ):
            with pytest.raises(VideoCallError):
                await session.start()

    @pytest.mark.asyncio
    async def test_no_media_does_not_raise(self):
        """When wait_for_first_video returns False a warning is logged but no error is raised.

        Coverage: lines 468-470 (no-media warning branch).
        """
        config, mock_client, mock_receiver, mock_rtsp = self._make_mocks(no_media=True)
        session = self._make_session(config, mock_client)
        mock_ctpp_init = AsyncMock()

        with (
            patch("custom_components.comelit_man.video_call.RtpReceiver", return_value=mock_receiver),
            patch("custom_components.comelit_man.video_call.LocalRtspServer", return_value=mock_rtsp),
            patch("custom_components.comelit_man.video_call.ctpp_init_sequence", mock_ctpp_init),
        ):
            result = await session.start()

        assert result is mock_receiver
        await session.stop()

    @pytest.mark.asyncio
    async def test_auto_timeout_creates_timeout_task(self):
        """When auto_timeout=True a timeout task is created after start() succeeds.

        Coverage: line 502 (auto_timeout branch).
        """
        config, mock_client, mock_receiver, mock_rtsp = self._make_mocks()
        session = self._make_session(config, mock_client, auto_timeout=True)
        mock_ctpp_init = AsyncMock()

        with (
            patch("custom_components.comelit_man.video_call.RtpReceiver", return_value=mock_receiver),
            patch("custom_components.comelit_man.video_call.LocalRtspServer", return_value=mock_rtsp),
            patch("custom_components.comelit_man.video_call.ctpp_init_sequence", mock_ctpp_init),
        ):
            await session.start()

        assert session._timeout_task is not None
        await session.stop()

    @pytest.mark.asyncio
    async def test_exception_raises_video_call_error(self):
        """Any unhandled exception during start() is wrapped and re-raised as VideoCallError.

        Coverage: lines 510-512 (outer except handler).
        """
        config, mock_client, mock_receiver, mock_rtsp = self._make_mocks()
        session = self._make_session(config, mock_client)

        mock_client.send_binary = AsyncMock(side_effect=RuntimeError("inject failure"))

        with (
            patch("custom_components.comelit_man.video_call.RtpReceiver", return_value=mock_receiver),
            patch("custom_components.comelit_man.video_call.LocalRtspServer", return_value=mock_rtsp),
            patch("custom_components.comelit_man.video_call.ctpp_init_sequence", AsyncMock()),
        ):
            with pytest.raises(VideoCallError):
                await session.start()


class TestStartInbound:
    """Tests for VideoCallSession.start_inbound()."""

    @staticmethod
    def _make_mocks():
        """Return (config, mock_client, mock_receiver, mock_rtsp) for start_inbound()."""

        config = MagicMock()
        config.apt_address = "SB000006"
        config.apt_subaddress = "1"

        ctpp_ch = MagicMock()
        ctpp_ch.server_channel_id = 0x0010
        ctpp_ch.response_queue = asyncio.Queue()

        channel_id_counter = [0x0020]

        async def mock_open_channel(name, channel_type, extra_data=None, trailing_byte=0, wire_name=None):
            ch = MagicMock()
            ch.server_channel_id = channel_id_counter[0]
            channel_id_counter[0] += 1
            ch.open_response_body = b"\x00" * 20
            ch.response_queue = asyncio.Queue()
            return ch

        device_rtpc = MagicMock()
        device_rtpc.open_event = asyncio.Event()
        device_rtpc.open_event.set()
        device_rtpc.server_channel_id = 0xBBBB

        mock_client = MagicMock()
        mock_client.host = "192.168.1.1"
        mock_client.port = 64100
        mock_client.get_channel = MagicMock(return_value=ctpp_ch)
        mock_client.open_channel = mock_open_channel
        mock_client.send_binary = AsyncMock()
        mock_client.register_placeholder_channel = MagicMock(return_value=device_rtpc)

        mock_receiver = MagicMock()
        mock_receiver.running = False
        mock_receiver.start_control = AsyncMock()
        mock_receiver.start_media = AsyncMock()
        mock_receiver.stop = AsyncMock()
        mock_receiver.wait_for_first_video = AsyncMock()
        mock_receiver.udp_media_packet_count = 0
        mock_receiver.tcp_media_packet_count = 0

        mock_rtsp = MagicMock()
        mock_rtsp.start = AsyncMock()
        mock_rtsp.stop = AsyncMock()
        mock_rtsp.nal_queue = asyncio.Queue()
        mock_rtsp.audio_queue = asyncio.Queue()
        mock_rtsp.rtp_queue = asyncio.Queue()

        return config, mock_client, mock_receiver, mock_rtsp, ctpp_ch

    def _make_session(self, config, mock_client, *, rtsp_server=None) -> VideoCallSession:
        session = VideoCallSession.__new__(VideoCallSession)
        session._client = mock_client
        session._config = config
        session._auto_timeout = False
        session._external_rtsp = rtsp_server is not None
        session._rtsp_server = rtsp_server
        session._active = False
        session._device_rtpc_req_id = 0
        session._owns_ctpp = False
        session._timeout_task = None
        session._tcp_task = None
        session._ctpp_task = None
        session._rtp_receiver = None
        session._call_counter = 0
        session._ctpp_lock = asyncio.Lock()
        session._on_call_end = None
        session._on_timeout = None
        session._inbound_device_rtpc = None
        session._answer_handoff = None
        session._on_ring = None
        return session

    @pytest.mark.asyncio
    async def test_start_inbound_returns_receiver_and_sets_active(self):
        """start_inbound returns the receiver and sets session.active=True."""
        config, mock_client, mock_receiver, mock_rtsp, ctpp_ch = self._make_mocks()
        session = self._make_session(config, mock_client)

        # Unblock the response_queue drains immediately (empty queues = TimeoutError handled)
        with (
            patch("custom_components.comelit_man.video_call.RtpReceiver", return_value=mock_receiver),
            patch("custom_components.comelit_man.video_call.LocalRtspServer", return_value=mock_rtsp),
        ):
            result = await session.start_inbound("SB100001", 0x12345678)

        assert result is mock_receiver
        assert session.active is True
        await session.stop()

    @pytest.mark.asyncio
    async def test_start_inbound_stores_inbound_device_rtpc_not_req_id(self):
        """start_inbound saves the device RTPC placeholder on self._inbound_device_rtpc
        but does NOT set _device_rtpc_req_id — that's deferred to answer_inbound()."""
        config, mock_client, mock_receiver, mock_rtsp, ctpp_ch = self._make_mocks()
        session = self._make_session(config, mock_client)

        with (
            patch("custom_components.comelit_man.video_call.RtpReceiver", return_value=mock_receiver),
            patch("custom_components.comelit_man.video_call.LocalRtspServer", return_value=mock_rtsp),
        ):
            await session.start_inbound("SB100001", 0x12345678)

        # Answer is deferred — req_id stays 0 until answer_inbound() is called
        assert session._device_rtpc_req_id == 0
        # Placeholder must be stored for answer_inbound() to await
        assert session._inbound_device_rtpc is not None
        await session.stop()

    @pytest.mark.asyncio
    async def test_start_inbound_no_ctpp_raises_video_call_error(self):
        """start_inbound raises VideoCallError when no CTPP channel is open."""
        from custom_components.comelit_man.exceptions import VideoCallError

        config, mock_client, mock_receiver, mock_rtsp, ctpp_ch = self._make_mocks()
        mock_client.get_channel = MagicMock(return_value=None)
        session = self._make_session(config, mock_client)

        with pytest.raises(VideoCallError):
            await session.start_inbound("SB100001", 0x12345678)

    @pytest.mark.asyncio
    async def test_renewal_in_bundle_wait_is_acked_and_does_not_break_loop(self):
        """0x1860/0x0010 arriving before the device bundle is ACKed with the
        init-based timestamp pair and the loop continues; the actual bundle
        response still breaks it normally (not the renewal).

        Without the fix, the renewal would hit `if msg_type != 0x18C0: break`
        and abort the signaling sequence prematurely.
        """
        import struct
        from unittest.mock import call as mcall

        from custom_components.comelit_man.protocol import encode_call_response_ack

        config, mock_client, mock_receiver, mock_rtsp, ctpp_ch = self._make_mocks()
        session = self._make_session(config, mock_client)

        RENEWAL_ACK_TS = 0xDEADBEEF
        our_addr = f"{config.apt_address}{config.apt_subaddress}"
        our_base = config.apt_address

        renewal = struct.pack("<HI", 0x1860, 0xAAAAAAAA) + struct.pack(">HH", 0x0010, 0x0000)
        bundle = struct.pack("<HI", 0x1840, 0x11111111) + struct.pack(">HH", 0x0008, 0x0000)

        await ctpp_ch.response_queue.put(renewal)
        await ctpp_ch.response_queue.put(bundle)

        # Steps 13-17 (peer, call_accepted, drain, device RTPC, audio) are now
        # deferred to answer_inbound() — no injection needed for start_inbound().
        with (
            patch("custom_components.comelit_man.video_call.RtpReceiver", return_value=mock_receiver),
            patch("custom_components.comelit_man.video_call.LocalRtspServer", return_value=mock_rtsp),
        ):
            await session.start_inbound("SB100001", 0x12345678, renewal_ack_ts=RENEWAL_ACK_TS)

        expected_1800 = encode_call_response_ack(our_addr, our_base, RENEWAL_ACK_TS)
        expected_1820 = encode_call_response_ack(our_addr, our_base, RENEWAL_ACK_TS, prefix=0x1820)
        assert mcall(ctpp_ch, expected_1800) in mock_client.send_binary.call_args_list
        assert mcall(ctpp_ch, expected_1820) in mock_client.send_binary.call_args_list
        assert session.active is True
        await session.stop()


class TestAnswerInbound:
    """Tests for VideoCallSession.answer_inbound() — now async."""

    def _make_session(self, device_rtpc_req_id: int = 0, inbound_rtpc_pre_opened: bool = True):
        """Build a session in the passive-inbound state (after start_inbound)."""
        config = MagicMock()
        config.apt_address = "SB000006"
        config.apt_subaddress = "1"

        ctpp_ch = MagicMock()
        ctpp_ch.server_channel_id = 0x0010

        mock_client = MagicMock()
        mock_client.get_channel = MagicMock(return_value=ctpp_ch)
        mock_client.send_binary = AsyncMock()

        inbound_device_rtpc = MagicMock()
        inbound_device_rtpc.open_event = asyncio.Event()
        inbound_device_rtpc.server_channel_id = 0xBBBB
        if inbound_rtpc_pre_opened:
            inbound_device_rtpc.open_event.set()

        mock_receiver = MagicMock()
        mock_receiver.start_audio_sender = MagicMock()

        session = VideoCallSession.__new__(VideoCallSession)
        session._active = True
        session._device_rtpc_req_id = device_rtpc_req_id
        session._rtp_receiver = mock_receiver
        session._client = mock_client
        session._config = config
        session._ctpp_lock = asyncio.Lock()
        session._call_counter = 0x00100000
        session._inbound_device_rtpc = inbound_device_rtpc
        session._answer_handoff = None
        session._on_ring = None
        return session, mock_client, mock_receiver, ctpp_ch, inbound_device_rtpc

    @pytest.mark.asyncio
    async def test_answer_inbound_sends_peer_call_accepted_transform_acks_starts_audio(self):
        """answer_inbound sends peer+call_accepted, drains 0x000A/0x000E with
        transform(device_ts) ACKs, waits for device RTPC, starts audio."""
        import struct

        from custom_components.comelit_man.video_call import _transform_device_ts

        session, mock_client, mock_receiver, ctpp_ch, _ = self._make_session()

        DEV_TS_A = 0xAABBCCDD  # 0x000A device timestamp
        DEV_TS_E = 0x11223344  # 0x000E device timestamp
        rtpc_link_msg = struct.pack("<HI", 0x1840, DEV_TS_A) + struct.pack(">H", 0x000A)
        peer_msg = struct.pack("<HI", 0x1840, DEV_TS_E) + struct.pack(">H", 0x000E)

        our_addr = "SB0000061"
        our_base = "SB000006"

        async def run_and_inject():
            # Inject 0x000A + 0x000E into the handoff queue once it's created
            while session._answer_handoff is None:
                await asyncio.sleep(0)
            await session._answer_handoff.put(rtpc_link_msg)
            await session._answer_handoff.put(peer_msg)

        inject_task = asyncio.create_task(run_and_inject())
        await session.answer_inbound()
        await inject_task

        calls = mock_client.send_binary.call_args_list
        sent_payloads = [c.args[1] for c in calls]

        # Peer + call_accepted must be the first two sends
        from custom_components.comelit_man.protocol import encode_answer_peer, encode_call_accepted

        counter_after_peer = (0x00100000 + 0x00010000) & 0xFFFFFFFF
        counter_after_accepted = (counter_after_peer + 0x00010000) & 0xFFFFFFFF
        assert encode_answer_peer(our_addr, our_base, counter_after_peer, inbound=True) in sent_payloads
        assert encode_call_accepted(our_addr, our_base, counter_after_accepted) in sent_payloads

        # Transform-based ACKs for 0x000A and 0x000E
        from custom_components.comelit_man.protocol import encode_call_response_ack

        assert encode_call_response_ack(our_addr, our_base, _transform_device_ts(DEV_TS_A)) in sent_payloads
        assert encode_call_response_ack(our_addr, our_base, _transform_device_ts(DEV_TS_E)) in sent_payloads

        # Audio sender started with device RTPC req_id
        assert mock_receiver.start_audio_sender.call_count == 1
        args, kwargs = mock_receiver.start_audio_sender.call_args
        assert args[0] == 0xBBBB
        # TCP TX path is offered for TCP-media inbound sessions
        assert "tcp_send" in kwargs

    @pytest.mark.asyncio
    async def test_answer_inbound_already_answered_is_noop(self):
        """answer_inbound is a no-op when _device_rtpc_req_id is already set."""
        session, mock_client, mock_receiver, _, _ = self._make_session(device_rtpc_req_id=0x1234)
        await session.answer_inbound()
        mock_client.send_binary.assert_not_called()
        mock_receiver.start_audio_sender.assert_not_called()

    @pytest.mark.asyncio
    async def test_answer_inbound_no_op_when_no_receiver(self):
        """answer_inbound is a no-op (no raise) when receiver is None."""
        session, mock_client, _, _, _ = self._make_session()
        session._rtp_receiver = None
        await session.answer_inbound()  # must not raise
        mock_client.send_binary.assert_not_called()

    @pytest.mark.asyncio
    async def test_answer_inbound_device_rtpc_timeout_skips_audio(self):
        """If device RTPC open_event never fires, audio sender is NOT started."""
        import struct

        session, _, mock_receiver, _, _ = self._make_session(inbound_rtpc_pre_opened=False)

        DEV_TS_A = 0xAABBCCDD
        DEV_TS_E = 0x11223344
        rtpc_link_msg = struct.pack("<HI", 0x1840, DEV_TS_A) + struct.pack(">H", 0x000A)
        peer_msg = struct.pack("<HI", 0x1840, DEV_TS_E) + struct.pack(">H", 0x000E)

        async def run_and_inject():
            while session._answer_handoff is None:
                await asyncio.sleep(0)
            await session._answer_handoff.put(rtpc_link_msg)
            await session._answer_handoff.put(peer_msg)

        inject_task = asyncio.create_task(run_and_inject())
        with patch("custom_components.comelit_man.video_call.VIDEO_RESPONSE_TIMEOUT", 0.05):
            await session.answer_inbound()
        await inject_task

        mock_receiver.start_audio_sender.assert_not_called()


# ---------------------------------------------------------------------------
# _forward_ring — mid-call ring forwarding (Phase 2)
# ---------------------------------------------------------------------------


class TestForwardRing:
    def _make_session(self, on_ring=None) -> VideoCallSession:
        session = VideoCallSession.__new__(VideoCallSession)
        session._active = True
        session._client = None
        session._rtp_receiver = None
        session._rtsp_server = None
        session._external_rtsp = False
        session._ctpp_lock = asyncio.Lock()
        session._call_counter = 0
        session._answer_handoff = None
        session._on_ring = on_ring
        return session

    @staticmethod
    def _ring_frame(ring_ts: int = 0x27EEAB1C, addresses: tuple[str, ...] = ("SB100001", "SB000003")) -> bytes:
        buf = bytearray()
        buf += struct.pack("<H", 0x18C0)
        buf += struct.pack("<I", ring_ts)
        buf += struct.pack(">H", 0x0028)
        buf += struct.pack(">H", 0x0001)
        buf += b"\xff\xff\xff\xff"
        for addr in addresses:
            buf += addr.encode("ascii") + b"\x00"
        return bytes(buf)

    def test_forwards_entrance_and_ring_ts(self):
        rings = []
        session = self._make_session(on_ring=lambda addr, ts: rings.append((addr, ts)))
        session._forward_ring(self._ring_frame())
        assert rings == [("SB100001", 0x27EEAB1C)]

    def test_no_callback_is_noop(self):
        session = self._make_session(on_ring=None)
        session._forward_ring(self._ring_frame())  # must not raise

    def test_unparseable_frame_ignored(self):
        rings = []
        session = self._make_session(on_ring=lambda addr, ts: rings.append((addr, ts)))
        session._forward_ring(b"\x00\x01")
        assert rings == []

    def test_no_addresses_forwards_empty_entrance(self):
        rings = []
        session = self._make_session(on_ring=lambda addr, ts: rings.append((addr, ts)))
        session._forward_ring(self._ring_frame(addresses=()))
        assert rings == [("", 0x27EEAB1C)]

    def test_callback_exception_swallowed(self):
        def boom(addr, ts):
            raise RuntimeError("boom")

        session = self._make_session(on_ring=boom)
        session._forward_ring(self._ring_frame())  # must not raise

    @pytest.mark.asyncio
    async def test_monitor_forwards_18c0_without_ack(self):
        """0x18C0 in the monitor loop forwards the ring and sends NO ACK."""
        rings = []
        session = self._make_session(on_ring=lambda addr, ts: rings.append((addr, ts)))

        sent_data: list[bytes] = []
        mock_client = MagicMock()
        mock_client.send_binary = AsyncMock(side_effect=lambda ch, data: sent_data.append(data))
        frames = [self._ring_frame()]

        async def mock_read_response(channel):
            if frames:
                return frames.pop(0)
            session._active = False
            return b""

        mock_client.read_response = mock_read_response

        await session._ctpp_monitor_loop(
            mock_client,
            MagicMock(),
            "SB0000031",
            "SB100001",
            0x10000000,
            rtpc1_server_id=0xABCD,
            media_req_id=0x1234,
        )

        assert rings == [("SB100001", 0x27EEAB1C)]
        assert sent_data == []  # no ACK for the new ring

    @pytest.mark.asyncio
    async def test_monitor_forwards_1860_in_alerting_and_still_acks(self):
        """0x1860/0x0001 (IN_ALERTING) forwards the ring AND keeps its normal ACK."""
        rings = []
        session = self._make_session(on_ring=lambda addr, ts: rings.append((addr, ts)))

        buf = bytearray()
        buf += struct.pack("<H", 0x1860)
        buf += struct.pack("<I", 0x11223344)
        buf += struct.pack(">H", 0x0001)
        buf += struct.pack(">H", 0x0000)
        buf += b"\xff\xff\xff\xff"
        buf += b"SB100001\x00"

        sent_data: list[bytes] = []
        mock_client = MagicMock()
        mock_client.send_binary = AsyncMock(side_effect=lambda ch, data: sent_data.append(data))
        frames = [bytes(buf)]

        async def mock_read_response(channel):
            if frames:
                return frames.pop(0)
            session._active = False
            return b""

        mock_client.read_response = mock_read_response

        await session._ctpp_monitor_loop(
            mock_client,
            MagicMock(),
            "SB0000031",
            "SB100001",
            0x10000000,
            rtpc1_server_id=0xABCD,
            media_req_id=0x1234,
        )

        assert rings == [("SB100001", 0x11223344)]
        assert len(sent_data) == 1  # existing transform ACK unchanged
        assert struct.unpack_from("<H", sent_data[0], 0)[0] == 0x1800


class TestForwardRingFloorTag:
    def _session(self, on_ring):
        session = VideoCallSession.__new__(VideoCallSession)
        session._active = True
        session._client = None
        session._rtp_receiver = None
        session._rtsp_server = None
        session._external_rtsp = False
        session._ctpp_lock = asyncio.Lock()
        session._call_counter = 0
        session._answer_handoff = None
        session._on_ring = on_ring
        cfg = MagicMock()
        cfg.apt_address = "SB000006"
        session._config = cfg
        return session

    @staticmethod
    def _tagged_ring(tag: bytes) -> bytes:
        buf = bytearray()
        buf += struct.pack("<H", 0x18C0)
        buf += struct.pack("<I", 0x27EEAB1C)
        buf += struct.pack(">H", 0x0028)
        buf += struct.pack(">H", 0x0001)
        buf += tag + b"\xff\xff\xff\xff"
        buf += b"SB100001\x00"
        return bytes(buf)

    def test_floor_ring_forwarded_as_own_apartment(self):
        rings = []
        self._session(lambda a, t: rings.append(a))._forward_ring(self._tagged_ring(b"FF"))
        assert rings == ["SB000006"]

    def test_entrance_ring_forwarded_as_entrance(self):
        rings = []
        self._session(lambda a, t: rings.append(a))._forward_ring(self._tagged_ring(b"PP"))
        assert rings == ["SB100001"]
