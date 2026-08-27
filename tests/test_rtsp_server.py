"""Unit tests for LocalRtspServer — no real network clients needed."""

from __future__ import annotations

import asyncio
import struct
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.comelit_man.rtsp_server import (
    LocalRtspServer,
    _build_rtcp_sr,
    _build_rtp,
    _ntp_now,
    _TcpClient,
)

# ---------------------------------------------------------------------------
# _build_rtp helper
# ---------------------------------------------------------------------------


class TestBuildRtp:
    def test_fixed_header_length(self):
        """RTP packet is always 12 bytes of header + payload length."""
        payload = b"\xd5" * 20
        pkt = _build_rtp(pt=8, seq=0, ts=0, ssrc=0, payload=payload, marker=False)
        assert len(pkt) == 12 + len(payload)

    def test_version_bits(self):
        """First byte must have RTP version=2 (bits 7-6 = 0b10 → 0x80)."""
        pkt = _build_rtp(pt=0, seq=0, ts=0, ssrc=0, payload=b"", marker=False)
        assert pkt[0] == 0x80

    def test_payload_type_encoded(self):
        """Payload type is in low 7 bits of byte 1."""
        pkt = _build_rtp(pt=96, seq=0, ts=0, ssrc=0, payload=b"", marker=False)
        assert (pkt[1] & 0x7F) == 96

    def test_marker_bit_set(self):
        """Marker bit (bit 7 of byte 1) is set when marker=True."""
        pkt = _build_rtp(pt=8, seq=0, ts=0, ssrc=0, payload=b"", marker=True)
        assert pkt[1] & 0x80

    def test_marker_bit_clear(self):
        """Marker bit is clear when marker=False."""
        pkt = _build_rtp(pt=8, seq=0, ts=0, ssrc=0, payload=b"", marker=False)
        assert not (pkt[1] & 0x80)

    def test_sequence_number_encoded_be(self):
        """Sequence number is big-endian uint16 at bytes 2-3."""
        pkt = _build_rtp(pt=0, seq=0x1234, ts=0, ssrc=0, payload=b"", marker=False)
        seq = struct.unpack_from("!H", pkt, 2)[0]
        assert seq == 0x1234

    def test_timestamp_encoded_be(self):
        """Timestamp is big-endian uint32 at bytes 4-7."""
        pkt = _build_rtp(pt=0, seq=0, ts=0xDEADBEEF, ssrc=0, payload=b"", marker=False)
        ts = struct.unpack_from("!I", pkt, 4)[0]
        assert ts == 0xDEADBEEF

    def test_ssrc_encoded_be(self):
        """SSRC is big-endian uint32 at bytes 8-11."""
        pkt = _build_rtp(pt=0, seq=0, ts=0, ssrc=0xC0DE1234, payload=b"", marker=False)
        ssrc = struct.unpack_from("!I", pkt, 8)[0]
        assert ssrc == 0xC0DE1234

    def test_payload_appended(self):
        """Payload bytes follow immediately after the 12-byte header."""
        payload = b"\x01\x02\x03\x04"
        pkt = _build_rtp(pt=0, seq=0, ts=0, ssrc=0, payload=payload, marker=False)
        assert pkt[12:] == payload

    def test_seq_wraps_at_16_bits(self):
        """seq=0xFFFF + increment wraps to 0 (server is responsible, not _build_rtp)."""
        pkt = _build_rtp(pt=0, seq=0xFFFF, ts=0, ssrc=0, payload=b"", marker=False)
        seq = struct.unpack_from("!H", pkt, 2)[0]
        assert seq == 0xFFFF


# ---------------------------------------------------------------------------
# _build_sdp
# ---------------------------------------------------------------------------


class TestBuildSdp:
    def test_sdp_contains_video_track(self):
        server = LocalRtspServer()
        sdp = server._build_sdp()
        assert "m=video" in sdp
        assert "H264/90000" in sdp

    def test_sdp_video_pt96(self):
        server = LocalRtspServer()
        sdp = server._build_sdp()
        assert "RTP/AVP 96" in sdp
        assert "rtpmap:96 H264" in sdp

    def test_sdp_video_control(self):
        server = LocalRtspServer()
        sdp = server._build_sdp()
        assert "a=control:video" in sdp

    def test_sdp_audio_track(self):
        """SDP includes G.711 PCMA audio track from the device."""
        server = LocalRtspServer()
        sdp = server._build_sdp()
        assert "m=audio" in sdp
        assert "PCMA/8000" in sdp
        assert "a=control:audio" in sdp

    def test_sdp_uses_bind_host(self):
        server = LocalRtspServer(bind_host="192.168.1.1")
        sdp = server._build_sdp()
        assert "192.168.1.1" in sdp


# ---------------------------------------------------------------------------
# _parse_client_port
# ---------------------------------------------------------------------------


class TestParseClientPort:
    def test_parses_single_port(self):
        assert LocalRtspServer._parse_client_port("client_port=12345") == 12345

    def test_parses_port_range(self):
        assert LocalRtspServer._parse_client_port("client_port=12345-12346") == 12345

    def test_parses_from_full_transport_header(self):
        transport = "RTP/AVP;unicast;client_port=54320-54321"
        assert LocalRtspServer._parse_client_port(transport) == 54320

    def test_returns_zero_when_no_client_port(self):
        assert LocalRtspServer._parse_client_port("RTP/AVP/TCP;interleaved=0-1") == 0


# ---------------------------------------------------------------------------
# _parse_setup
# ---------------------------------------------------------------------------


class TestParseSetup:
    def test_tcp_interleaved_video(self):
        server = LocalRtspServer()
        client = _TcpClient(writer=MagicMock())
        transport = "RTP/AVP/TCP;unicast;interleaved=0-1"
        resp = server._parse_setup(transport, is_audio=False, client=client, client_host="127.0.0.1")
        assert "interleaved=0-1" in resp
        assert client.video_ch == 0

    def test_tcp_interleaved_audio(self):
        server = LocalRtspServer()
        client = _TcpClient(writer=MagicMock())
        transport = "RTP/AVP/TCP;unicast;interleaved=2-3"
        resp = server._parse_setup(transport, is_audio=True, client=client, client_host="127.0.0.1")
        assert "interleaved=2-3" in resp
        assert client.audio_ch == 2

    def test_udp_video_sets_udp_host(self):
        server = LocalRtspServer()
        server._video_server_port = 9000
        client = _TcpClient(writer=MagicMock())
        transport = "RTP/AVP;unicast;client_port=50000-50001"
        server._parse_setup(transport, is_audio=False, client=client, client_host="192.168.1.5")
        assert server._udp_host == "192.168.1.5"
        assert server._udp_video_port == 50000

    def test_udp_audio_sets_udp_audio_port(self):
        server = LocalRtspServer()
        server._audio_server_port = 9002
        client = _TcpClient(writer=MagicMock())
        transport = "RTP/AVP;unicast;client_port=51000-51001"
        server._parse_setup(transport, is_audio=True, client=client, client_host="192.168.1.5")
        assert server._udp_audio_port == 51000

    def test_tcp_response_includes_rtp_avp_tcp(self):
        server = LocalRtspServer()
        client = _TcpClient(writer=MagicMock())
        transport = "RTP/AVP/TCP;unicast;interleaved=0-1"
        resp = server._parse_setup(transport, is_audio=False, client=client, client_host="127.0.0.1")
        assert "RTP/AVP/TCP" in resp


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_drains_nal_queue(self):
        server = LocalRtspServer()
        server.nal_queue.put_nowait(b"\x00" * 10)
        server.nal_queue.put_nowait(b"\x00" * 10)
        server.reset()
        assert server.nal_queue.empty()

    def test_reset_drains_audio_queue(self):
        server = LocalRtspServer()
        server.audio_queue.put_nowait(b"\xd5" * 160)
        server.reset()
        assert server.audio_queue.empty()

    def test_reset_never_clears_rtp_counters(self):
        """reset() never resets RTP seq/ts — the persistent HA stream worker
        stays connected across calls and backwards jumps cause discontinuity errors."""
        server = LocalRtspServer()
        server._video_seq = 100
        server._audio_seq = 50
        server._audio_ts = 8000
        server.reset(renewal=False)
        assert server._video_seq == 100
        assert server._audio_seq == 50
        assert server._audio_ts == 8000

    def test_reset_sets_rebase_pending(self):
        """reset() sets _video_ts_rebase_pending so the feed loop rebases on next NAL."""
        server = LocalRtspServer()
        server._video_ts_rebase_pending = False
        server.reset()
        assert server._video_ts_rebase_pending is True

    def test_reset_with_renewal_also_preserves_counters(self):
        """renewal=True is kept for API compatibility but no longer changes behaviour."""
        server = LocalRtspServer()
        server._video_seq = 200
        server._audio_seq = 100
        server._audio_ts = 16000
        server.reset(renewal=True)
        assert server._video_seq == 200
        assert server._audio_seq == 100
        assert server._audio_ts == 16000

    def test_reset_preserves_active_clients(self):
        server = LocalRtspServer()
        fake_client = _TcpClient(writer=MagicMock())
        server._active_clients.append(fake_client)
        server.reset()
        assert len(server._active_clients) == 1

    def test_reset_queue_objects_unchanged(self):
        """reset() drains queues in-place — same objects for RtpReceiver to keep pushing."""
        server = LocalRtspServer()
        original_nal_q = server.nal_queue
        original_audio_q = server.audio_queue
        server.nal_queue.put_nowait(b"\x00" * 5)
        server.reset()
        assert server.nal_queue is original_nal_q
        assert server.audio_queue is original_audio_q


# ---------------------------------------------------------------------------
# start() / stop() lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("socket_enabled")
class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_returns_rtsp_url(self):
        server = LocalRtspServer()
        url = await server.start()
        try:
            assert url.startswith("rtsp://127.0.0.1:")
            assert "/intercom" in url
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_start_sets_running(self):
        server = LocalRtspServer()
        await server.start()
        try:
            assert server._running is True
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_running(self):
        server = LocalRtspServer()
        await server.start()
        await server.stop()
        assert server._running is False

    @pytest.mark.asyncio
    async def test_start_binds_rtsp_port(self):
        server = LocalRtspServer()
        await server.start()
        try:
            assert server._rtsp_port > 0
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_udp_sockets_are_created(self):
        """UDP sockets are created during start()."""
        server = LocalRtspServer(bind_host="127.0.0.1")
        await server.start()
        try:
            assert server._video_sock is not None
            assert server._audio_sock is not None
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_udp_sockets_bind_to_bind_host(self):
        """UDP sockets bind to bind_host, not all interfaces."""
        server = LocalRtspServer(bind_host="127.0.0.1")
        await server.start()
        try:
            assert server._video_sock.getsockname()[0] == "127.0.0.1"
            assert server._audio_sock.getsockname()[0] == "127.0.0.1"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_udp_sockets_get_ephemeral_port(self):
        """UDP sockets receive an OS-assigned ephemeral port (> 0)."""
        server = LocalRtspServer()
        await server.start()
        try:
            assert server._video_sock.getsockname()[1] > 0
            assert server._audio_sock.getsockname()[1] > 0
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_rtsp_url_property_after_start(self):
        server = LocalRtspServer()
        await server.start()
        try:
            assert str(server._rtsp_port) in server.rtsp_url
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_server(self):
        server = LocalRtspServer()
        await server.start()
        await server.stop()
        assert server._server is None

    @pytest.mark.asyncio
    async def test_start_creates_feed_tasks(self):
        server = LocalRtspServer()
        await server.start()
        try:
            assert len(server._feed_tasks) == 3
            for task in server._feed_tasks:
                assert not task.done()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_feed_tasks(self):
        server = LocalRtspServer()
        await server.start()
        feed_tasks = list(server._feed_tasks)
        await server.stop()
        # Give the event loop a moment to process cancellation
        await asyncio.sleep(0)
        assert len(server._feed_tasks) == 0


# ---------------------------------------------------------------------------
# _broadcast_rtp
# ---------------------------------------------------------------------------


class TestBroadcastRtp:
    def _make_client(self, video_ch: int | None = 0, audio_ch: int | None = 2) -> _TcpClient:
        mock_writer = MagicMock()
        mock_writer.is_closing.return_value = False
        mock_writer.write = MagicMock()
        client = _TcpClient(writer=mock_writer, video_ch=video_ch, audio_ch=audio_ch)
        return client

    def test_broadcast_video_to_tcp_client(self):
        server = LocalRtspServer()
        client = self._make_client(video_ch=0)
        server._active_clients.append(client)

        pkt = b"\x80\xe0" + b"\x00" * 10
        server._broadcast_rtp(pkt, is_video=True)

        client.writer.write.assert_called_once()
        written = client.writer.write.call_args[0][0]
        # TCP interleaved: $ + channel + length (2 BE) + RTP
        assert written[0] == 0x24  # '$'
        assert written[1] == 0  # video channel
        length = struct.unpack_from("!H", written, 2)[0]
        assert length == len(pkt)

    def test_broadcast_audio_to_tcp_client(self):
        server = LocalRtspServer()
        client = self._make_client(audio_ch=2)
        server._active_clients.append(client)

        pkt = b"\x80\x08" + b"\x00" * 10
        server._broadcast_rtp(pkt, is_video=False)

        written = client.writer.write.call_args[0][0]
        assert written[1] == 2  # audio channel

    def test_broadcast_skips_closing_client(self):
        server = LocalRtspServer()
        mock_writer = MagicMock()
        mock_writer.is_closing.return_value = True
        mock_writer.write = MagicMock()
        client = _TcpClient(writer=mock_writer, video_ch=0)
        server._active_clients.append(client)

        server._broadcast_rtp(b"\x80\xe0" + b"\x00" * 10, is_video=True)
        mock_writer.write.assert_not_called()

    def test_broadcast_skips_client_with_no_channel(self):
        server = LocalRtspServer()
        client = self._make_client(video_ch=None)  # No video channel set
        server._active_clients.append(client)

        server._broadcast_rtp(b"\x80\xe0" + b"\x00" * 10, is_video=True)
        client.writer.write.assert_not_called()

    def test_broadcast_to_multiple_clients(self):
        server = LocalRtspServer()
        c1 = self._make_client(video_ch=0)
        c2 = self._make_client(video_ch=0)
        server._active_clients.extend([c1, c2])

        server._broadcast_rtp(b"\x80\xe0" + b"\x00" * 10, is_video=True)
        c1.writer.write.assert_called_once()
        c2.writer.write.assert_called_once()

    def test_broadcast_exception_does_not_crash(self):
        server = LocalRtspServer()
        mock_writer = MagicMock()
        mock_writer.is_closing.return_value = False
        mock_writer.write.side_effect = OSError("broken pipe")
        client = _TcpClient(writer=mock_writer, video_ch=0)
        server._active_clients.append(client)

        # Must not raise
        server._broadcast_rtp(b"\x80\xe0" + b"\x00" * 10, is_video=True)


# ---------------------------------------------------------------------------
# _send_h264 (FU-A fragmentation)
# ---------------------------------------------------------------------------


class TestSendH264:
    def _setup_server_with_client(self) -> tuple[LocalRtspServer, list[bytes]]:
        server = LocalRtspServer()
        written: list[bytes] = []
        mock_writer = MagicMock()
        mock_writer.is_closing.return_value = False
        mock_writer.write = lambda data: written.append(data)
        client = _TcpClient(writer=mock_writer, video_ch=0)
        server._active_clients.append(client)
        return server, written

    def test_small_nal_single_rtp_packet(self):
        """NAL ≤ 1400 bytes is sent as a single RTP packet with marker=True."""
        server, written = self._setup_server_with_client()
        nal_data = b"\x65" + b"\xaa" * 100  # IDR NAL
        server._send_h264(nal_data)

        assert len(written) == 1
        # Interleaved header is 4 bytes; RTP follows
        rtp = written[0][4:]
        # Marker bit should be set for single packet
        assert rtp[1] & 0x80

    def test_large_nal_fragmented_fu_a(self):
        """NAL > 1400 bytes is fragmented into multiple FU-A RTP packets."""
        server, written = self._setup_server_with_client()
        nal_data = b"\x65" + b"\xbb" * 3000  # Large IDR NAL
        server._send_h264(nal_data)

        assert len(written) > 1

        # First fragment: FU indicator type=28, FU header start bit set
        first_rtp = written[0][4:]
        nal_header_byte = first_rtp[12]
        fu_header_byte = first_rtp[13]
        assert (nal_header_byte & 0x1F) == 28  # FU-A type
        assert fu_header_byte & 0x80  # Start bit

        # Last fragment: end bit set, marker bit set
        last_rtp = written[-1][4:]
        last_fu_header = last_rtp[13]
        assert last_fu_header & 0x40  # End bit
        assert last_rtp[1] & 0x80  # Marker bit

    def test_small_nal_increments_video_seq(self):
        """Each RTP packet sent increments the video sequence counter."""
        server, _ = self._setup_server_with_client()
        assert server._video_seq == 0
        server._send_h264(b"\x65" + b"\x00" * 50)
        assert server._video_seq == 1

    def test_large_nal_increments_seq_per_fragment(self):
        """Each FU-A fragment increments the sequence counter."""
        server, written = self._setup_server_with_client()
        nal_data = b"\x65" + b"\x00" * 3000
        server._send_h264(nal_data)
        assert server._video_seq == len(written)

    def test_start_code_4_bytes_stripped(self):
        """_drain_nal_queue_fallback strips 4-byte start codes before _send_h264."""
        # This tests the stripping logic applied to NALs from the queue
        server = LocalRtspServer()
        server._running = True
        written: list[bytes] = []
        mock_writer = MagicMock()
        mock_writer.is_closing.return_value = False
        mock_writer.write = lambda data: written.append(data)
        client = _TcpClient(writer=mock_writer, video_ch=0)
        server._active_clients.append(client)

        # The feed loop reads from nal_queue and strips start codes
        nal_with_start = b"\x00\x00\x00\x01\x65" + b"\xcc" * 20
        server.nal_queue.put_nowait(nal_with_start)

        # Verify _send_h264 does not receive the start code
        calls = []
        original_send = server._send_h264

        def capture_send(nal_data):
            calls.append(nal_data)
            original_send(nal_data)

        server._send_h264 = capture_send
        # Drain queue manually (feed loop logic)
        if not server.nal_queue.empty():
            nal = server.nal_queue.get_nowait()
            if nal[:4] == b"\x00\x00\x00\x01":
                nal = nal[4:]
            server._send_h264(nal)

        assert len(calls) == 1
        assert not calls[0].startswith(b"\x00\x00\x00\x01")


# ---------------------------------------------------------------------------
# rtsp_url property
# ---------------------------------------------------------------------------


class TestRtspUrl:
    def test_rtsp_url_format(self):
        server = LocalRtspServer()
        server._rtsp_port = 8554
        assert server.rtsp_url == "rtsp://127.0.0.1:8554/intercom"

    def test_rtsp_url_custom_bind_host(self):
        server = LocalRtspServer(bind_host="0.0.0.0")
        server._rtsp_port = 8554
        assert server.rtsp_url == "rtsp://0.0.0.0:8554/intercom"


# ===========================================================================
# TRACK A — direct unit tests, no TCP client required
# ===========================================================================

# ---------------------------------------------------------------------------
# A-1: mark_ready, mark_not_ready, disconnect_clients, reset rtp_queue drain
# ---------------------------------------------------------------------------


class TestMarkReadyNotReady:
    def test_mark_ready_sets_event(self):
        server = LocalRtspServer()
        assert not server._ready_event.is_set()
        server.mark_ready()
        assert server._ready_event.is_set()

    def test_mark_not_ready_clears_event(self):
        server = LocalRtspServer()
        server._ready_event.set()
        server.mark_not_ready()
        assert not server._ready_event.is_set()

    def test_mark_ready_then_not_ready(self):
        server = LocalRtspServer()
        server.mark_ready()
        server.mark_not_ready()
        assert not server._ready_event.is_set()


class TestDisconnectClients:
    def test_closes_all_writers(self):
        server = LocalRtspServer()
        w1, w2 = MagicMock(), MagicMock()
        server._active_clients.extend([_TcpClient(writer=w1), _TcpClient(writer=w2)])
        server.disconnect_clients()
        w1.close.assert_called_once()
        w2.close.assert_called_once()

    def test_empties_active_clients(self):
        server = LocalRtspServer()
        server._active_clients.append(_TcpClient(writer=MagicMock()))
        server.disconnect_clients()
        assert server._active_clients == []

    def test_no_clients_silent(self):
        server = LocalRtspServer()
        server.disconnect_clients()  # must not raise

    def test_writer_close_exception_suppressed(self):
        server = LocalRtspServer()
        w = MagicMock()
        w.close.side_effect = OSError("broken pipe")
        server._active_clients.append(_TcpClient(writer=w))
        server.disconnect_clients()  # must not raise
        assert server._active_clients == []


class TestResetRtpQueueDrain:
    def test_reset_drains_rtp_queue(self):
        server = LocalRtspServer()
        server.rtp_queue.put_nowait(b"\x80\x60" + b"\x00" * 10)
        server.rtp_queue.put_nowait(b"\x80\x60" + b"\x00" * 10)
        server.reset()
        assert server.rtp_queue.empty()

    def test_reset_primes_clients_with_video_ch(self):
        server = LocalRtspServer()
        written: list[bytes] = []
        w = MagicMock()
        w.write = lambda d: written.append(d)
        server._active_clients.append(_TcpClient(writer=w, video_ch=0))
        server.reset()
        rtp_writes = [x for x in written if x and x[0] == 0x24]
        assert len(rtp_writes) >= 2  # SPS + PPS primed


# ---------------------------------------------------------------------------
# A-2: _send(), _wait_for_teardown(), UDP path in _broadcast_rtp()
# ---------------------------------------------------------------------------


class TestSendStaticMethod:
    def test_writes_ok_response(self):
        writer = MagicMock()
        LocalRtspServer._send(writer, cseq="1")
        written = writer.write.call_args[0][0].decode()
        assert "RTSP/1.0 200 OK" in written

    def test_includes_cseq(self):
        writer = MagicMock()
        LocalRtspServer._send(writer, cseq="42")
        written = writer.write.call_args[0][0].decode()
        assert "CSeq: 42" in written

    def test_includes_extra(self):
        writer = MagicMock()
        LocalRtspServer._send(writer, cseq="1", extra="Session: 87654321\r\n")
        written = writer.write.call_args[0][0].decode()
        assert "Session: 87654321" in written

    def test_empty_extra(self):
        writer = MagicMock()
        LocalRtspServer._send(writer, cseq="5")
        written = writer.write.call_args[0][0]
        assert b"RTSP/1.0 200 OK" in written
        assert b"CSeq: 5" in written


class TestBroadcastRtpUdpPath:
    def test_udp_video_sendto(self):
        server = LocalRtspServer()
        server._udp_host = "192.168.1.10"
        server._udp_video_port = 5004
        sock = MagicMock()
        server._video_sock = sock
        pkt = b"\x80\xe0" + b"\x00" * 10
        server._broadcast_rtp(pkt, is_video=True)
        sock.sendto.assert_called_once_with(pkt, ("192.168.1.10", 5004))

    def test_udp_audio_sendto(self):
        server = LocalRtspServer()
        server._udp_host = "192.168.1.10"
        server._udp_audio_port = 5006
        sock = MagicMock()
        server._audio_sock = sock
        pkt = b"\x80\x08" + b"\x00" * 10
        server._broadcast_rtp(pkt, is_video=False)
        sock.sendto.assert_called_once_with(pkt, ("192.168.1.10", 5006))

    def test_no_udp_host_skips(self):
        server = LocalRtspServer()
        server._udp_host = None
        sock = MagicMock()
        server._video_sock = sock
        server._broadcast_rtp(b"\x80\xe0" + b"\x00" * 10, is_video=True)
        sock.sendto.assert_not_called()

    def test_udp_zero_port_skips(self):
        server = LocalRtspServer()
        server._udp_host = "192.168.1.10"
        server._udp_video_port = 0
        sock = MagicMock()
        server._video_sock = sock
        server._broadcast_rtp(b"\x80\xe0" + b"\x00" * 10, is_video=True)
        sock.sendto.assert_not_called()

    def test_udp_os_error_suppressed(self):
        server = LocalRtspServer()
        server._udp_host = "192.168.1.10"
        server._udp_video_port = 5004
        sock = MagicMock()
        sock.sendto.side_effect = OSError("network unreachable")
        server._video_sock = sock
        server._broadcast_rtp(b"\x80\xe0" + b"\x00" * 10, is_video=True)  # must not raise


# ---------------------------------------------------------------------------
# A-3: _prime_client_with_parameter_sets(), _send_initial_sr_to_client()
# ---------------------------------------------------------------------------


def _make_writing_client(video_ch=0, audio_ch=None):
    """Return (_TcpClient, written_list) where writer.write appends to the list."""
    written: list[bytes] = []
    w = MagicMock()
    w.write = lambda data: written.append(data)
    return _TcpClient(writer=w, video_ch=video_ch, audio_ch=audio_ch), written


class TestPrimeClientWithParameterSets:
    def test_no_video_ch_returns_early(self):
        server = LocalRtspServer()
        client, written = _make_writing_client(video_ch=None)
        server._prime_client_with_parameter_sets(client)
        assert written == []

    def test_sends_sps_and_pps_rtp(self):
        server = LocalRtspServer()
        client, written = _make_writing_client(video_ch=0)
        server._prime_client_with_parameter_sets(client)
        rtp_writes = [x for x in written if x and x[0] == 0x24]
        assert len(rtp_writes) >= 2

    def test_increments_video_seq_by_two(self):
        server = LocalRtspServer()
        client, _ = _make_writing_client(video_ch=0)
        initial = server._video_seq
        server._prime_client_with_parameter_sets(client)
        assert server._video_seq == initial + 2

    def test_empty_sps_skipped(self):
        server = LocalRtspServer()
        server._latest_sps = b""  # triggers `if not nal: continue`
        client, written = _make_writing_client(video_ch=0)
        server._prime_client_with_parameter_sets(client)
        rtp_writes = [x for x in written if x and x[0] == 0x24]
        assert len(rtp_writes) == 1  # only PPS written

    def test_write_exception_does_not_raise(self):
        server = LocalRtspServer()
        w = MagicMock()
        w.write.side_effect = OSError("broken pipe")
        client = _TcpClient(writer=w, video_ch=0)
        server._prime_client_with_parameter_sets(client)  # must not raise

    def test_uses_correct_channel(self):
        server = LocalRtspServer()
        client, written = _make_writing_client(video_ch=4)
        server._prime_client_with_parameter_sets(client)
        rtp_writes = [x for x in written if x and x[0] == 0x24]
        for w in rtp_writes:
            assert w[1] == 4  # channel byte


class TestSendInitialSrToClient:
    def test_video_sr_when_pkt_count_positive(self):
        server = LocalRtspServer()
        server._video_pkt_count = 10
        server._video_octet_count = 1400
        client, written = _make_writing_client(video_ch=0)
        server._send_initial_sr_to_client(client)
        assert len(written) == 1
        assert written[0][0] == 0x24

    def test_audio_sr_when_pkt_count_positive(self):
        server = LocalRtspServer()
        server._audio_pkt_count = 5
        server._audio_octet_count = 800
        client, written = _make_writing_client(video_ch=None, audio_ch=2)
        server._send_initial_sr_to_client(client)
        assert len(written) == 1

    def test_both_srs_when_both_counts_positive(self):
        server = LocalRtspServer()
        server._video_pkt_count = 10
        server._audio_pkt_count = 5
        client, written = _make_writing_client(video_ch=0, audio_ch=2)
        server._send_initial_sr_to_client(client)
        assert len(written) == 2

    def test_no_video_sr_when_pkt_count_zero(self):
        server = LocalRtspServer()
        server._video_pkt_count = 0
        client, written = _make_writing_client(video_ch=0)
        server._send_initial_sr_to_client(client)
        assert len(written) == 0

    def test_no_audio_sr_when_pkt_count_zero(self):
        server = LocalRtspServer()
        server._audio_pkt_count = 0
        client, written = _make_writing_client(video_ch=None, audio_ch=2)
        server._send_initial_sr_to_client(client)
        assert len(written) == 0

    def test_exception_suppressed(self):
        server = LocalRtspServer()
        server._video_pkt_count = 10
        w = MagicMock()
        w.write.side_effect = OSError("broken pipe")
        client = _TcpClient(writer=w, video_ch=0)
        server._send_initial_sr_to_client(client)  # must not raise

    def test_rtcp_channel_is_video_ch_plus_one(self):
        server = LocalRtspServer()
        server._video_pkt_count = 1
        client, written = _make_writing_client(video_ch=0)
        server._send_initial_sr_to_client(client)
        assert written[0][1] == 1  # video_ch + 1


# ---------------------------------------------------------------------------
# A-4: _translate_video_ts() — first call, rebase pending, forward, backward
# ---------------------------------------------------------------------------


class TestTranslateVideoTs:
    def test_first_call_sets_offset_and_clears_rebase(self):
        server = LocalRtspServer()
        assert server._last_device_ts is None
        server._video_ts_out = 0
        server._translate_video_ts(1000)
        expected_offset = (0 + 1 - 1000) & 0xFFFFFFFF
        assert server._video_ts_offset == expected_offset
        assert server._last_device_ts == 1000
        assert server._video_ts_rebase_pending is False

    def test_rebase_pending_forces_rebase_even_with_last_ts(self):
        server = LocalRtspServer()
        server._last_device_ts = 100
        server._video_ts_out = 9000
        server._video_ts_rebase_pending = True
        server._translate_video_ts(200)
        expected_offset = (9000 + 1 - 200) & 0xFFFFFFFF
        assert server._video_ts_offset == expected_offset
        assert server._video_ts_rebase_pending is False

    def test_normal_forward_advance_no_rebase(self):
        server = LocalRtspServer()
        server._last_device_ts = 1000
        server._video_ts_rebase_pending = False
        server._video_ts_offset = 0
        server._video_ts_out = 1000
        server._translate_video_ts(1100)
        # forward = 100 < 0x80000000 → no rebase; offset unchanged at 0
        assert server._video_ts_out == 1100
        assert server._last_device_ts == 1100

    def test_backward_jump_triggers_rebase(self):
        server = LocalRtspServer()
        server._last_device_ts = 1000
        server._video_ts_rebase_pending = False
        server._video_ts_out = 5000
        server._video_ts_offset = 0
        server._translate_video_ts(100)  # backward: (100-1000)&0xFFFFFFFF >> 0x80000000
        expected_offset = (5000 + 1 - 100) & 0xFFFFFFFF
        assert server._video_ts_offset == expected_offset

    def test_output_ts_is_device_plus_offset(self):
        server = LocalRtspServer()
        server._last_device_ts = 500
        server._video_ts_rebase_pending = False
        server._video_ts_offset = 100
        server._video_ts_out = 600
        server._translate_video_ts(600)
        assert server._video_ts_out == (600 + 100) & 0xFFFFFFFF

    def test_output_is_nondecreasing_on_forward(self):
        server = LocalRtspServer()
        server._translate_video_ts(1000)
        out1 = server._video_ts_out
        server._translate_video_ts(1100)
        out2 = server._video_ts_out
        assert out2 >= out1

    def test_32bit_wrap(self):
        server = LocalRtspServer()
        server._video_ts_out = 0xFFFFFFF0
        server._video_ts_rebase_pending = True
        server._translate_video_ts(0x10)
        # offset = (0xFFFFFFF0 + 1 - 0x10) & 0xFFFFFFFF = 0xFFFFFFE1
        assert server._video_ts_out == (0x10 + server._video_ts_offset) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# A-5: _drain_nal_queue_fallback(), _broadcast_rtcp(), _build_rtcp_sr(), _ntp_now()
# ---------------------------------------------------------------------------


class TestDrainNalQueueFallback:
    @pytest.mark.asyncio
    async def test_strips_4byte_start_code(self):
        server = LocalRtspServer()
        captured: list[bytes] = []
        server._send_h264 = lambda nal: captured.append(nal)  # type: ignore[method-assign]
        server.nal_queue.put_nowait((1000, b"\x00\x00\x00\x01\x65" + b"\xaa" * 10))
        await server._drain_nal_queue_fallback()
        assert len(captured) == 1
        assert not captured[0].startswith(b"\x00\x00\x00\x01")
        assert captured[0][0] == 0x65

    @pytest.mark.asyncio
    async def test_strips_3byte_start_code(self):
        server = LocalRtspServer()
        captured: list[bytes] = []
        server._send_h264 = lambda nal: captured.append(nal)  # type: ignore[method-assign]
        server.nal_queue.put_nowait((2000, b"\x00\x00\x01\x65" + b"\xbb" * 10))
        await server._drain_nal_queue_fallback()
        assert len(captured) == 1
        assert captured[0][0] == 0x65

    @pytest.mark.asyncio
    async def test_no_start_code_passthrough(self):
        server = LocalRtspServer()
        captured: list[bytes] = []
        server._send_h264 = lambda nal: captured.append(nal)  # type: ignore[method-assign]
        raw = b"\x65" + b"\xcc" * 10
        server.nal_queue.put_nowait((3000, raw))
        await server._drain_nal_queue_fallback()
        assert captured[0] == raw

    @pytest.mark.asyncio
    async def test_empty_nal_after_strip_skipped(self):
        server = LocalRtspServer()
        captured: list[bytes] = []
        server._send_h264 = lambda nal: captured.append(nal)  # type: ignore[method-assign]
        server.nal_queue.put_nowait((4000, b"\x00\x00\x00\x01"))  # start code only
        await server._drain_nal_queue_fallback()
        assert len(captured) == 0

    @pytest.mark.asyncio
    async def test_caches_sps(self):
        server = LocalRtspServer()
        server._send_h264 = lambda nal: None  # type: ignore[method-assign]
        sps = b"\x67" + b"\x42" * 8  # NAL type 7
        server.nal_queue.put_nowait((5000, sps))
        await server._drain_nal_queue_fallback()
        assert server._latest_sps == sps

    @pytest.mark.asyncio
    async def test_caches_pps(self):
        server = LocalRtspServer()
        server._send_h264 = lambda nal: None  # type: ignore[method-assign]
        pps = b"\x68" + b"\xce" * 3  # NAL type 8
        server.nal_queue.put_nowait((6000, pps))
        await server._drain_nal_queue_fallback()
        assert server._latest_pps == pps

    @pytest.mark.asyncio
    async def test_processes_multiple_nals(self):
        server = LocalRtspServer()
        captured: list[bytes] = []
        server._send_h264 = lambda nal: captured.append(nal)  # type: ignore[method-assign]
        for i in range(3):
            server.nal_queue.put_nowait((i * 3000, b"\x65" + bytes([i]) * 5))
        await server._drain_nal_queue_fallback()
        assert len(captured) == 3

    @pytest.mark.asyncio
    async def test_empty_queue_is_noop(self):
        server = LocalRtspServer()
        captured: list[bytes] = []
        server._send_h264 = lambda nal: captured.append(nal)  # type: ignore[method-assign]
        await server._drain_nal_queue_fallback()
        assert len(captured) == 0

    @pytest.mark.asyncio
    async def test_queue_empty_race_breaks_loop(self):
        server = LocalRtspServer()
        captured: list[bytes] = []
        server._send_h264 = lambda nal: captured.append(nal)  # type: ignore[method-assign]
        server.nal_queue.put_nowait((1000, b"\x65"))  # non-empty so loop enters
        with patch.object(server.nal_queue, "get_nowait", side_effect=asyncio.QueueEmpty):
            await server._drain_nal_queue_fallback()
        assert len(captured) == 0


class TestBroadcastRtcp:
    def _make(self, video_ch=0, audio_ch=None, closing=False):
        written: list[bytes] = []
        w = MagicMock()
        w.is_closing.return_value = closing
        w.write = lambda data: written.append(data)
        return _TcpClient(writer=w, video_ch=video_ch, audio_ch=audio_ch), written

    def test_sends_to_tcp_client_video(self):
        server = LocalRtspServer()
        client, written = self._make(video_ch=0)
        server._active_clients.append(client)
        server._broadcast_rtcp(b"\x80\xc8" + b"\x00" * 26, is_video=True)
        assert len(written) == 1
        assert written[0][1] == 1  # video_ch + 1

    def test_sends_to_tcp_client_audio(self):
        server = LocalRtspServer()
        client, written = self._make(video_ch=None, audio_ch=2)
        server._active_clients.append(client)
        server._broadcast_rtcp(b"\x80\xc8" + b"\x00" * 26, is_video=False)
        assert len(written) == 1
        assert written[0][1] == 3  # audio_ch + 1

    def test_skips_closing_client(self):
        server = LocalRtspServer()
        client, written = self._make(video_ch=0, closing=True)
        server._active_clients.append(client)
        server._broadcast_rtcp(b"\x00" * 10, is_video=True)
        assert len(written) == 0

    def test_skips_no_channel(self):
        server = LocalRtspServer()
        client, written = self._make(video_ch=None)
        server._active_clients.append(client)
        server._broadcast_rtcp(b"\x00" * 10, is_video=True)
        assert len(written) == 0

    def test_exception_removes_dead_client(self):
        server = LocalRtspServer()
        w = MagicMock()
        w.is_closing.return_value = False
        w.write.side_effect = OSError("broken pipe")
        client = _TcpClient(writer=w, video_ch=0)
        server._active_clients.append(client)
        server._broadcast_rtcp(b"\x00" * 10, is_video=True)  # must not raise
        assert client not in server._active_clients

    def test_udp_video_port_plus_one(self):
        server = LocalRtspServer()
        server._udp_host = "192.168.1.10"
        server._udp_video_port = 5004
        sock = MagicMock()
        server._video_sock = sock
        server._broadcast_rtcp(b"\x80\xc8" + b"\x00" * 26, is_video=True)
        sock.sendto.assert_called_once_with(b"\x80\xc8" + b"\x00" * 26, ("192.168.1.10", 5005))

    def test_udp_audio_port_plus_one(self):
        server = LocalRtspServer()
        server._udp_host = "192.168.1.10"
        server._udp_audio_port = 5006
        sock = MagicMock()
        server._audio_sock = sock
        server._broadcast_rtcp(b"\x80\xc8" + b"\x00" * 26, is_video=False)
        sock.sendto.assert_called_once_with(b"\x80\xc8" + b"\x00" * 26, ("192.168.1.10", 5007))

    def test_no_udp_host_skips_sendto(self):
        server = LocalRtspServer()
        server._udp_host = None
        sock = MagicMock()
        server._video_sock = sock
        server._broadcast_rtcp(b"\x00" * 10, is_video=True)
        sock.sendto.assert_not_called()

    def test_udp_os_error_suppressed(self):
        server = LocalRtspServer()
        server._udp_host = "192.168.1.10"
        server._udp_video_port = 5004
        sock = MagicMock()
        sock.sendto.side_effect = OSError("net unreachable")
        server._video_sock = sock
        server._broadcast_rtcp(b"\x00" * 10, is_video=True)  # must not raise


class TestBuildRtcpSr:
    def _sr(self, **kw):
        defaults = dict(
            ssrc=0xC0DE1234,
            ntp_secs=3_900_000_000,
            ntp_frac=0,
            rtp_ts=12345,
            pkt_count=100,
            octet_count=14000,
        )
        defaults.update(kw)
        return _build_rtcp_sr(**defaults)

    def test_returns_bytes(self):
        assert isinstance(self._sr(), bytes)

    def test_sr_pt_is_200(self):
        result = self._sr()
        assert result[1] == 200

    def test_sdes_pt_is_202(self):
        result = self._sr()
        # SR is 28 bytes; SDES header starts right after
        assert result[29] == 202

    def test_ssrc_encoded_in_sr(self):
        result = self._sr(ssrc=0xDEADBEEF)
        assert struct.unpack_from("!I", result, 4)[0] == 0xDEADBEEF

    def test_compound_packet_32bit_aligned(self):
        assert len(self._sr()) % 4 == 0

    def test_sr_length_word_is_6(self):
        result = self._sr()
        # V=2 P=0 RC=0 -> 0x80; PT=200; length (16-bit word count minus 1) = 6
        length = struct.unpack_from("!H", result, 2)[0]
        assert length == 6

    def test_pkt_count_encoded(self):
        result = self._sr(pkt_count=42)
        # SR layout: 0x80 PT len ssrc ntp_hi ntp_lo rtp_ts pkt_count octet_count
        # bytes: 1+1+2+4+4+4+4+4+4 = 28; pkt_count at offset 20
        pkt_count = struct.unpack_from("!I", result, 20)[0]
        assert pkt_count == 42


class TestNtpNow:
    def test_returns_tuple_of_two_ints(self):
        secs, frac = _ntp_now()
        assert isinstance(secs, int)
        assert isinstance(frac, int)

    def test_secs_above_ntp_2023_threshold(self):
        secs, _ = _ntp_now()
        # 2023-01-01 in NTP time ≈ 3,913,056,000
        assert secs > 3_900_000_000

    def test_frac_is_32bit_unsigned(self):
        _, frac = _ntp_now()
        assert 0 <= frac <= 0xFFFFFFFF

    def test_consecutive_calls_nondecreasing(self):
        s1, _ = _ntp_now()
        s2, _ = _ntp_now()
        assert s2 >= s1


# ===========================================================================
# TRACK B — async feed loops and RTSP client handler
# ===========================================================================


# ---------------------------------------------------------------------------
# Shared helpers for Track B
# ---------------------------------------------------------------------------


class _RequestReader:
    """Fake StreamReader that returns pre-queued byte chunks sequentially."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def read(self, n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    async def readexactly(self, n: int) -> bytes:
        chunk = self._chunks.pop(0) if self._chunks else b""
        if not chunk:
            raise asyncio.IncompleteReadError(b"", n)
        return chunk[:n]


class _ResponseWriter:
    """Fake StreamWriter that captures all written bytes."""

    def __init__(self, socket: object = None, peername: tuple | None = ("127.0.0.1", 50000)) -> None:
        self._buf = bytearray()
        self._sock = socket
        self._peername = peername
        self.closed = False

    def write(self, data: bytes) -> None:
        self._buf.extend(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def get_extra_info(self, key: str, default: object = None) -> object:
        if key == "peername":
            return self._peername
        if key == "socket":
            return self._sock
        return default

    def is_closing(self) -> bool:
        return False

    @property
    def data(self) -> bytes:
        return bytes(self._buf)


# ---------------------------------------------------------------------------
# B-4: _rtcp_sr_loop
# ---------------------------------------------------------------------------


class TestRtcpSrLoop:
    @pytest.mark.asyncio
    async def test_pre_loop_wait_then_broadcasts_to_active_client(self):
        """Event-gated wait resolves, then SRs are sent to active clients."""
        server = LocalRtspServer()
        server._running = True
        server._video_pkt_count = 1
        server._audio_pkt_count = 1
        server._first_media_event.set()

        w = MagicMock()
        w.is_closing.return_value = False
        written: list[bytes] = []
        w.write = lambda d: written.append(d)
        server._active_clients.append(_TcpClient(writer=w, video_ch=0, audio_ch=2))

        async def mock_sleep(t: float) -> None:
            server._running = False

        with patch("custom_components.comelit_man.rtsp_server.asyncio.sleep", mock_sleep):
            await server._rtcp_sr_loop()

        rtcp_writes = [x for x in written if x and x[0] == 0x24]
        assert len(rtcp_writes) >= 2  # video SR + audio SR

    @pytest.mark.asyncio
    async def test_no_clients_skips_sr(self):
        """When no clients are registered, SR broadcast is skipped."""
        server = LocalRtspServer()
        server._running = True
        server._video_pkt_count = 1
        server._first_media_event.set()

        sleep_count = 0
        broadcasts: list[tuple] = []

        async def mock_sleep(t: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            server._running = False

        orig_brtcp = server._broadcast_rtcp

        def track_broadcast(pkt, is_video):
            broadcasts.append((pkt, is_video))
            orig_brtcp(pkt, is_video)

        server._broadcast_rtcp = track_broadcast  # type: ignore[method-assign]

        with patch("custom_components.comelit_man.rtsp_server.asyncio.sleep", mock_sleep):
            await server._rtcp_sr_loop()

        assert broadcasts == []  # no clients → no broadcasts

    @pytest.mark.asyncio
    async def test_cancelled_error_exits_cleanly(self):
        """CancelledError inside the SR loop is silently swallowed."""
        server = LocalRtspServer()
        server._running = True
        server._video_pkt_count = 1
        server._first_media_event.set()

        async def mock_sleep(t: float) -> None:
            raise asyncio.CancelledError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.sleep", mock_sleep):
            await server._rtcp_sr_loop()  # must not raise

    @pytest.mark.asyncio
    async def test_exception_caught(self):
        """Unexpected exceptions in the SR loop are caught and logged."""
        server = LocalRtspServer()
        server._running = True
        server._video_pkt_count = 1
        server._first_media_event.set()

        async def mock_sleep(t: float) -> None:
            raise ValueError("unexpected")

        with patch("custom_components.comelit_man.rtsp_server.asyncio.sleep", mock_sleep):
            await server._rtcp_sr_loop()  # must not raise

    @pytest.mark.asyncio
    async def test_udp_client_gets_sr(self):
        """UDP host triggers SR broadcast path."""
        server = LocalRtspServer()
        server._running = True
        server._video_pkt_count = 1
        server._audio_pkt_count = 1
        server._first_media_event.set()
        server._udp_host = "192.168.1.5"
        server._udp_video_port = 5004
        sock = MagicMock()
        server._video_sock = sock

        sleep_count = 0

        async def mock_sleep(t: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            server._running = False

        with patch("custom_components.comelit_man.rtsp_server.asyncio.sleep", mock_sleep):
            await server._rtcp_sr_loop()

        assert sock.sendto.called


# ---------------------------------------------------------------------------
# B-3b: _audio_feed_loop
# ---------------------------------------------------------------------------


class TestAudioFeedLoop:
    @pytest.mark.asyncio
    async def test_happy_path_broadcasts_audio(self):
        """Audio payload from queue is broadcast."""
        server = LocalRtspServer()
        server._running = True
        broadcasts: list[bytes] = []
        server._broadcast_rtp = lambda pkt, is_video: broadcasts.append(pkt)  # type: ignore[method-assign]

        payload = b"\xd5" * 160
        items: list[bytes] = [payload]

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            if items:
                return items.pop(0)
            raise asyncio.CancelledError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._audio_feed_loop()

        assert len(broadcasts) == 1
        rtp = broadcasts[0]
        assert rtp[1] & 0x7F == 8  # PT=8 PCMA

    @pytest.mark.asyncio
    async def test_timeout_no_silence_when_no_clients(self):
        """Timeout with no active clients → nothing broadcast."""
        server = LocalRtspServer()
        server._running = True
        broadcasts: list[bytes] = []
        server._broadcast_rtp = lambda pkt, is_video: broadcasts.append(pkt)  # type: ignore[method-assign]

        call_count = 0

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            nonlocal call_count
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            call_count += 1
            if call_count >= 2:
                server._running = False
            raise TimeoutError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._audio_feed_loop()

        assert len(broadcasts) == 0

    @pytest.mark.asyncio
    async def test_timeout_sends_silence_when_idle(self):
        """Timeout while idle (clients connected, ready_event clear) → silence frame broadcast."""
        server = LocalRtspServer()
        server._running = True
        broadcasts: list[bytes] = []
        server._broadcast_rtp = lambda pkt, is_video: broadcasts.append(pkt)  # type: ignore[method-assign]
        server._active_clients.append(MagicMock())  # type: ignore[arg-type]

        call_count = 0

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            nonlocal call_count
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError
            raise TimeoutError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._audio_feed_loop()

        assert len(broadcasts) == 1
        rtp = broadcasts[0]
        assert rtp[1] & 0x7F == 8  # PT=8 PCMA
        assert rtp[12:] == b"\xd5" * 160  # silence payload

    @pytest.mark.asyncio
    async def test_timeout_no_silence_during_live_call(self):
        """Timeout while ready_event is set (live call) → nothing broadcast."""
        server = LocalRtspServer()
        server._running = True
        server._ready_event.set()
        broadcasts: list[bytes] = []
        server._broadcast_rtp = lambda pkt, is_video: broadcasts.append(pkt)  # type: ignore[method-assign]
        server._active_clients.append(MagicMock())  # type: ignore[arg-type]

        call_count = 0

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            nonlocal call_count
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            call_count += 1
            if call_count >= 2:
                server._running = False
            raise TimeoutError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._audio_feed_loop()

        assert len(broadcasts) == 0

    @pytest.mark.asyncio
    async def test_cancelled_error_exits_cleanly(self):
        server = LocalRtspServer()
        server._running = True

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            raise asyncio.CancelledError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._audio_feed_loop()  # must not raise

    @pytest.mark.asyncio
    async def test_exception_caught(self):
        server = LocalRtspServer()
        server._running = True

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            server._running = False
            raise ValueError("unexpected")

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._audio_feed_loop()  # must not raise

    @pytest.mark.asyncio
    async def test_increments_audio_counters(self):
        """Audio pkt_count and octet_count are incremented per packet."""
        server = LocalRtspServer()
        server._running = True
        server._broadcast_rtp = lambda *a, **kw: None  # type: ignore[method-assign]

        payload = b"\xd5" * 160
        items: list[bytes] = [payload]

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            if items:
                return items.pop(0)
            raise asyncio.CancelledError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._audio_feed_loop()

        assert server._audio_pkt_count == 1
        assert server._audio_octet_count == len(payload)


# ---------------------------------------------------------------------------
# B-2: _video_rtp_passthrough_loop
# ---------------------------------------------------------------------------


def _make_rtp_pkt(
    payload: bytes = b"\x65" + b"\xaa" * 10,
    ts: int = 1000,
    seq: int = 1,
) -> bytes:
    return struct.pack("!BBHII", 0x80, 0xE0, seq, ts, 0xDEADBEEF) + payload


class TestVideoRtpPassthroughLoop:
    @pytest.mark.asyncio
    async def test_happy_path_broadcasts_rewritten_rtp(self):
        """Valid RTP from queue has header rewritten and is broadcast."""
        server = LocalRtspServer()
        server._running = True
        broadcasts: list[tuple] = []
        server._broadcast_rtp = lambda pkt, is_video: broadcasts.append((pkt, is_video))  # type: ignore[method-assign]

        rtp = _make_rtp_pkt()
        items: list[bytes] = [rtp]

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            if items:
                return items.pop(0)
            server._running = False
            raise TimeoutError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._video_rtp_passthrough_loop()

        assert len(broadcasts) == 1
        assert broadcasts[0][1] is True  # is_video

    @pytest.mark.asyncio
    async def test_short_packet_skipped(self):
        """Packets shorter than 12 bytes are skipped."""
        server = LocalRtspServer()
        server._running = True
        broadcasts: list[bytes] = []
        server._broadcast_rtp = lambda pkt, is_video: broadcasts.append(pkt)  # type: ignore[method-assign]

        items: list[bytes] = [b"\x80\xe0" + b"\x00" * 5]  # 7 bytes < 12

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            if items:
                return items.pop(0)
            server._running = False
            raise TimeoutError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._video_rtp_passthrough_loop()

        assert broadcasts == []

    @pytest.mark.asyncio
    async def test_empty_payload_skipped(self):
        """Exactly 12-byte RTP with no payload is skipped."""
        server = LocalRtspServer()
        server._running = True
        broadcasts: list[bytes] = []
        server._broadcast_rtp = lambda pkt, is_video: broadcasts.append(pkt)  # type: ignore[method-assign]

        items: list[bytes] = [b"\x80\xe0" + b"\x00" * 10]  # 12 bytes exactly, no payload

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            if items:
                return items.pop(0)
            server._running = False
            raise TimeoutError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._video_rtp_passthrough_loop()

        assert broadcasts == []

    @pytest.mark.asyncio
    async def test_caches_sps(self):
        """SPS NAL in payload (type 7) cached in _latest_sps."""
        server = LocalRtspServer()
        server._running = True
        server._broadcast_rtp = lambda *a, **kw: None  # type: ignore[method-assign]

        sps_payload = b"\x67" + b"\x42" * 8
        rtp = _make_rtp_pkt(payload=sps_payload)
        items: list[bytes] = [rtp]

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            if items:
                return items.pop(0)
            server._running = False
            raise TimeoutError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._video_rtp_passthrough_loop()

        assert server._latest_sps == sps_payload

    @pytest.mark.asyncio
    async def test_caches_pps(self):
        """PPS NAL in payload (type 8) cached in _latest_pps."""
        server = LocalRtspServer()
        server._running = True
        server._broadcast_rtp = lambda *a, **kw: None  # type: ignore[method-assign]

        pps_payload = b"\x68" + b"\xce" * 4
        rtp = _make_rtp_pkt(payload=pps_payload)
        items: list[bytes] = [rtp]

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            if items:
                return items.pop(0)
            server._running = False
            raise TimeoutError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._video_rtp_passthrough_loop()

        assert server._latest_pps == pps_payload

    @pytest.mark.asyncio
    async def test_timeout_triggers_fallback_after_three_misses(self):
        """Three consecutive timeouts with non-empty nal_queue triggers _drain_nal_queue_fallback."""
        server = LocalRtspServer()
        server._running = True
        server._broadcast_rtp = lambda *a, **kw: None  # type: ignore[method-assign]
        server.nal_queue.put_nowait((1000, b"\x65" + b"\xaa" * 10))

        fallback_called: list[bool] = []
        original_drain = server._drain_nal_queue_fallback

        async def patched_drain() -> None:
            fallback_called.append(True)
            await original_drain()

        server._drain_nal_queue_fallback = patched_drain  # type: ignore[method-assign]

        timeout_count = 0

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            nonlocal timeout_count
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            timeout_count += 1
            if timeout_count > 3:
                server._running = False
            raise TimeoutError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._video_rtp_passthrough_loop()

        assert fallback_called  # drain was triggered at timeout_count == 3

    @pytest.mark.asyncio
    async def test_fallback_count_resets_on_successful_get(self):
        """Successful RTP get resets fallback_count to 0."""
        server = LocalRtspServer()
        server._running = True
        server._broadcast_rtp = lambda *a, **kw: None  # type: ignore[method-assign]

        # First call: return valid RTP (resets fallback_count)
        # Subsequent calls: timeout → exit
        rtp = _make_rtp_pkt()
        items: list[bytes] = [rtp]
        call_count = 0

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            nonlocal call_count
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            call_count += 1
            if items:
                return items.pop(0)
            server._running = False
            raise TimeoutError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._video_rtp_passthrough_loop()

        assert call_count >= 2  # consumed packet + at least one timeout

    @pytest.mark.asyncio
    async def test_cancelled_error_exits_cleanly(self):
        server = LocalRtspServer()
        server._running = True

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            raise asyncio.CancelledError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._video_rtp_passthrough_loop()  # must not raise

    @pytest.mark.asyncio
    async def test_exception_caught(self):
        server = LocalRtspServer()
        server._running = True

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            server._running = False
            raise ValueError("unexpected")

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._video_rtp_passthrough_loop()  # must not raise

    @pytest.mark.asyncio
    async def test_rewrites_rtp_header_pt_and_seq(self):
        """RTP header is rewritten: PT=96, seq from server counter."""
        server = LocalRtspServer()
        server._running = True
        server._video_seq = 10
        broadcasts: list[bytes] = []
        server._broadcast_rtp = lambda pkt, is_video: broadcasts.append(pkt)  # type: ignore[method-assign]

        rtp = _make_rtp_pkt(payload=b"\x65" + b"\xaa" * 5)
        items: list[bytes] = [rtp]

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            if items:
                return items.pop(0)
            server._running = False
            raise TimeoutError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._video_rtp_passthrough_loop()

        assert len(broadcasts) == 1
        pkt = broadcasts[0]
        assert (pkt[1] & 0x7F) == 96  # PT forced to 96
        assert struct.unpack_from("!H", pkt, 2)[0] == 10  # seq = initial value
        assert server._video_seq == 11  # incremented


# ---------------------------------------------------------------------------
# B-1: _handle_client
# ---------------------------------------------------------------------------


class TestHandleClient:
    @pytest.mark.asyncio
    async def test_options_response(self):
        """OPTIONS returns 200 OK with allowed methods."""
        server = LocalRtspServer()
        server._running = True
        reader = _RequestReader(
            [
                b"OPTIONS rtsp://127.0.0.1/intercom RTSP/1.0\r\nCSeq: 1\r\n\r\n",
                b"",
            ]
        )
        writer = _ResponseWriter()
        await server._handle_client(reader, writer)
        assert b"RTSP/1.0 200 OK" in writer.data
        assert b"DESCRIBE" in writer.data

    @pytest.mark.asyncio
    async def test_describe_returns_sdp(self):
        """DESCRIBE returns 200 OK with SDP body."""
        server = LocalRtspServer()
        server._running = True
        reader = _RequestReader(
            [
                b"DESCRIBE rtsp://127.0.0.1/intercom RTSP/1.0\r\nCSeq: 2\r\n\r\n",
                b"",
            ]
        )
        writer = _ResponseWriter()
        await server._handle_client(reader, writer)
        assert b"Content-Type: application/sdp" in writer.data
        assert b"m=video" in writer.data

    @pytest.mark.asyncio
    async def test_setup_tcp_returns_session_header(self):
        """SETUP with TCP interleaved returns Session header."""
        server = LocalRtspServer()
        server._running = True
        reader = _RequestReader(
            [
                b"SETUP rtsp://127.0.0.1/intercom RTSP/1.0\r\n"
                b"CSeq: 3\r\n"
                b"Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n\r\n",
                b"",
            ]
        )
        writer = _ResponseWriter()
        await server._handle_client(reader, writer)
        assert b"Session:" in writer.data

    @pytest.mark.asyncio
    async def test_play_registers_client_and_returns_200(self):
        """PLAY registers client and returns 200 OK immediately (no ready_event gate)."""
        server = LocalRtspServer()
        server._running = True
        reader = _RequestReader(
            [
                b"SETUP rtsp://127.0.0.1/intercom RTSP/1.0\r\n"
                b"CSeq: 1\r\nTransport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n\r\n",
                b"PLAY rtsp://127.0.0.1/intercom RTSP/1.0\r\nCSeq: 2\r\n\r\n",
                b"",  # EOF in _wait_for_teardown
            ]
        )
        writer = _ResponseWriter()
        await server._handle_client(reader, writer)
        assert b"RTSP/1.0 200 OK" in writer.data
        assert len(server._active_clients) == 0  # removed in finally

    @pytest.mark.asyncio
    async def test_play_without_ready_event_still_returns_200(self):
        """PLAY returns 200 OK immediately even when ready_event is not set."""
        server = LocalRtspServer()
        server._running = True
        # ready_event NOT set — should no longer block or 503
        reader = _RequestReader(
            [
                b"PLAY rtsp://127.0.0.1/intercom RTSP/1.0\r\nCSeq: 1\r\n\r\n",
                b"",  # EOF in _wait_for_teardown
            ]
        )
        writer = _ResponseWriter()
        await server._handle_client(reader, writer)
        assert b"RTSP/1.0 200 OK" in writer.data
        assert b"503" not in writer.data

    @pytest.mark.asyncio
    async def test_teardown_in_main_loop_returns_200(self):
        """TEARDOWN before PLAY returns 200 OK from main loop."""
        server = LocalRtspServer()
        server._running = True
        reader = _RequestReader(
            [
                b"TEARDOWN rtsp://127.0.0.1/intercom RTSP/1.0\r\nCSeq: 5\r\n\r\n",
            ]
        )
        writer = _ResponseWriter()
        await server._handle_client(reader, writer)
        assert b"RTSP/1.0 200 OK" in writer.data
        assert b"Session:" in writer.data

    @pytest.mark.asyncio
    async def test_unknown_method_returns_405(self):
        """Unknown RTSP method returns 405 Method Not Allowed."""
        server = LocalRtspServer()
        server._running = True
        reader = _RequestReader(
            [
                b"FOOBAR rtsp://127.0.0.1/intercom RTSP/1.0\r\nCSeq: 1\r\n\r\n",
                b"",
            ]
        )
        writer = _ResponseWriter()
        await server._handle_client(reader, writer)
        assert b"405 Method Not Allowed" in writer.data

    @pytest.mark.asyncio
    async def test_empty_read_returns_cleanly(self):
        """Immediate EOF from reader exits cleanly."""
        server = LocalRtspServer()
        server._running = True
        reader = _RequestReader([b""])
        writer = _ResponseWriter()
        await server._handle_client(reader, writer)  # must not raise

    @pytest.mark.asyncio
    async def test_partial_then_eof_returns_cleanly(self):
        """Partial request followed by EOF exits via 'if not chunk: return'."""
        server = LocalRtspServer()
        server._running = True
        reader = _RequestReader(
            [
                b"OPTIONS rtsp://127.0.0.1",  # no \r\n\r\n yet
                b"",  # EOF mid-request
            ]
        )
        writer = _ResponseWriter()
        await server._handle_client(reader, writer)  # must not raise

    @pytest.mark.asyncio
    async def test_timeout_error_caught(self):
        """TimeoutError from reader.read is caught by except (TimeoutError, ConnectionError)."""
        server = LocalRtspServer()
        server._running = True

        class _TimeoutReader:
            async def read(self, n: int) -> bytes:
                raise TimeoutError("no data")

        writer = _ResponseWriter()
        await server._handle_client(_TimeoutReader(), writer)  # must not raise

    @pytest.mark.asyncio
    async def test_connection_error_caught(self):
        """ConnectionError from reader.read is caught by except (TimeoutError, ConnectionError)."""
        server = LocalRtspServer()
        server._running = True

        class _ConnErrorReader:
            async def read(self, n: int) -> bytes:
                raise ConnectionError("peer reset")

        writer = _ResponseWriter()
        await server._handle_client(_ConnErrorReader(), writer)  # must not raise

    @pytest.mark.asyncio
    async def test_general_exception_caught(self):
        """Unexpected exception from reader.read is caught by except Exception."""
        server = LocalRtspServer()
        server._running = True

        class _BrokenReader:
            async def read(self, n: int) -> bytes:
                raise RuntimeError("unexpected failure")

        writer = _ResponseWriter()
        await server._handle_client(_BrokenReader(), writer)  # must not raise

    @pytest.mark.asyncio
    async def test_peername_none_uses_unknown_host(self):
        """When peername is None, client_host is set to 'unknown'."""
        server = LocalRtspServer()
        server._running = True
        reader = _RequestReader([b""])
        writer = _ResponseWriter(peername=None)
        await server._handle_client(reader, writer)  # must not raise

    @pytest.mark.asyncio
    async def test_socket_not_none_sets_tcp_nodelay(self):
        """When socket info is available, TCP_NODELAY is set on it."""
        server = LocalRtspServer()
        server._running = True
        reader = _RequestReader([b""])
        mock_sock = MagicMock()
        writer = _ResponseWriter(socket=mock_sock)
        await server._handle_client(reader, writer)
        mock_sock.setsockopt.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_lines_break(self):
        """Request with only CRLF (no method line) causes 'if not lines: break'."""
        server = LocalRtspServer()
        server._running = True
        reader = _RequestReader([b"\r\n\r\n"])  # pure CRLF, no content
        writer = _ResponseWriter()
        await server._handle_client(reader, writer)  # must not raise

    @pytest.mark.asyncio
    async def test_short_request_line_break(self):
        """Request with < 2 parts on first line causes 'if len(parts) < 2: break'."""
        server = LocalRtspServer()
        server._running = True
        reader = _RequestReader([b"RTSP/1.0\r\n\r\n"])  # only one token
        writer = _ResponseWriter()
        await server._handle_client(reader, writer)  # must not raise

    @pytest.mark.asyncio
    async def test_full_options_describe_setup_play_teardown_flow(self):
        """Complete RTSP session: OPTIONS→DESCRIBE→SETUP→PLAY→TEARDOWN."""
        server = LocalRtspServer()
        server._running = True
        reader = _RequestReader(
            [
                b"OPTIONS rtsp://127.0.0.1/intercom RTSP/1.0\r\nCSeq: 1\r\n\r\n",
                b"DESCRIBE rtsp://127.0.0.1/intercom RTSP/1.0\r\nCSeq: 2\r\n\r\n",
                b"SETUP rtsp://127.0.0.1/intercom RTSP/1.0\r\n"
                b"CSeq: 3\r\nTransport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n\r\n",
                b"PLAY rtsp://127.0.0.1/intercom RTSP/1.0\r\nCSeq: 4\r\n\r\n",
                b"TEARDOWN /intercom RTSP/1.0\r\n",  # read by _wait_for_teardown
            ]
        )
        writer = _ResponseWriter()
        await server._handle_client(reader, writer)

        resp = writer.data
        assert resp.count(b"RTSP/1.0 200 OK") >= 4
        assert b"application/sdp" in resp  # DESCRIBE
        assert b"Session:" in resp  # SETUP/PLAY
        assert len(server._active_clients) == 0  # removed in finally


# ---------------------------------------------------------------------------
# Backchannel (ANNOUNCE / RECORD) tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# RFC 3550/3551 audio pause encoding (wall-clock ts advance + marker bit)
# ---------------------------------------------------------------------------


class TestAudioPauseEncoding:
    def _run_one_frame(self, server: LocalRtspServer, payload: bytes) -> list[bytes]:
        """Push one payload through _audio_feed_loop, return broadcast packets."""
        broadcasts: list[bytes] = []
        server._broadcast_rtp = lambda pkt, is_video: broadcasts.append(pkt)  # type: ignore[method-assign]
        items = [payload]

        async def mock_wait_for(coro: object, timeout: float) -> bytes:
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[attr-defined]
            if items:
                return items.pop(0)
            server._running = False
            raise TimeoutError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            asyncio.get_event_loop().run_until_complete(server._audio_feed_loop())
        return broadcasts

    @pytest.mark.asyncio
    async def test_gap_advances_ts_by_wall_clock_and_sets_marker(self):
        server = LocalRtspServer()
        server._running = True
        server._audio_ts = 1160  # would-be contiguous ts (stale)
        server._last_audio_send_ts = 1000
        server._last_audio_send_mono = time.monotonic() - 0.5  # 0.5s pause

        broadcasts: list[bytes] = []
        server._broadcast_rtp = lambda pkt, is_video: broadcasts.append(pkt)  # type: ignore[method-assign]
        items = [b"\xaa" * 160]

        async def mock_wait_for(coro, timeout):
            if asyncio.iscoroutine(coro):
                coro.close()
            if items:
                return items.pop(0)
            server._running = False
            raise TimeoutError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._audio_feed_loop()

        assert len(broadcasts) == 1
        pkt = broadcasts[0]
        assert pkt[1] & 0x80  # marker bit set on resume
        ts = struct.unpack_from("!I", pkt, 4)[0]
        # ~0.5s * 8000 = ~4000 ticks past the last sent packet's ts
        assert 1000 + 3800 <= ts <= 1000 + 4600

    @pytest.mark.asyncio
    async def test_small_jitter_keeps_smooth_cadence_no_marker(self):
        server = LocalRtspServer()
        server._running = True
        server._audio_ts = 1160
        server._last_audio_send_ts = 1000
        server._last_audio_send_mono = time.monotonic() - 0.03  # 30ms < threshold

        broadcasts: list[bytes] = []
        server._broadcast_rtp = lambda pkt, is_video: broadcasts.append(pkt)  # type: ignore[method-assign]
        items = [b"\xaa" * 160]

        async def mock_wait_for(coro, timeout):
            if asyncio.iscoroutine(coro):
                coro.close()
            if items:
                return items.pop(0)
            server._running = False
            raise TimeoutError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._audio_feed_loop()

        pkt = broadcasts[0]
        assert not (pkt[1] & 0x80)  # no marker
        assert struct.unpack_from("!I", pkt, 4)[0] == 1160  # contiguous ts kept

    @pytest.mark.asyncio
    async def test_first_packet_ever_has_no_marker(self):
        server = LocalRtspServer()
        server._running = True
        assert server._last_audio_send_mono is None

        broadcasts: list[bytes] = []
        server._broadcast_rtp = lambda pkt, is_video: broadcasts.append(pkt)  # type: ignore[method-assign]
        items = [b"\xaa" * 160]

        async def mock_wait_for(coro, timeout):
            if asyncio.iscoroutine(coro):
                coro.close()
            if items:
                return items.pop(0)
            server._running = False
            raise TimeoutError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._audio_feed_loop()

        assert not (broadcasts[0][1] & 0x80)
        assert server._last_audio_send_mono is not None  # bookkeeping recorded

    @pytest.mark.asyncio
    async def test_send_updates_bookkeeping(self):
        server = LocalRtspServer()
        server._running = True
        server._audio_ts = 5000

        server._broadcast_rtp = lambda pkt, is_video: None  # type: ignore[method-assign]
        items = [b"\xaa" * 160]

        async def mock_wait_for(coro, timeout):
            if asyncio.iscoroutine(coro):
                coro.close()
            if items:
                return items.pop(0)
            server._running = False
            raise TimeoutError

        with patch("custom_components.comelit_man.rtsp_server.asyncio.wait_for", mock_wait_for):
            await server._audio_feed_loop()

        assert server._last_audio_send_ts == 5000  # ts of the packet actually sent
        assert server._audio_ts == 5160  # advanced for the next contiguous frame


# ---------------------------------------------------------------------------
# Require-header backchannel negotiation (Phase 4)
# ---------------------------------------------------------------------------


class TestBackchannelNegotiation:
    def test_sdp_without_require_has_two_mlines(self):
        """Plain DESCRIBE: video + one audio m-line, no direction attributes."""
        server = LocalRtspServer()
        sdp = server._build_sdp()
        assert sdp.count("m=video") == 1
        assert sdp.count("m=audio") == 1
        assert "a=sendonly" not in sdp
        assert "a=recvonly" not in sdp
        assert "a=control:audio" in sdp
        assert "backchannel" not in sdp

    def test_sdp_with_backchannel_adds_sendonly_mline(self):
        """Require-header DESCRIBE: third m-line, sendonly, control:backchannel."""
        server = LocalRtspServer()
        sdp = server._build_sdp(backchannel=True)
        assert sdp.count("m=audio") == 2
        # sendonly appears exactly once, inside the backchannel m-line block
        assert sdp.count("a=sendonly") == 1
        backchannel_block = sdp.split("m=audio")[2]
        assert "a=sendonly" in backchannel_block
        assert "a=control:backchannel" in backchannel_block
        # the main audio m-line stays direction-free
        main_audio_block = sdp.split("m=audio")[1]
        assert "sendonly" not in main_audio_block
        assert "recvonly" not in main_audio_block

    @pytest.mark.asyncio
    async def test_describe_with_require_header_gates_sdp(self):
        server = LocalRtspServer()
        server._running = True
        req = (
            b"DESCRIBE rtsp://127.0.0.1/intercom RTSP/1.0\r\n"
            b"CSeq: 1\r\n"
            b"Require: www.onvif.org/ver20/backchannel\r\n\r\n"
        )
        reader = _RequestReader([req, b""])
        writer = _ResponseWriter()
        await server._handle_client(reader, writer)
        assert b"a=control:backchannel" in writer.data

    @pytest.mark.asyncio
    async def test_describe_without_require_header_omits_backchannel(self):
        server = LocalRtspServer()
        server._running = True
        req = b"DESCRIBE rtsp://127.0.0.1/intercom RTSP/1.0\r\nCSeq: 1\r\n\r\n"
        reader = _RequestReader([req, b""])
        writer = _ResponseWriter()
        await server._handle_client(reader, writer)
        assert b"backchannel" not in writer.data

    def test_setup_backchannel_url_routes_to_backchannel_ch(self):
        server = LocalRtspServer()
        client = _TcpClient(writer=MagicMock())
        resp = server._parse_setup(
            "RTP/AVP/TCP;unicast;interleaved=4-5",
            False,
            client,
            "127.0.0.1",
            is_backchannel=True,
        )
        assert client.backchannel_ch == 4
        assert client.audio_ch is None
        assert client.video_ch is None
        assert "interleaved=4-5" in resp

    @pytest.mark.asyncio
    async def test_announce_now_rejected(self):
        """ANNOUNCE is no longer supported — 405."""
        server = LocalRtspServer()
        server._running = True
        req = b"ANNOUNCE rtsp://127.0.0.1/intercom RTSP/1.0\r\nCSeq: 1\r\nContent-Length: 0\r\n\r\n"
        reader = _RequestReader([req, b""])
        writer = _ResponseWriter()
        await server._handle_client(reader, writer)
        assert b"405 Method Not Allowed" in writer.data

    @pytest.mark.asyncio
    async def test_options_no_longer_advertises_announce_record(self):
        server = LocalRtspServer()
        server._running = True
        req = b"OPTIONS rtsp://127.0.0.1/intercom RTSP/1.0\r\nCSeq: 1\r\n\r\n"
        reader = _RequestReader([req, b""])
        writer = _ResponseWriter()
        await server._handle_client(reader, writer)
        assert b"ANNOUNCE" not in writer.data
        assert b"RECORD" not in writer.data
        assert b"PLAY" in writer.data


# ---------------------------------------------------------------------------
# _read_client_stream + _on_backchannel_rtp (Phase 4)
# ---------------------------------------------------------------------------


def _rtp(pt: int = 8, payload: bytes = b"\xaa" * 160, cc: int = 0, ext: bool = False) -> bytes:
    b0 = 0x80 | (0x10 if ext else 0) | cc
    hdr = struct.pack("!BBHII", b0, pt, 1, 160, 0xDEADBEEF)
    hdr += b"\x00\x00\x00\x00" * cc
    if ext:
        hdr += struct.pack("!HH", 0xBEDE, 1) + b"\x00\x00\x00\x00"
    return hdr + payload


def _interleave(ch: int, frame: bytes) -> bytes:
    return struct.pack("!BBH", 0x24, ch, len(frame)) + frame


class TestReadClientStream:
    class _ChunkReader:
        def __init__(self, chunks: list[bytes]) -> None:
            self._chunks = list(chunks)

        async def read(self, n: int) -> bytes:
            if self._chunks:
                return self._chunks.pop(0)
            return b""

    @pytest.mark.asyncio
    async def test_backchannel_frame_lands_in_queue(self):
        server = LocalRtspServer()
        server._running = True
        client = _TcpClient(writer=MagicMock(), backchannel_ch=4)
        reader = self._ChunkReader([_interleave(4, _rtp())])
        await server._read_client_stream(reader, client)
        assert server.backchannel_queue.get_nowait() == b"\xaa" * 160
        assert server.backchannel_rx_count == 1

    @pytest.mark.asyncio
    async def test_other_channel_ignored(self):
        server = LocalRtspServer()
        server._running = True
        client = _TcpClient(writer=MagicMock(), backchannel_ch=4)
        reader = self._ChunkReader([_interleave(2, _rtp())])
        await server._read_client_stream(reader, client)
        assert server.backchannel_queue.empty()

    @pytest.mark.asyncio
    async def test_no_backchannel_setup_ignores_frames(self):
        server = LocalRtspServer()
        server._running = True
        client = _TcpClient(writer=MagicMock())  # backchannel_ch=None
        reader = self._ChunkReader([_interleave(0, _rtp())])
        await server._read_client_stream(reader, client)
        assert server.backchannel_queue.empty()

    @pytest.mark.asyncio
    async def test_teardown_text_ends_stream(self):
        server = LocalRtspServer()
        server._running = True
        client = _TcpClient(writer=MagicMock(), backchannel_ch=4)
        reader = self._ChunkReader([b"TEARDOWN rtsp://x RTSP/1.0\r\n", _interleave(4, _rtp())])
        await server._read_client_stream(reader, client)
        assert server.backchannel_queue.empty()  # returned before the frame

    @pytest.mark.asyncio
    async def test_split_frame_across_reads_reassembled(self):
        server = LocalRtspServer()
        server._running = True
        client = _TcpClient(writer=MagicMock(), backchannel_ch=4)
        frame = _interleave(4, _rtp())
        reader = self._ChunkReader([frame[:10], frame[10:]])
        await server._read_client_stream(reader, client)
        assert server.backchannel_queue.get_nowait() == b"\xaa" * 160


class TestOnBackchannelRtp:
    def test_pcma_payload_queued(self):
        server = LocalRtspServer()
        server._on_backchannel_rtp(_rtp(pt=8))
        assert server.backchannel_queue.get_nowait() == b"\xaa" * 160

    def test_pcmu_payload_queued(self):
        server = LocalRtspServer()
        server._on_backchannel_rtp(_rtp(pt=0))
        assert not server.backchannel_queue.empty()

    def test_non_g711_rejected(self):
        server = LocalRtspServer()
        server._on_backchannel_rtp(_rtp(pt=96))
        assert server.backchannel_queue.empty()

    def test_short_packet_rejected(self):
        server = LocalRtspServer()
        server._on_backchannel_rtp(b"\x80\x08\x00")
        assert server.backchannel_queue.empty()

    def test_csrc_skipped(self):
        server = LocalRtspServer()
        server._on_backchannel_rtp(_rtp(pt=8, cc=2))
        assert server.backchannel_queue.get_nowait() == b"\xaa" * 160

    def test_extension_header_skipped(self):
        server = LocalRtspServer()
        server._on_backchannel_rtp(_rtp(pt=8, ext=True))
        assert server.backchannel_queue.get_nowait() == b"\xaa" * 160

    def test_empty_payload_rejected(self):
        server = LocalRtspServer()
        server._on_backchannel_rtp(_rtp(pt=8, payload=b""))
        assert server.backchannel_queue.empty()

    def test_queue_full_drops_oldest(self):
        server = LocalRtspServer()
        server.backchannel_queue = asyncio.Queue(maxsize=2)
        server._on_backchannel_rtp(_rtp(payload=b"\x01" * 160))
        server._on_backchannel_rtp(_rtp(payload=b"\x02" * 160))
        server._on_backchannel_rtp(_rtp(payload=b"\x03" * 160))
        assert server.backchannel_queue.get_nowait() == b"\x02" * 160
        assert server.backchannel_queue.get_nowait() == b"\x03" * 160


class TestBackchannelPcmuConversion:
    def test_pcmu_payload_converted_to_alaw(self):
        from custom_components.comelit_man.rtsp_server import _ULAW_TO_ALAW

        server = LocalRtspServer()
        server._on_backchannel_rtp(_rtp(pt=0, payload=b"\xff" * 160))  # µ-law silence
        assert server.backchannel_queue.get_nowait() == b"\xd5" * 160  # A-law silence
        assert _ULAW_TO_ALAW[0xFF] == 0xD5

    def test_pcma_payload_passthrough(self):
        server = LocalRtspServer()
        server._on_backchannel_rtp(_rtp(pt=8, payload=b"\xd5" * 160))
        assert server.backchannel_queue.get_nowait() == b"\xd5" * 160
