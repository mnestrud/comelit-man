"""Targeted coverage for paths not exercised by module-specific test files.

Covers uncovered lines in client.py, protocol.py, token.py,
rtp_receiver.py, and video_call.py.
"""

from __future__ import annotations

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.comelit_man.channels import Channel, ChannelType
from custom_components.comelit_man.client import IconaBridgeClient
from custom_components.comelit_man.exceptions import ConnectionComelitError, ProtocolError
from custom_components.comelit_man.protocol import (
    HEADER_SIZE,
    MessageType,
    decode_rtp_header,
    encode_door_open_during_video,
    encode_header,
)

# ---------------------------------------------------------------------------
# Helpers shared across sections
# ---------------------------------------------------------------------------


def _raises_timeout(*args: object, **kwargs: object) -> None:
    """Raise TimeoutError after closing any coroutine arguments.

    Use as side_effect when patching asyncio.wait_for to avoid
    RuntimeWarning: coroutine '...' was never awaited.
    """
    for arg in args:
        if asyncio.iscoroutine(arg):
            arg.close()
    raise TimeoutError


class _FakeWriter:
    def __init__(self):
        self.data = bytearray()

    def write(self, b: bytes) -> None:
        self.data.extend(b)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass

    def transport(self):
        return MagicMock()


def _connected_client() -> IconaBridgeClient:
    """Return a client that appears connected (no real socket)."""
    client = IconaBridgeClient("127.0.0.1", 64100)
    client._writer = _FakeWriter()
    client._reader = MagicMock()
    client._connected = True
    return client


def _open_channel(client: IconaBridgeClient, name: str = "TEST", ch_id: int = 42) -> Channel:
    ch = Channel(name=name, channel_type=ChannelType.UAUT, request_id=1)
    ch.server_channel_id = ch_id
    ch.is_open = True
    client._channels[name] = ch
    return ch


# ---------------------------------------------------------------------------
# protocol.py — encode_door_open_during_video, decode_rtp_header
# ---------------------------------------------------------------------------


class TestEncodeDoorDuringVideo:
    def test_returns_bytes(self):
        result = encode_door_open_during_video(
            our_addr="SB000006",
            entrance_addr="SB100001",
            call_counter=0x00010101,
            relay_index=1,
        )
        assert isinstance(result, bytes)

    def test_starts_with_1840(self):
        result = encode_door_open_during_video("SB000006", "SB100001", 0, 1)
        msg_type = struct.unpack_from("<H", result, 0)[0]
        assert msg_type == 0x1840

    def test_relay_index_encoded(self):
        result = encode_door_open_during_video("SB000006", "SB100001", 0, 5)
        # buf layout: 0x1840(2) + counter(4) + ACTION(2) + 0x002D(2) + entr_b(10) = 20
        relay = struct.unpack_from("<I", result, 20)[0]
        assert relay == 5

    def test_addresses_present_in_payload(self):
        result = encode_door_open_during_video("SB000006", "SB100001", 0, 1)
        assert b"SB000006" in result
        assert b"SB100001" in result


class TestDecodeRtpHeader:
    def _make_packet(self, payload_type: int = 96, seq: int = 1, ts: int = 0, ssrc: int = 0) -> bytes:
        icona = b"\x00\x06" + b"\x00" * 6  # 8-byte ICONA header
        rtp = bytes(
            [
                0x80,  # V=2, P=0, X=0, CC=0
                payload_type & 0x7F,
                (seq >> 8) & 0xFF,
                seq & 0xFF,
                (ts >> 24) & 0xFF,
                (ts >> 16) & 0xFF,
                (ts >> 8) & 0xFF,
                ts & 0xFF,
                (ssrc >> 24) & 0xFF,
                (ssrc >> 16) & 0xFF,
                (ssrc >> 8) & 0xFF,
                ssrc & 0xFF,
            ]
        )
        return icona + rtp + b"\x00" * 4  # minimal payload

    def test_parses_version(self):
        pkt = self._make_packet()
        hdr, _ = decode_rtp_header(pkt)
        assert hdr.version == 2

    def test_parses_payload_type(self):
        pkt = self._make_packet(payload_type=96)
        hdr, _ = decode_rtp_header(pkt)
        assert hdr.payload_type == 96

    def test_parses_sequence(self):
        pkt = self._make_packet(seq=123)
        hdr, _ = decode_rtp_header(pkt)
        assert hdr.sequence == 123

    def test_returns_payload(self):
        pkt = self._make_packet()
        _, payload = decode_rtp_header(pkt)
        assert isinstance(payload, bytes)

    def test_returns_payload_excluding_csrc_extension_and_padding(self):
        icona = b"\x00\x06" + b"\x00" * 6
        fixed_header = struct.pack("!BBHII", 0xB1, 96, 1, 2, 3)
        csrc = struct.pack("!I", 4)
        extension = struct.pack("!HH", 0xBEDE, 1) + b"\x10\x20\x30\x40"
        media_payload = b"\x65\x01\x02"
        padding = b"\x00\x00\x00\x04"

        header, payload = decode_rtp_header(icona + fixed_header + csrc + extension + media_payload + padding)

        assert header.padding is True
        assert header.extension is True
        assert header.csrc_count == 1
        assert payload == media_payload

    def test_malformed_extension_raises(self):
        icona = b"\x00\x06" + b"\x00" * 6
        fixed_header = struct.pack("!BBHII", 0x90, 96, 1, 2, 3)
        truncated_extension = struct.pack("!HH", 0xBEDE, 2) + b"\x10\x20\x30\x40"

        with pytest.raises(ValueError, match="Malformed RTP"):
            decode_rtp_header(icona + fixed_header + truncated_extension)

    def test_non_v2_packet_raises(self):
        icona = b"\x00\x06" + b"\x00" * 6
        fixed_header = struct.pack("!BBHII", 0x40, 96, 1, 2, 3)

        with pytest.raises(ValueError, match="Malformed RTP"):
            decode_rtp_header(icona + fixed_header)

    @pytest.mark.parametrize("padding", [b"\x65\x00", b"\x65\x03"])
    def test_malformed_padding_raises(self, padding: bytes):
        icona = b"\x00\x06" + b"\x00" * 6
        fixed_header = struct.pack("!BBHII", 0xA0, 96, 1, 2, 3)

        with pytest.raises(ValueError, match="Malformed RTP"):
            decode_rtp_header(icona + fixed_header + padding)

    def test_too_short_raises(self):
        with pytest.raises(ValueError, match="too short"):
            decode_rtp_header(b"\x00" * 5)


# ---------------------------------------------------------------------------
# token.py — hass=None path (standalone aiohttp session)
# ---------------------------------------------------------------------------


class TestExtractTokenNoHass:
    @pytest.mark.asyncio
    async def test_hass_none_uses_standalone_session(self):
        from custom_components.comelit_man.token import extract_token

        mock_session = AsyncMock()
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("custom_components.comelit_man.token.aiohttp.ClientSession", return_value=mock_cm),
            patch(
                "custom_components.comelit_man.token._do_extract",
                new_callable=AsyncMock,
                return_value="deadbeef" * 4,
            ) as mock_do,
        ):
            result = await extract_token("192.168.1.1", "comelit", 8080, hass=None)

        assert result == "deadbeef" * 4
        mock_do.assert_awaited_once()


# ---------------------------------------------------------------------------
# client.py — uncovered error paths and utility methods
# ---------------------------------------------------------------------------


class TestConnectedProperty:
    def test_false_on_fresh_client(self):
        client = IconaBridgeClient("127.0.0.1", 64100)
        assert client.connected is False


class TestConnectErrors:
    @pytest.mark.asyncio
    async def test_os_error_raises_connection_error(self):
        client = IconaBridgeClient("127.0.0.1", 64100)
        with (
            patch(
                "custom_components.comelit_man.client.asyncio.open_connection",
                side_effect=OSError("connection refused"),
            ),
            pytest.raises(ConnectionComelitError),
        ):
            await client.connect()

    @pytest.mark.asyncio
    async def test_timeout_raises_connection_error(self):
        client = IconaBridgeClient("127.0.0.1", 64100)
        with (
            patch(
                "custom_components.comelit_man.client.asyncio.wait_for",
                side_effect=_raises_timeout,
            ),
            pytest.raises(ConnectionComelitError),
        ):
            await client.connect()


class TestSendNotConnected:
    @pytest.mark.asyncio
    async def test_raises_when_writer_missing(self):
        client = IconaBridgeClient("127.0.0.1", 64100)
        with pytest.raises(ConnectionComelitError, match="Not connected"):
            await client._send(b"hello")


class TestReadPacketNotConnected:
    @pytest.mark.asyncio
    async def test_raises_when_reader_missing(self):
        client = IconaBridgeClient("127.0.0.1", 64100)
        with pytest.raises(ConnectionComelitError, match="Not connected"):
            await client._read_packet()


class TestSetDisconnectCallback:
    def test_stores_callback(self):
        client = IconaBridgeClient("127.0.0.1", 64100)
        cb = MagicMock()
        client.set_disconnect_callback(cb)
        assert client._disconnect_callback is cb


class TestReceiveLoopErrors:
    @pytest.mark.asyncio
    async def test_timeout_marks_disconnected_and_fires_callback(self):
        client = _connected_client()
        fired = []
        client.set_disconnect_callback(lambda: fired.append(True))

        # _read_packet() raises TimeoutError before asyncio.wait_for is invoked;
        # the receive loop's except TimeoutError: branch fires and breaks the loop.
        with patch.object(client, "_read_packet", side_effect=TimeoutError):
            await client._receive_loop()

        assert not client.connected
        assert fired

    @pytest.mark.asyncio
    async def test_incomplete_read_marks_disconnected_and_fires_callback(self):
        client = _connected_client()
        fired = []
        client.set_disconnect_callback(lambda: fired.append(True))

        async def raise_incomplete(*_a, **_kw):
            raise asyncio.IncompleteReadError(b"", 8)

        with patch.object(client, "_read_packet", raise_incomplete):
            await client._receive_loop()

        assert not client.connected
        assert fired

    @pytest.mark.asyncio
    async def test_unexpected_exception_marks_disconnected_and_fires_callback(self):
        client = _connected_client()
        fired = []
        client.set_disconnect_callback(lambda: fired.append(True))

        async def raise_runtimeerror(*_a, **_kw):
            raise RuntimeError("surprise")

        with patch.object(client, "_read_packet", raise_runtimeerror):
            await client._receive_loop()

        assert not client.connected
        assert fired

    @pytest.mark.asyncio
    async def test_no_callback_on_cancelled_error(self):
        client = _connected_client()
        fired = []
        client.set_disconnect_callback(lambda: fired.append(True))

        async def raise_cancelled(*_a, **_kw):
            raise asyncio.CancelledError

        with patch.object(client, "_read_packet", raise_cancelled), pytest.raises(asyncio.CancelledError):
            await client._receive_loop()

        assert not fired


class TestDisconnectPendingCallbacks:
    @pytest.mark.asyncio
    async def test_pending_futures_are_cancelled(self):
        client = _connected_client()
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[bytes] = loop.create_future()
        client._callbacks[99] = fut

        await client.disconnect()

        assert fut.cancelled()

    @pytest.mark.asyncio
    async def test_already_done_future_not_re_cancelled(self):
        client = _connected_client()
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[bytes] = loop.create_future()
        fut.set_result(b"done")
        client._callbacks[99] = fut

        await client.disconnect()  # should not raise


class TestSendJsonErrors:
    @pytest.mark.asyncio
    async def test_timeout_raises_protocol_error(self):
        client = _connected_client()
        ch = _open_channel(client)

        with (
            patch(
                "custom_components.comelit_man.client.asyncio.wait_for",
                side_effect=TimeoutError,
            ),
            pytest.raises(ProtocolError),
        ):
            await client.send_json(ch, {"cmd": "test"})

    @pytest.mark.asyncio
    async def test_channel_not_open_raises_protocol_error(self):
        client = _connected_client()
        ch = Channel(name="CLOSED", channel_type=ChannelType.UAUT, request_id=1)
        ch.server_channel_id = 0
        ch.is_open = False

        with pytest.raises(ProtocolError):
            await client.send_json(ch, {})


class TestSendBinaryErrors:
    @pytest.mark.asyncio
    async def test_closed_channel_raises_protocol_error(self):
        client = _connected_client()
        ch = Channel(name="CLOSED", channel_type=ChannelType.UAUT, request_id=1)
        ch.is_open = False

        with pytest.raises(ProtocolError):
            await client.send_binary(ch, b"\x00" * 4)


class TestOpenChannelTimeout:
    @pytest.mark.asyncio
    async def test_timeout_raises_protocol_error(self):
        client = _connected_client()

        with (
            patch(
                "custom_components.comelit_man.client.asyncio.wait_for",
                side_effect=_raises_timeout,
            ),
            pytest.raises(ProtocolError, match="Timeout"),
        ):
            await client.open_channel("CH", ChannelType.UAUT)


class TestCloseChannel:
    @pytest.mark.asyncio
    async def test_close_existing_channel_sends_end_packet(self):
        client = _connected_client()
        ch = _open_channel(client, "CLOSE_ME", ch_id=7)

        await client.close_channel("CLOSE_ME")

        assert "CLOSE_ME" not in client._channels
        assert len(client._writer.data) > 0  # END packet was written

    @pytest.mark.asyncio
    async def test_close_missing_channel_is_noop(self):
        client = _connected_client()
        await client.close_channel("DOES_NOT_EXIST")  # must not raise


class TestRenameChannel:
    def test_renames_existing_channel(self):
        client = IconaBridgeClient("127.0.0.1", 64100)
        ch = Channel(name="OLD", channel_type=ChannelType.UAUT, request_id=1)
        ch.is_open = True
        ch.server_channel_id = 5
        client._channels["OLD"] = ch

        client.rename_channel("OLD", "NEW")

        assert "OLD" not in client._channels
        assert "NEW" in client._channels
        assert client._channels["NEW"].name == "NEW"

    def test_rename_missing_channel_is_noop(self):
        client = IconaBridgeClient("127.0.0.1", 64100)
        client.rename_channel("MISSING", "NEW")  # must not raise
        assert "NEW" not in client._channels


class TestReleasePlaceholder:
    def test_removes_placeholder(self):
        client = IconaBridgeClient("127.0.0.1", 64100)
        client.register_placeholder_channel("PLACEHOLDER")
        assert "PLACEHOLDER" in client._channels

        client.release_placeholder_channel("PLACEHOLDER")
        assert "PLACEHOLDER" not in client._channels

    def test_missing_placeholder_is_noop(self):
        client = IconaBridgeClient("127.0.0.1", 64100)
        client.release_placeholder_channel("MISSING")  # must not raise


class TestDispatchEdgeCases:
    def test_device_close_sub_type_not_2_logs_ack(self):
        """Device ACKed our close (sub_type != 2) — lines 282-285."""
        client = _connected_client()
        body = bytearray(10)
        struct.pack_into("<H", body, 0, 0x01EF)  # END magic
        struct.pack_into("<H", body, 2, 3)  # seq=3
        struct.pack_into("<I", body, 4, 4)  # sub_type=4 (ACK, not close-request)
        struct.pack_into("<H", body, 8, 42)  # ch_id
        pkt = encode_header(len(body), 0) + bytes(body)
        header = pkt[:HEADER_SIZE]
        assert struct.unpack_from("<H", header, 4)[0] == 0  # request_id=0
        client._dispatch(0, bytes(body))  # should not raise

    def test_non_command_message_type_logs(self):
        """Non-COMMAND, non-END message type dispatched to channel 0 — line 286-289."""
        client = _connected_client()
        body = bytearray(10)
        struct.pack_into("<H", body, 0, 0x0001)  # unknown type
        struct.pack_into("<H", body, 2, 2)
        client._dispatch(0, bytes(body))  # should not raise

    def test_push_callback_decode_failure_logged(self):
        """Unsolicited binary-looking JSON that fails decode — lines 317-322."""
        client = _connected_client()
        client._push_callback = MagicMock()

        bad_json = b"{invalid json}"
        client._dispatch(999, bad_json)  # non-zero request_id, looks JSON-ish
        # push_callback should not have been called with decoded dict on bad JSON

    def test_unsolicited_binary_no_channel_match(self):
        """Binary data with no matching channel — line 322."""
        client = _connected_client()
        binary_data = b"\x00\x01\x02\x03"  # not JSON
        client._dispatch(999, binary_data)  # should not raise

    def test_binary_response_queued_on_channel(self):
        """Binary response queued on matching channel — line 307 (binary branch)."""
        client = _connected_client()
        ch = _open_channel(client, "CH", ch_id=10)
        binary_body = b"\x80\x60\x00\x00"
        client._dispatch(10, binary_body)
        assert not ch.response_queue.empty()

    def test_json_response_queued_on_channel(self):
        """JSON response queued on matching channel — line 305 (JSON branch)."""
        import json

        client = _connected_client()
        ch = _open_channel(client, "CH", ch_id=55)
        json_body = json.dumps({"response-code": 200}).encode()
        client._dispatch(55, json_body)
        assert not ch.response_queue.empty()

    def test_device_initiated_open_parse_error_fallback(self):
        """No null terminator in device-initiated open body — line 215."""
        client = _connected_client()
        # Build a COMMAND response body with seq=1 (device-initiated) but no null byte
        body = bytearray()
        body += struct.pack("<H", MessageType.COMMAND)
        body += struct.pack("<H", 1)  # seq=1 → device-initiated
        body += struct.pack("<I", 0)  # type
        body += b"NOCTPP"  # channel name (no null terminator)
        body += b"\x00\x00\x42\x00"  # dev_req_id at end-3
        client._dispatch(0, bytes(body))  # should not raise


class TestSendJsonBinaryResponse:
    """send_json raises ProtocolError when response is not JSON — line 426."""

    @pytest.mark.asyncio
    async def test_binary_response_raises_protocol_error(self):
        """Inject a binary response via _dispatch so the real future resolves."""
        client = _connected_client()
        ch = _open_channel(client, "CH", ch_id=77)

        async def inject_binary_response() -> None:
            # Yield once so send_json can register its callback future first.
            await asyncio.sleep(0)
            # Dispatch binary data on channel 77 — resolves the callback future.
            client._dispatch(77, b"\x00\x01\x02\x03")

        asyncio.create_task(inject_binary_response())
        with pytest.raises(ProtocolError, match="Expected JSON"):
            await client.send_json(ch, {"cmd": "test"})


# ---------------------------------------------------------------------------
# rtp_receiver.py — _UdpProtocol protocol methods
# ---------------------------------------------------------------------------


class TestUdpProtocol:
    def _make(self):
        from custom_components.comelit_man.rtp_receiver import RtpReceiver, _UdpProtocol

        receiver = MagicMock(spec=RtpReceiver)
        return _UdpProtocol(receiver), receiver

    def test_init_stores_receiver(self):
        from custom_components.comelit_man.rtp_receiver import RtpReceiver, _UdpProtocol

        receiver = MagicMock(spec=RtpReceiver)
        proto = _UdpProtocol(receiver)
        assert proto._receiver is receiver

    def test_connection_made_does_not_raise(self):
        proto, _ = self._make()
        transport = MagicMock()
        transport.get_extra_info.return_value = ("127.0.0.1", 5000)
        proto.connection_made(transport)

    def test_datagram_received_calls_on_udp_packet(self):
        proto, receiver = self._make()
        data = b"\x80\x60" + b"\x00" * 10
        proto.datagram_received(data, ("127.0.0.1", 5000))
        receiver._on_udp_packet.assert_called_once_with(data)

    def test_error_received_does_not_raise(self):
        proto, _ = self._make()
        proto.error_received(OSError("udp error"))

    def test_connection_lost_with_exc_does_not_raise(self):
        proto, _ = self._make()
        proto.connection_lost(OSError("lost"))

    def test_connection_lost_none_does_not_raise(self):
        proto, _ = self._make()
        proto.connection_lost(None)


# ---------------------------------------------------------------------------
# video_call.py — rtp_receiver and rtsp_server properties
# ---------------------------------------------------------------------------


class TestVideoCallProperties:
    def _make_session(self):
        from custom_components.comelit_man.models import DeviceConfig
        from custom_components.comelit_man.video_call import VideoCallSession

        client = MagicMock()
        config = MagicMock(spec=DeviceConfig)
        config.apt_address = "SB000006"
        config.apt_subaddress = 1
        config.doors = []
        config.cameras = []
        return VideoCallSession(
            client,
            config,
            rtsp_server=None,
            on_call_end=None,
            on_timeout=None,
        )

    def test_rtp_receiver_is_none_before_start(self):
        session = self._make_session()
        assert session.rtp_receiver is None

    def test_rtsp_server_is_none_when_not_provided(self):
        session = self._make_session()
        assert session.rtsp_server is None

    def test_rtsp_server_returned_when_provided(self):
        from custom_components.comelit_man.models import DeviceConfig
        from custom_components.comelit_man.video_call import VideoCallSession

        client = MagicMock()
        config = MagicMock(spec=DeviceConfig)
        config.apt_address = "SB000006"
        config.apt_subaddress = 1
        config.doors = []
        config.cameras = []
        rtsp = MagicMock()
        session = VideoCallSession(client, config, rtsp_server=rtsp, on_call_end=None, on_timeout=None)
        assert session.rtsp_server is rtsp
