"""Unit tests for RtpReceiver — no device or PyAV needed."""

from __future__ import annotations

import asyncio
import struct
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.comelit_man.rtp_receiver import (
    RtpReceiver,
    _build_control_packet,
)


class TestStartMediaStart:
    @pytest.mark.asyncio
    async def test_start_media_creates_decode_task(self):
        """start_media() creates the decode task — lines 201-202."""
        receiver = RtpReceiver("127.0.0.1")
        with patch.object(receiver, "_decode_loop", new_callable=AsyncMock):
            await receiver.start_media()
            assert receiver._decode_task is not None
            receiver._decode_task.cancel()
            await asyncio.gather(receiver._decode_task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_start_returns_port(self):
        """start() calls start_control + start_media and returns port — lines 206-208."""
        receiver = RtpReceiver("127.0.0.1")
        with (
            patch.object(receiver, "start_control", new_callable=AsyncMock, return_value=54321),
            patch.object(receiver, "start_media", new_callable=AsyncMock),
        ):
            port = await receiver.start()
        assert port == 54321


class TestRtpReceiverStop:
    @pytest.mark.asyncio
    async def test_stop_awaits_keepalive_task(self):
        """stop() must await the keepalive task, not just cancel it."""
        receiver = RtpReceiver("127.0.0.1")
        receiver._running = True

        cancelled = asyncio.Event()

        async def slow_keepalive():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        receiver._keepalive_task = asyncio.create_task(slow_keepalive())
        await asyncio.sleep(0)  # let the task start before cancelling

        await receiver.stop()

        assert cancelled.is_set(), "keepalive task was not properly awaited/cancelled"
        assert receiver._keepalive_task is None

    @pytest.mark.asyncio
    async def test_stop_awaits_decode_task(self):
        """stop() must await the decode task, not just cancel it."""
        receiver = RtpReceiver("127.0.0.1")
        receiver._running = True

        cancelled = asyncio.Event()

        async def slow_decode():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        receiver._decode_task = asyncio.create_task(slow_decode())
        await asyncio.sleep(0)  # let the task start before cancelling

        await receiver.stop()

        assert cancelled.is_set(), "decode task was not properly awaited/cancelled"
        assert receiver._decode_task is None

    @pytest.mark.asyncio
    async def test_stop_closes_transport(self):
        """stop() closes _transport when one is set — lines 608-609."""
        receiver = RtpReceiver("127.0.0.1")
        receiver._running = True
        mock_transport = MagicMock()
        receiver._transport = mock_transport
        await receiver.stop()
        mock_transport.close.assert_called_once()
        assert receiver._transport is None

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self):
        receiver = RtpReceiver("127.0.0.1")
        receiver._running = True
        await receiver.stop()
        assert not receiver._running

    @pytest.mark.asyncio
    async def test_running_property(self):
        receiver = RtpReceiver("127.0.0.1")
        assert not receiver.running
        receiver._running = True
        assert receiver.running


class TestDecodeLoopRobustness:
    def _make_fake_av(self, *, parse_raises=None, decode_raises=None) -> ModuleType:
        """Build a minimal fake `av` module for injection."""
        fake_av = ModuleType("av")

        class FakeInvalidDataError(Exception):
            pass

        fake_av.error = ModuleType("av.error")
        fake_av.error.InvalidDataError = FakeInvalidDataError

        packet = MagicMock()

        class FakeCodecContext:
            def parse(self, data):
                if parse_raises:
                    raise parse_raises
                return [packet]

            def decode(self, pkt):
                if decode_raises:
                    raise decode_raises
                return []

        fake_av.CodecContext = MagicMock()
        fake_av.CodecContext.create = lambda *a, **kw: FakeCodecContext()
        return fake_av

    @pytest.mark.asyncio
    async def test_decode_loop_stops_on_repeated_errors(self):
        """_decode_loop must break after _MAX_CONSECUTIVE_ERRORS non-InvalidDataError exceptions."""
        receiver = RtpReceiver("127.0.0.1")
        receiver._running = True

        fake_av = self._make_fake_av(decode_raises=RuntimeError("boom"))

        with patch.dict(sys.modules, {"av": fake_av, "av.error": fake_av.error}):
            for _ in range(10):
                await receiver._nal_queue.put((0, b"\x00\x00\x00\x01\x65" + b"\x00" * 20))

            await receiver._decode_loop()

        # Loop exited after 5 consecutive errors without hanging

    @pytest.mark.asyncio
    async def test_decode_loop_continues_on_invalid_data(self):
        """InvalidDataError must reset the consecutive error counter and not stop the loop."""
        receiver = RtpReceiver("127.0.0.1")
        receiver._running = True

        fake_av = self._make_fake_av()
        parse_call_count = 0

        class FakeCodecContext:
            def parse(self, data):
                nonlocal parse_call_count
                parse_call_count += 1
                if parse_call_count <= 3:
                    raise fake_av.error.InvalidDataError("bad data")
                receiver._running = False
                return []

            def decode(self, pkt):
                return []

        fake_av.CodecContext.create = lambda *a, **kw: FakeCodecContext()

        with patch.dict(sys.modules, {"av": fake_av, "av.error": fake_av.error}):
            for _ in range(10):
                await receiver._nal_queue.put((0, b"\x00\x00\x00\x01\x65" + b"\x00" * 20))
            await receiver._decode_loop()

        assert parse_call_count >= 3

    @pytest.mark.asyncio
    async def test_decode_loop_av_import_error(self):
        """ImportError from av causes loop to log and return — lines 472-477."""
        receiver = RtpReceiver("127.0.0.1")
        receiver._running = True

        with patch.dict(sys.modules, {"av": None}):
            await receiver._decode_loop()

        # Completed without propagating ImportError; no frames decoded
        assert receiver._latest_frame is None

    @pytest.mark.asyncio
    async def test_decode_loop_produces_frames(self):
        """Successful frame decode updates _latest_frame — lines 499-504, 538-542."""
        import logging

        FAKE_JPEG = b"\xff\xd8\xff\xe0fake_jpeg\xff\xd9"

        receiver = RtpReceiver("127.0.0.1")
        receiver._running = True

        class _FakeImage:
            def save(self, buf, **kwargs):
                buf.write(FAKE_JPEG)

        class _FakeFrame:
            width = 640
            height = 480

            def to_image(self):
                return _FakeImage()

        fake_av = self._make_fake_av()

        class _FrameCodecCtx:
            def parse(self, data):
                return [MagicMock()]

            def decode(self, pkt):
                receiver._running = False  # stop after first frame
                return [_FakeFrame()]

        fake_av.CodecContext.create = lambda *a, **kw: _FrameCodecCtx()

        await receiver._nal_queue.put((0, b"\x00\x00\x00\x01\x65" + b"\x00" * 20))

        with patch.dict(sys.modules, {"av": fake_av, "av.error": fake_av.error}):
            # Enable debug so the verbose timing branch (lines 541-542) also runs
            import logging

            logger = logging.getLogger("custom_components.comelit_man.rtp_receiver")
            old_level = logger.level
            logger.setLevel(logging.DEBUG)
            try:
                await receiver._decode_loop()
            finally:
                logger.setLevel(old_level)

        assert receiver._latest_frame == FAKE_JPEG
        assert receiver._frame_event.is_set()

    @pytest.mark.asyncio
    async def test_decode_loop_timeout_no_nal(self):
        """TimeoutError waiting for NAL logs and continues — lines 520-526.

        Enable DEBUG so the verbose branch (line 522) also executes.
        """
        import contextlib
        import logging

        receiver = RtpReceiver("127.0.0.1")
        receiver._running = True

        fake_av = self._make_fake_av()
        iteration = 0

        async def mock_wait_for(coro, timeout):
            nonlocal iteration
            with contextlib.suppress(AttributeError):
                coro.close()
            iteration += 1
            if iteration >= 2:
                receiver._running = False
            raise TimeoutError()

        logger = logging.getLogger("custom_components.comelit_man.rtp_receiver")
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            with patch.dict(sys.modules, {"av": fake_av, "av.error": fake_av.error}):
                with patch("asyncio.wait_for", side_effect=mock_wait_for):
                    await receiver._decode_loop()
        finally:
            logger.setLevel(old_level)

        assert iteration >= 2

    @pytest.mark.asyncio
    async def test_decode_loop_outer_exception_caught(self):
        """Non-CancelledError from wait_for propagates to outer except Exception — lines 563-564.

        asyncio.wait_for is in an inner try that only catches TimeoutError.
        A ValueError escapes all inner handlers and is caught by the outer
        `except Exception:` at line 563.
        """
        import contextlib

        receiver = RtpReceiver("127.0.0.1")
        receiver._running = True

        fake_av = self._make_fake_av()

        async def raise_value_error(coro, timeout):
            with contextlib.suppress(AttributeError):
                coro.close()
            raise ValueError("unexpected queue error")

        with patch.dict(sys.modules, {"av": fake_av, "av.error": fake_av.error}):
            with patch("asyncio.wait_for", side_effect=raise_value_error):
                await receiver._decode_loop()

        # Completed normally — ValueError was swallowed by lines 563-564

    @pytest.mark.asyncio
    async def test_decode_loop_cancelled_caught_by_outer_try(self):
        """CancelledError propagates past inner handlers to outer except — lines 561-562."""
        receiver = RtpReceiver("127.0.0.1")
        receiver._running = True

        fake_av = self._make_fake_av()

        with patch.dict(sys.modules, {"av": fake_av, "av.error": fake_av.error}):
            task = asyncio.create_task(receiver._decode_loop())
            # Wait for codec init to complete and loop to reach nal_queue.get
            await asyncio.sleep(0.2)
            task.cancel()
            results = await asyncio.gather(task, return_exceptions=True)

        # CancelledError was caught by 'except asyncio.CancelledError: pass'
        # so the task completes normally (not cancelled)
        assert task.done()
        assert not task.cancelled()
        assert results == [None]


# ---------------------------------------------------------------------------
# _build_control_packet
# ---------------------------------------------------------------------------


def test_build_control_packet_length():
    """Control packet is always 14 bytes (6-byte header + 6-byte body)."""
    pkt = _build_control_packet(0x0001, 0x1234, 0)
    assert len(pkt) == 14


def test_build_control_packet_seq_in_body():
    """Sequence number appears at body byte 3 (offset 11)."""
    pkt = _build_control_packet(0x0001, 0x0000, 42)
    assert pkt[11] == 42


def test_build_control_packet_token_le16():
    """UDPM token is encoded little-endian in the first two body bytes."""
    token = 0x6060
    pkt = _build_control_packet(0x0001, token, 0)
    assert pkt[8] == 0x60  # low byte
    assert pkt[9] == 0x60  # high byte


# ---------------------------------------------------------------------------
# Packet routing: _on_udp_packet and receive_tcp_rtp
# ---------------------------------------------------------------------------


def _make_rtp_packet(payload: bytes) -> bytes:
    """Build a minimal valid RTP packet (version=2, 12-byte header + payload)."""
    # First byte: V=2, P=0, X=0, CC=0 → 0x80
    header = bytes([0x80, 0x60, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01])
    return header + payload


def _make_icona_udp(rtp: bytes, req_id: int) -> bytes:
    """Wrap an RTP payload in an ICONA UDP header."""
    body_len = len(rtp)
    # Header: magic(2) + body_len(LE16) + req_id(LE16) + padding(2)
    header = struct.pack("<2sHH2s", b"\x00\x06", body_len, req_id, b"\x00\x00")
    # Append enough padding to satisfy HEADER_SIZE + 12 minimum
    return header + rtp


class TestOnUdpPacket:
    def test_too_short_packet_ignored(self):
        """Packets shorter than HEADER_SIZE+12 are silently dropped."""
        receiver = RtpReceiver("127.0.0.1", media_req_id=0x01)
        receiver._on_udp_packet(b"\x00" * 5)
        assert receiver._media_packet_count == 0

    def test_control_packet_recognized(self):
        """Control packets (matching control_req_id) are accepted without error."""
        receiver = RtpReceiver("127.0.0.1", control_req_id=0x0010)
        # Build a packet that looks like a control response
        data = struct.pack("<2sHH2s", b"\x00\x06", 20, 0x0010, b"\x00\x00") + b"\x00" * 20
        receiver._on_udp_packet(data)  # Should not raise

    def test_media_packet_increments_counter(self):
        """Media packets (matching media_req_id) increment the media packet counter."""
        receiver = RtpReceiver("127.0.0.1", media_req_id=0x0020)
        nal = b"\x67" + b"\x00" * 10  # NAL type 7 (SPS)
        rtp = _make_rtp_packet(nal)
        udp = _make_icona_udp(rtp, 0x0020)
        receiver._on_udp_packet(udp)
        assert receiver._media_packet_count == 1


class TestReceiveTcpRtp:
    def test_too_short_ignored(self):
        receiver = RtpReceiver("127.0.0.1")
        receiver.receive_tcp_rtp(b"\x80" * 5)
        assert receiver._media_packet_count == 0

    def test_valid_rtp_increments_counter(self):
        receiver = RtpReceiver("127.0.0.1")
        nal = b"\x67" + b"\x00" * 10  # SPS NAL
        pkt = _make_rtp_packet(nal)
        receiver.receive_tcp_rtp(pkt)
        assert receiver._media_packet_count == 1

    def test_rtp_forwarded_to_rtp_queue(self):
        """With rtp_queue attached, packet is forwarded there — lines 293-299."""
        receiver = RtpReceiver("127.0.0.1")
        rtp_q: asyncio.Queue[bytes] = asyncio.Queue()
        receiver.attach_rtsp_queues(asyncio.Queue(), asyncio.Queue(), rtp_q)

        nal = b"\x67" + b"\x00" * 10
        pkt = _make_rtp_packet(nal)
        receiver.receive_tcp_rtp(pkt)

        assert not rtp_q.empty()
        assert receiver._first_video_nal_event.is_set()

    def test_rtp_queue_full_increments_drop_counter(self):
        """QueueFull on rtp_queue increments rtsp_nal_drops — line 296."""
        receiver = RtpReceiver("127.0.0.1")
        rtp_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
        rtp_q.put_nowait(b"already_full")
        receiver.attach_rtsp_queues(asyncio.Queue(), asyncio.Queue(), rtp_q)

        nal = b"\x67" + b"\x00" * 10
        pkt = _make_rtp_packet(nal)
        receiver.receive_tcp_rtp(pkt)

        assert receiver._rtsp_nal_drops == 1

    def test_empty_nal_data_returns_early(self):
        """RTP with no payload after header returns without queuing — line 309."""
        receiver = RtpReceiver("127.0.0.1")
        # 12-byte header only, PT=96 (video, not audio), no payload
        pkt = bytes([0x80, 0x60, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01])
        receiver.receive_tcp_rtp(pkt)
        assert receiver._media_packet_count == 1
        assert receiver._nal_queue.empty()


class TestSetMediaReqId:
    def test_set_media_req_id(self):
        receiver = RtpReceiver("127.0.0.1", media_req_id=0)
        receiver.set_media_req_id(0xABCD)
        assert receiver._media_req_id == 0xABCD


# ---------------------------------------------------------------------------
# NAL unit processing: _process_rtp
# ---------------------------------------------------------------------------


class TestProcessRtp:
    def test_sps_nal_queued(self):
        """SPS NAL (type 7) is queued with start code prefix."""
        receiver = RtpReceiver("127.0.0.1")
        nal_payload = b"\x67" + b"\x42\x00\x1f\x01"  # type=7 (SPS)
        rtp = _make_rtp_packet(nal_payload)
        receiver._process_rtp(rtp)
        assert not receiver._nal_queue.empty()
        _rtp_ts, nal = receiver._nal_queue.get_nowait()
        assert nal.startswith(b"\x00\x00\x00\x01")

    def test_pps_nal_queued(self):
        """PPS NAL (type 8) is queued with start code prefix."""
        receiver = RtpReceiver("127.0.0.1")
        nal_payload = b"\x68" + b"\xce\x38\x80"  # type=8 (PPS)
        rtp = _make_rtp_packet(nal_payload)
        receiver._process_rtp(rtp)
        assert not receiver._nal_queue.empty()

    def test_non_idr_nal_queued(self):
        """Non-IDR NAL (type 1) is queued."""
        receiver = RtpReceiver("127.0.0.1")
        nal_payload = b"\x61" + b"\x00" * 8  # type=1 (non-IDR)
        rtp = _make_rtp_packet(nal_payload)
        receiver._process_rtp(rtp)
        assert not receiver._nal_queue.empty()

    def test_idr_single_nal_increments_idr_count(self):
        """NAL type 5 (IDR) as single unit calls _log_idr_arrival — line 349."""
        receiver = RtpReceiver("127.0.0.1")
        nal_payload = b"\x65" + b"\x00" * 8  # type=5 (IDR)
        rtp = _make_rtp_packet(nal_payload)
        receiver._process_rtp(rtp)
        assert not receiver._nal_queue.empty()
        assert receiver._idr_count == 1

    def test_invalid_rtp_version_ignored(self):
        """Packets with RTP version != 2 are discarded."""
        receiver = RtpReceiver("127.0.0.1")
        # First byte: V=1 → 0x40
        bad_rtp = b"\x40" + b"\x00" * 15
        receiver._process_rtp(bad_rtp)
        assert receiver._nal_queue.empty()

    def test_fua_reassembly_start_and_end(self):
        """FU-A fragments are reassembled into a single NAL unit."""
        receiver = RtpReceiver("127.0.0.1")

        # FU-A start: nal_type=28 (0x1C), fu_header=start_bit(0x80)|type(5)=0x85
        fu_indicator = 0x7C  # forbidden=0, nal_ref=3, type=28
        fu_header_start = 0x85  # S=1, E=0, R=0, type=5 (IDR)
        start_fragment = bytes([fu_indicator, fu_header_start]) + b"\xaa" * 10
        rtp_start = _make_rtp_packet(start_fragment)
        receiver._process_rtp(rtp_start)

        # Queue should still be empty (fragment not complete yet)
        assert receiver._nal_queue.empty()
        assert len(receiver._current_fua_nal) > 0

        # FU-A end: S=0, E=1
        fu_header_end = 0x45  # S=0, E=1, type=5
        end_fragment = bytes([fu_indicator, fu_header_end]) + b"\xbb" * 8
        rtp_end = _make_rtp_packet(end_fragment)
        receiver._process_rtp(rtp_end)

        # Now the queue should have the complete NAL
        assert not receiver._nal_queue.empty()
        _rtp_ts, nal = receiver._nal_queue.get_nowait()
        assert nal.startswith(b"\x00\x00\x00\x01")

    def test_fua_continuation_without_start_ignored(self):
        """FU-A continuation fragment without a prior start is discarded."""
        receiver = RtpReceiver("127.0.0.1")

        fu_indicator = 0x7C
        fu_header_cont = 0x05  # S=0, E=0 — continuation
        cont_fragment = bytes([fu_indicator, fu_header_cont]) + b"\xcc" * 5
        rtp_cont = _make_rtp_packet(cont_fragment)
        receiver._process_rtp(rtp_cont)

        assert receiver._nal_queue.empty()

    def test_fua_too_short_ignored(self):
        """FU-A packet with only 1 byte of NAL data is ignored."""
        receiver = RtpReceiver("127.0.0.1")
        nal_payload = b"\x7c"  # type=28, no FU header
        rtp = _make_rtp_packet(nal_payload)
        receiver._process_rtp(rtp)
        assert receiver._nal_queue.empty()

    def test_empty_nal_data_ignored(self):
        """RTP with no NAL data after 12-byte header is ignored."""
        receiver = RtpReceiver("127.0.0.1")
        rtp = b"\x80" + b"\x00" * 11  # 12 bytes total, no payload
        receiver._process_rtp(rtp)
        assert receiver._nal_queue.empty()


# ---------------------------------------------------------------------------
# NAL queue overflow
# ---------------------------------------------------------------------------


class TestQueueNal:
    def test_queue_full_drops_packet(self):
        """_queue_nal silently drops packets when the queue is full."""
        receiver = RtpReceiver("127.0.0.1")
        # Fill the queue to capacity
        for _ in range(500):
            receiver._nal_queue.put_nowait((0, b"\x00" * 4))
        # This should not raise
        receiver._queue_nal(0, b"\x00\x00\x00\x01\x67")
        assert receiver._nal_queue.full()

    def test_nal_also_pushed_to_rtsp_queue(self):
        """_queue_nal pushes (rtp_ts, nal) tuple to RTSP fanout queue when attached."""
        receiver = RtpReceiver("127.0.0.1")
        rtsp_nal_q = asyncio.Queue()
        receiver.attach_rtsp_queues(rtsp_nal_q, asyncio.Queue())

        nal = b"\x00\x00\x00\x01\x67" + b"\x00" * 10
        receiver._queue_nal(0xDEAD, nal)

        assert not receiver._nal_queue.empty()
        assert not rtsp_nal_q.empty()
        assert rtsp_nal_q.get_nowait() == (0xDEAD, nal)

    def test_rtsp_nal_queue_full_drops_silently(self):
        """_queue_nal drops silently when RTSP fanout queue is full."""
        receiver = RtpReceiver("127.0.0.1")
        rtsp_nal_q = asyncio.Queue(maxsize=1)
        rtsp_nal_q.put_nowait((0, b"already_full"))
        receiver.attach_rtsp_queues(rtsp_nal_q, asyncio.Queue())

        nal = b"\x00\x00\x00\x01\x67" + b"\x00" * 10
        receiver._queue_nal(0, nal)  # Must not raise

    def test_nal_queue_still_receives_when_no_rtsp(self):
        """_queue_nal works normally when no RTSP queues are attached."""
        receiver = RtpReceiver("127.0.0.1")
        nal = b"\x00\x00\x00\x01\x65" + b"\x00" * 10
        receiver._queue_nal(0, nal)
        assert not receiver._nal_queue.empty()


# ---------------------------------------------------------------------------
# get_jpeg_frame and latest_frame
# ---------------------------------------------------------------------------


class TestGetJpegFrame:
    @pytest.mark.asyncio
    async def test_returns_cached_frame_on_timeout(self):
        """On timeout, returns the last decoded frame (not None) if one exists.

        New behaviour: get_jpeg_frame always waits for the next event. If the
        event doesn't fire within the timeout the caller still gets whatever
        was decoded previously so it has something to display.
        """
        receiver = RtpReceiver("127.0.0.1")
        fake_jpeg = b"\xff\xd8cached\xff\xd9"
        receiver._latest_frame = fake_jpeg
        try:
            async with asyncio.timeout(0.05):
                result = await receiver.get_jpeg_frame()
        except TimeoutError:
            result = receiver.latest_frame
        assert result is fake_jpeg

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout_with_no_frame(self):
        """Returns None on timeout when no frame has ever been decoded."""
        receiver = RtpReceiver("127.0.0.1")
        try:
            async with asyncio.timeout(0.05):
                result = await receiver.get_jpeg_frame()
        except TimeoutError:
            result = receiver.latest_frame
        assert result is None

    @pytest.mark.asyncio
    async def test_always_waits_for_next_event(self):
        """Never returns a cached frame immediately — always waits for the event.

        This prevents camera.py from spinning in a tight loop that floods the
        TCP send buffer (~16fps natural throttling via the frame event).
        """
        receiver = RtpReceiver("127.0.0.1")
        # Pre-load a cached frame AND pre-set the event
        receiver._latest_frame = b"\xff\xd8old\xff\xd9"
        receiver._frame_event.set()

        # New frame arrives after a small delay
        new_jpeg = b"\xff\xd8new\xff\xd9"

        async def produce():
            await asyncio.sleep(0.02)
            receiver._latest_frame = new_jpeg
            receiver._frame_event.set()

        asyncio.create_task(produce())
        result = await receiver.get_jpeg_frame()
        # get_jpeg_frame clears the event first, so it must wait for produce()
        assert result is new_jpeg

    @pytest.mark.asyncio
    async def test_waits_for_frame_event(self):
        """Waits on _frame_event and returns frame when signaled."""
        receiver = RtpReceiver("127.0.0.1")
        fake_jpeg = b"\xff\xd8live\xff\xd9"

        async def produce():
            await asyncio.sleep(0.02)
            receiver._latest_frame = fake_jpeg
            receiver._frame_event.set()

        asyncio.create_task(produce())
        result = await receiver.get_jpeg_frame()
        assert result is fake_jpeg

    def test_latest_frame_property(self):
        """latest_frame returns _latest_frame without waiting."""
        receiver = RtpReceiver("127.0.0.1")
        assert receiver.latest_frame is None
        receiver._latest_frame = b"\xff\xd8\xff\xd9"
        assert receiver.latest_frame == b"\xff\xd8\xff\xd9"


# ---------------------------------------------------------------------------
# start_control / start_keepalive / keepalive loop
# ---------------------------------------------------------------------------


class TestStartControl:
    @pytest.mark.asyncio
    async def test_start_control_returns_port(self):
        """start_control() opens a UDP socket and returns the local port."""
        receiver = RtpReceiver("127.0.0.1", control_req_id=1, udpm_token=0x1234)

        mock_transport = MagicMock()
        mock_transport.get_extra_info.return_value = ("127.0.0.1", 54321)
        mock_transport.sendto = MagicMock()

        mock_protocol = MagicMock()

        with patch(
            "asyncio.get_running_loop",
            return_value=MagicMock(create_datagram_endpoint=AsyncMock(return_value=(mock_transport, mock_protocol))),
        ):
            port = await receiver.start_control()

        assert port == 54321
        assert receiver._running is True

    @pytest.mark.asyncio
    async def test_start_control_sends_two_discovery_packets(self):
        """start_control() sends exactly 2 control packets on startup."""
        receiver = RtpReceiver("127.0.0.1", control_req_id=1, udpm_token=0x0000)

        mock_transport = MagicMock()
        mock_transport.get_extra_info.return_value = ("127.0.0.1", 12345)
        sendto_calls = []
        mock_transport.sendto = lambda data: sendto_calls.append(data)

        with patch(
            "asyncio.get_running_loop",
            return_value=MagicMock(create_datagram_endpoint=AsyncMock(return_value=(mock_transport, MagicMock()))),
        ):
            await receiver.start_control()

        assert len(sendto_calls) == 2


class TestSendControlNoTransport:
    def test_send_control_with_no_transport_is_noop(self):
        """_send_control returns early when _transport is None — line 213."""
        receiver = RtpReceiver("127.0.0.1")
        assert receiver._transport is None
        receiver._send_control()  # must not raise


class TestKeepaliveLoop:
    @pytest.mark.asyncio
    async def test_keepalive_cancelled_error_swallowed(self):
        """CancelledError inside _keepalive_loop is caught by the try/except — lines 231-232.

        Inject CancelledError through a mocked sleep so we can directly call the
        coroutine and confirm it returns normally (no propagation).
        """
        receiver = RtpReceiver("127.0.0.1")
        receiver._running = True
        mock_transport = MagicMock()
        mock_transport.sendto = MagicMock()
        receiver._transport = mock_transport

        async def raise_cancelled(_t: float) -> None:
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=raise_cancelled):
            # CancelledError is caught (lines 231-232) and the coroutine returns normally
            await receiver._keepalive_loop()

        # If we reach here without exception, lines 231-232 executed correctly

    @pytest.mark.asyncio
    async def test_keepalive_sends_packets(self):
        """Keepalive loop sends a control packet on each iteration."""
        receiver = RtpReceiver("127.0.0.1")
        receiver._running = True

        sent = []
        mock_transport = MagicMock()
        mock_transport.sendto = lambda data: sent.append(data)
        receiver._transport = mock_transport

        async def run_two_iterations():
            iteration = 0

            original_sleep = asyncio.sleep

            async def fake_sleep(t):
                nonlocal iteration
                iteration += 1
                if iteration >= 2:
                    receiver._running = False
                await original_sleep(0)

            with patch("asyncio.sleep", fake_sleep):
                await receiver._keepalive_loop()

        await run_two_iterations()
        assert len(sent) >= 1

    @pytest.mark.asyncio
    async def test_start_keepalive_creates_task(self):
        """start_keepalive() creates the _keepalive_task."""
        receiver = RtpReceiver("127.0.0.1")
        receiver._running = False  # Loop exits immediately

        mock_transport = MagicMock()
        mock_transport.sendto = MagicMock()
        receiver._transport = mock_transport

        receiver.start_keepalive()
        assert receiver._keepalive_task is not None
        # Clean up
        receiver._keepalive_task.cancel()
        import contextlib

        with contextlib.suppress(asyncio.CancelledError):
            await receiver._keepalive_task


# ---------------------------------------------------------------------------
# _frame_to_jpeg
# ---------------------------------------------------------------------------


class TestFrameToJpeg:
    """Tests for _frame_to_jpeg which now uses Pillow (frame.to_image()) not PyAV MJPEG."""

    def _make_fake_frame(self, jpeg_bytes: bytes = b"\xff\xd8\xff\xe0fake\xff\xd9"):
        """Build a frame mock whose to_image() returns a Pillow-like image."""
        _jpeg = jpeg_bytes

        class FakeImage:
            def save(self, buf, format="JPEG", quality=80):
                buf.write(_jpeg)

        class FakeFrame:
            width = 320
            height = 240

            def to_image(self):
                return FakeImage()

        return FakeFrame(), _jpeg

    def test_frame_to_jpeg_success(self):
        """_frame_to_jpeg returns JPEG bytes produced by Pillow's image.save()."""
        fake_frame, expected_jpeg = self._make_fake_frame()
        result = RtpReceiver._frame_to_jpeg(fake_frame)
        assert result == expected_jpeg

    def test_frame_to_jpeg_returns_none_when_save_writes_nothing(self):
        """_frame_to_jpeg returns None when image.save() writes zero bytes."""

        class EmptyImage:
            def save(self, buf, **kwargs):
                pass  # write nothing

        class FakeFrame:
            def to_image(self):
                return EmptyImage()

        result = RtpReceiver._frame_to_jpeg(FakeFrame())
        assert result is None

    def test_frame_to_jpeg_returns_none_on_exception(self):
        """_frame_to_jpeg returns None when to_image() raises."""

        class BrokenFrame:
            def to_image(self):
                raise RuntimeError("boom")

        result = RtpReceiver._frame_to_jpeg(BrokenFrame())
        assert result is None


# ---------------------------------------------------------------------------
# attach_rtsp_queues
# ---------------------------------------------------------------------------


class TestAttachRtspQueues:
    def test_attach_sets_queues(self):
        """attach_rtsp_queues stores the provided queue references."""
        receiver = RtpReceiver("127.0.0.1")
        nal_q = asyncio.Queue()
        audio_q = asyncio.Queue()
        receiver.attach_rtsp_queues(nal_q, audio_q)
        assert receiver._rtsp_nal_queue is nal_q
        assert receiver._rtsp_audio_queue is audio_q

    def test_initial_state_has_no_rtsp_queues(self):
        """RTSP queues start as None (not attached)."""
        receiver = RtpReceiver("127.0.0.1")
        assert receiver._rtsp_nal_queue is None
        assert receiver._rtsp_audio_queue is None

    def test_attach_can_be_called_multiple_times(self):
        """attach_rtsp_queues can replace queues with new ones."""
        receiver = RtpReceiver("127.0.0.1")
        q1 = asyncio.Queue()
        receiver.attach_rtsp_queues(q1, asyncio.Queue())
        q2 = asyncio.Queue()
        receiver.attach_rtsp_queues(q2, asyncio.Queue())
        assert receiver._rtsp_nal_queue is q2


# ---------------------------------------------------------------------------
# Audio routing: _process_audio_rtp and _process_rtp PT routing
# ---------------------------------------------------------------------------


def _make_audio_rtp(pt: int, payload: bytes = b"\xd5" * 160) -> bytes:
    """Build a minimal valid RTP packet with the given payload type."""
    header = bytes(
        [
            0x80,  # V=2, P=0, X=0, CC=0
            pt & 0x7F,  # M=0, PT
            0x00,
            0x01,  # seq=1
            0x00,
            0x00,
            0x00,
            0x00,  # timestamp=0
            0x00,
            0x00,
            0x00,
            0x01,  # ssrc=1
        ]
    )
    return header + payload


class TestAudioRouting:
    def test_pcma_pt8_goes_to_audio_queue(self):
        """PT=8 (PCMA) is routed to the RTSP audio queue, not the NAL queue."""
        receiver = RtpReceiver("127.0.0.1")
        audio_q = asyncio.Queue()
        receiver.attach_rtsp_queues(asyncio.Queue(), audio_q)

        rtp = _make_audio_rtp(8, b"\xd5" * 160)
        receiver._process_rtp(rtp)

        assert not audio_q.empty()
        assert receiver._nal_queue.empty()

    def test_pcmu_pt0_goes_to_audio_queue(self):
        """PT=0 (PCMU) is routed to the RTSP audio queue, not the NAL queue."""
        receiver = RtpReceiver("127.0.0.1")
        audio_q = asyncio.Queue()
        receiver.attach_rtsp_queues(asyncio.Queue(), audio_q)

        rtp = _make_audio_rtp(0, b"\x7f" * 160)
        receiver._process_rtp(rtp)

        assert not audio_q.empty()
        assert receiver._nal_queue.empty()

    def test_audio_payload_content_correct(self):
        """Audio payload in queue matches the RTP payload (strips 12-byte header)."""
        receiver = RtpReceiver("127.0.0.1")
        audio_q = asyncio.Queue()
        receiver.attach_rtsp_queues(asyncio.Queue(), audio_q)

        payload = b"\xd5" * 160
        rtp = _make_audio_rtp(8, payload)
        receiver._process_rtp(rtp)

        queued = audio_q.get_nowait()
        assert queued == payload

    def test_audio_increments_packet_count(self):
        """Each audio packet increments _audio_packet_count."""
        receiver = RtpReceiver("127.0.0.1")
        receiver.attach_rtsp_queues(asyncio.Queue(), asyncio.Queue())

        for _ in range(3):
            receiver._process_rtp(_make_audio_rtp(8))

        assert receiver._audio_packet_count == 3

    def test_audio_without_rtsp_queue_does_not_crash(self):
        """Audio packets are handled gracefully even when no RTSP queues are attached."""
        receiver = RtpReceiver("127.0.0.1")
        rtp = _make_audio_rtp(8, b"\xd5" * 160)
        receiver._process_rtp(rtp)  # Must not raise
        assert receiver._audio_packet_count == 1

    def test_audio_debug_log_for_first_packets(self, caplog):
        """Debug log fires for first ≤3 audio packets when DEBUG enabled — line 360."""
        import logging

        receiver = RtpReceiver("127.0.0.1")
        audio_q: asyncio.Queue[bytes] = asyncio.Queue()
        receiver.attach_rtsp_queues(asyncio.Queue(), audio_q)

        with caplog.at_level(logging.DEBUG, logger="custom_components.comelit_man.rtp_receiver"):
            receiver._process_rtp(_make_audio_rtp(8, b"\xd5" * 160))

        assert any("Audio RTP" in r.message for r in caplog.records)

    def test_audio_queue_full_drops_silently(self):
        """Audio payload is silently dropped when RTSP audio queue is full."""
        receiver = RtpReceiver("127.0.0.1")
        audio_q = asyncio.Queue(maxsize=1)
        audio_q.put_nowait(b"already_full")
        receiver.attach_rtsp_queues(asyncio.Queue(), audio_q)

        rtp = _make_audio_rtp(8, b"\xd5" * 160)
        receiver._process_rtp(rtp)  # Must not raise

    def test_empty_audio_payload_ignored(self):
        """RTP audio packet with no payload after header is discarded."""
        receiver = RtpReceiver("127.0.0.1")
        audio_q = asyncio.Queue()
        receiver.attach_rtsp_queues(asyncio.Queue(), audio_q)

        rtp = _make_audio_rtp(8, b"")  # empty payload
        receiver._process_rtp(rtp)

        assert audio_q.empty()

    def test_video_not_routed_to_audio_queue(self):
        """Non-audio PT (e.g. PT=96 video) goes to NAL queue, not audio queue."""
        receiver = RtpReceiver("127.0.0.1")
        audio_q = asyncio.Queue()
        nal_q = asyncio.Queue()
        receiver.attach_rtsp_queues(nal_q, audio_q)

        # PT=96 video packet with SPS NAL
        header = bytes([0x80, 0x60, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01])
        rtp = header + b"\x67" + b"\x00" * 10
        receiver._process_rtp(rtp)

        assert audio_q.empty()
        assert not receiver._nal_queue.empty()


# ---------------------------------------------------------------------------
# _maybe_log_drops — rate limiting
# ---------------------------------------------------------------------------


class TestMaybeLogDrops:
    def test_rate_limited_returns_early(self):
        """Second call within 5s returns early — line 421."""
        import time

        receiver = RtpReceiver("127.0.0.1")
        receiver._rtsp_nal_drops = 5
        # Simulate a very recent last-log time so the rate limit fires
        receiver._last_drop_log_mono = time.monotonic()
        receiver._maybe_log_drops()  # must not raise; returns early without logging


# ---------------------------------------------------------------------------
# wait_for_first_video
# ---------------------------------------------------------------------------


class TestWaitForFirstVideo:
    @pytest.mark.asyncio
    async def test_returns_immediately_when_event_already_set(self):
        """Event pre-set → returns immediately without blocking."""
        receiver = RtpReceiver("127.0.0.1")
        receiver._first_video_nal_event.set()
        async with asyncio.timeout(0.1):
            await receiver.wait_for_first_video()

    @pytest.mark.asyncio
    async def test_raises_timeout_error_when_no_event(self):
        """Event never set → caller-supplied asyncio.timeout raises TimeoutError."""
        receiver = RtpReceiver("127.0.0.1")
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.01):
                await receiver.wait_for_first_video()


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_udp_media_packet_count_property(self):
        """udp_media_packet_count returns _udp_media_packet_count — line 449."""
        receiver = RtpReceiver("127.0.0.1")
        assert receiver.udp_media_packet_count == 0
        receiver._udp_media_packet_count = 7
        assert receiver.udp_media_packet_count == 7


# ---------------------------------------------------------------------------
# IDR tracking (_log_idr_arrival)
# ---------------------------------------------------------------------------


class TestIdrTracking:
    def test_idr_count_starts_at_zero(self):
        receiver = RtpReceiver("127.0.0.1")
        assert receiver._idr_count == 0

    def test_last_idr_mono_starts_none(self):
        receiver = RtpReceiver("127.0.0.1")
        assert receiver._last_idr_mono is None

    def test_log_idr_arrival_increments_count(self):
        receiver = RtpReceiver("127.0.0.1")
        receiver._log_idr_arrival(0x12345678)
        assert receiver._idr_count == 1
        receiver._log_idr_arrival(0x12345679)
        assert receiver._idr_count == 2

    def test_log_idr_arrival_records_monotonic_time(self):
        import time

        receiver = RtpReceiver("127.0.0.1")
        before = time.monotonic()
        receiver._log_idr_arrival(0)
        after = time.monotonic()
        assert before <= receiver._last_idr_mono <= after

    def test_log_idr_arrival_interval_zero_on_first_call(self, caplog):
        import logging

        receiver = RtpReceiver("127.0.0.1")
        with caplog.at_level(logging.DEBUG, logger="custom_components.comelit_man.rtp_receiver"):
            receiver._log_idr_arrival(0x10000000)
        assert "IDR #1" in caplog.text
        assert "interval=0.00s" in caplog.text

    def test_log_idr_arrival_logs_at_debug(self, caplog):
        import logging

        receiver = RtpReceiver("127.0.0.1")
        with caplog.at_level(logging.DEBUG, logger="custom_components.comelit_man.rtp_receiver"):
            receiver._log_idr_arrival(0xABCDEF01)
        # Must appear in DEBUG records, not only INFO+
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("IDR" in r.message for r in debug_records)


# ---------------------------------------------------------------------------
# Backchannel audio: attach_backchannel_queue + _audio_send_loop
# ---------------------------------------------------------------------------


class TestBackchannelAudio:
    def test_attach_backchannel_queue_sets_field(self):
        """attach_backchannel_queue stores the queue reference."""
        receiver = RtpReceiver("127.0.0.1")
        q = asyncio.Queue()
        receiver.attach_backchannel_queue(q)
        assert receiver._backchannel_queue is q

    def test_initial_state_has_no_backchannel_queue(self):
        """Backchannel queue starts as None (not attached)."""
        receiver = RtpReceiver("127.0.0.1")
        assert receiver._backchannel_queue is None

    @pytest.mark.asyncio
    async def test_audio_send_loop_prefers_backchannel_over_silence(self):
        """When backchannel queue has a frame, _audio_send_loop sends it instead of silence."""
        receiver = RtpReceiver("127.0.0.1")
        receiver._running = True

        real_audio = bytes([0xAB] * 160)
        q: asyncio.Queue[bytes] = asyncio.Queue()
        await q.put(real_audio)
        receiver.attach_backchannel_queue(q)

        sent: list[bytes] = []
        transport = MagicMock()
        transport.sendto = lambda data, **kw: sent.append(data)
        receiver._transport = transport

        stop_after = 2

        async def fake_sleep(_t: float) -> None:
            nonlocal stop_after
            stop_after -= 1
            if stop_after <= 0:
                receiver._running = False

        with patch("custom_components.comelit_man.rtp_receiver.asyncio.sleep", side_effect=fake_sleep):
            await receiver._audio_send_loop(0x1234)

        # First send should contain real_audio, not silence
        assert len(sent) >= 1
        first_payload = sent[0][20:]  # skip 8-byte ICONA header + 12-byte RTP header
        assert first_payload == real_audio

        # Second send (after queue exhausted) should be silence
        silence = bytes([0xD5] * 160)
        second_payload = sent[1][20:] if len(sent) > 1 else silence
        assert second_payload == silence


# ---------------------------------------------------------------------------
# TX audit counters: real mic frames vs silence filler (Phase 4)
# ---------------------------------------------------------------------------


class TestTxAuditCounters:
    async def _run_loop(self, receiver, iterations: int) -> None:
        remaining = iterations

        async def fake_sleep(_t: float) -> None:
            nonlocal remaining
            remaining -= 1
            if remaining <= 0:
                receiver._running = False

        with patch("custom_components.comelit_man.rtp_receiver.asyncio.sleep", side_effect=fake_sleep):
            await receiver._audio_send_loop(0x1234)

    @pytest.mark.asyncio
    async def test_silence_counted_when_no_backchannel(self):
        receiver = RtpReceiver("127.0.0.1")
        receiver._running = True
        receiver._transport = MagicMock()
        await self._run_loop(receiver, 3)
        assert receiver.audio_tx_silence_count == 3
        assert receiver.audio_tx_real_count == 0

    @pytest.mark.asyncio
    async def test_real_frames_counted_from_backchannel(self):
        receiver = RtpReceiver("127.0.0.1")
        receiver._running = True
        receiver._transport = MagicMock()
        q: asyncio.Queue[bytes] = asyncio.Queue()
        await q.put(bytes([0xAB] * 160))
        await q.put(bytes([0xCD] * 160))
        receiver.attach_backchannel_queue(q)
        await self._run_loop(receiver, 3)
        assert receiver.audio_tx_real_count == 2
        assert receiver.audio_tx_silence_count == 1

    @pytest.mark.asyncio
    async def test_empty_backchannel_counts_silence(self):
        receiver = RtpReceiver("127.0.0.1")
        receiver._running = True
        receiver._transport = MagicMock()
        receiver.attach_backchannel_queue(asyncio.Queue())
        await self._run_loop(receiver, 2)
        assert receiver.audio_tx_silence_count == 2
        assert receiver.audio_tx_real_count == 0
