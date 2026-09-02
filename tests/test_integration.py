"""Real device integration tests.

Run with: COMELIT_HOST=192.168.1.XX COMELIT_TOKEN=<token> pytest tests/test_integration.py -v -s
Set COMELIT_PASSWORD to auto-extract token via HTTP backup.

Video/door tests require:
  1. The intercom screen to be awake (tap it — WiFi drops when idle).
  2. The HA comelit_man integration to be STOPPED or DISABLED. The device
     only accepts one CTPP session at a time; if HA is running with the
     integration active, it holds CTPP and the video tests will fail with
     ConnectionResetError during call initiation.
"""

import asyncio
import contextlib
import os
import struct
import time

import pytest

COMELIT_HOST = os.environ.get("COMELIT_HOST")
COMELIT_TOKEN = os.environ.get("COMELIT_TOKEN")
COMELIT_PASSWORD = os.environ.get("COMELIT_PASSWORD", "comelit")

pytestmark = pytest.mark.skipif(not COMELIT_HOST, reason="COMELIT_HOST not set (real device required)")


@pytest.mark.asyncio
async def test_extract_token():
    """Extract token from device backup.

    Useful for initial setup when no token is known yet. If COMELIT_TOKEN
    is already set this test is skipped — the token is proven valid by the
    other tests that use it directly.
    """
    if COMELIT_TOKEN:
        pytest.skip("COMELIT_TOKEN already set — extraction not needed")
    pytest.importorskip("aiohttp")
    from custom_components.comelit_man.token import extract_token

    token = await extract_token(COMELIT_HOST, password=COMELIT_PASSWORD)
    assert len(token) == 32
    assert all(c in "0123456789abcdef" for c in token)
    print(f"Extracted token: {token}")


@pytest.mark.asyncio
async def test_connect_and_authenticate():
    """Connect and authenticate with the device."""
    if not COMELIT_TOKEN:
        pytest.skip("COMELIT_TOKEN not set")

    from custom_components.comelit_man.auth import authenticate
    from custom_components.comelit_man.client import IconaBridgeClient

    client = IconaBridgeClient(COMELIT_HOST)
    await client.connect()
    try:
        await authenticate(client, COMELIT_TOKEN)
        assert client.connected
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_get_config():
    """Fetch device configuration."""
    if not COMELIT_TOKEN:
        pytest.skip("COMELIT_TOKEN not set")

    from custom_components.comelit_man.auth import authenticate
    from custom_components.comelit_man.client import IconaBridgeClient
    from custom_components.comelit_man.config_reader import get_device_config

    client = IconaBridgeClient(COMELIT_HOST)
    await client.connect()
    try:
        await authenticate(client, COMELIT_TOKEN)
        config = await get_device_config(client)
        print(f"Apt address: {config.apt_address}")
        print(f"Doors: {[d.name for d in config.doors]}")
        print(f"Cameras: {[c.name for c in config.cameras]}")
        assert config.apt_address
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_open_door():
    """Open the first door (CAREFUL: this actually opens a door!)."""
    if not COMELIT_TOKEN:
        pytest.skip("COMELIT_TOKEN not set")

    from custom_components.comelit_man.auth import authenticate
    from custom_components.comelit_man.client import IconaBridgeClient
    from custom_components.comelit_man.config_reader import get_device_config
    from custom_components.comelit_man.door import open_door
    from custom_components.comelit_man.protocol import ICONA_BRIDGE_PORT

    client = IconaBridgeClient(COMELIT_HOST)
    await client.connect()
    try:
        await authenticate(client, COMELIT_TOKEN)
        config = await get_device_config(client)
        assert config.doors, "No doors found in config"
        door = config.doors[0]
        print(f"Opening door: {door.name}")
        await open_door(COMELIT_HOST, ICONA_BRIDGE_PORT, COMELIT_TOKEN, client, config, door)
        print(f"Device ACKed relay open — {door.name} triggered")
    finally:
        await client.disconnect()


# ---------------------------------------------------------------------------
# CTPP setup helper — mirrors coordinator._open_ctpp_channels
# ---------------------------------------------------------------------------


async def _setup_ctpp(client, config) -> int:
    """Open CTPP+CSPB channels and run the init handshake.

    The device requires this registration before it will accept a video call
    initiation. In normal HA operation the coordinator does this at setup via
    the VIP listener; integration tests must replicate it before calling
    VideoCallSession.start().

    Returns the init_ts used in the handshake (needed for subsequent ACKs).
    """
    from custom_components.comelit_man.channels import ChannelType
    from custom_components.comelit_man.ctpp import ctpp_init_sequence

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
    return ts


# ---------------------------------------------------------------------------
# RTSP client helpers (used by video integration tests)
# ---------------------------------------------------------------------------


async def _read_rtsp_response(reader: asyncio.StreamReader) -> bytes:
    """Read one complete RTSP response (headers + body) from the stream."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = await asyncio.wait_for(reader.read(4096), timeout=15.0)
        if not chunk:
            break
        data += chunk
    # Read body if Content-Length is present
    for line in data.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            body_len = int(line.split(b":", 1)[1].strip())
            header_end = data.index(b"\r\n\r\n") + 4
            remaining = body_len - (len(data) - header_end)
            while remaining > 0:
                chunk = await asyncio.wait_for(reader.read(remaining), timeout=15.0)
                if not chunk:
                    break
                data += chunk
                remaining -= len(chunk)
            break
    return data


async def _read_interleaved_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Read one interleaved RTSP/RTP frame: $ channel(1) len(2) data(len).

    Skips bytes until the $ magic is found (handles partial RTSP response
    trailing data that may precede the first RTP frame).
    """
    while True:
        b = await asyncio.wait_for(reader.read(1), timeout=10.0)
        if b == b"$":
            break
        if not b:
            raise EOFError("Stream closed before interleaved frame")
    channel = struct.unpack("B", await asyncio.wait_for(reader.readexactly(1), timeout=5.0))[0]
    length = struct.unpack("!H", await asyncio.wait_for(reader.readexactly(2), timeout=5.0))[0]
    payload = await asyncio.wait_for(reader.readexactly(length), timeout=5.0)
    return channel, payload


async def _rtsp_play(
    url: str,
    port: int,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect a minimal RTSP client and negotiate through to PLAY (video only).

    Returns (reader, writer) with the stream positioned just after the
    PLAY 200 OK response — subsequent reads will be interleaved RTP frames.
    Caller is responsible for closing the writer.
    """
    reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=5.0)
    base_url = f"rtsp://127.0.0.1:{port}/intercom"

    # OPTIONS
    writer.write(f"OPTIONS {base_url} RTSP/1.0\r\nCSeq: 1\r\n\r\n".encode())
    await writer.drain()
    resp = await _read_rtsp_response(reader)
    assert b"200 OK" in resp, f"OPTIONS failed: {resp[:200]}"

    # DESCRIBE
    writer.write(f"DESCRIBE {base_url} RTSP/1.0\r\nCSeq: 2\r\nAccept: application/sdp\r\n\r\n".encode())
    await writer.drain()
    resp = await _read_rtsp_response(reader)
    assert b"200 OK" in resp, f"DESCRIBE failed: {resp[:200]}"

    # SETUP video track (interleaved TCP, ch 0-1)
    writer.write(
        f"SETUP {base_url}/video RTSP/1.0\r\nCSeq: 3\r\nTransport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n\r\n".encode()
    )
    await writer.drain()
    resp = await _read_rtsp_response(reader)
    assert b"200 OK" in resp, f"SETUP video failed: {resp[:200]}"
    session_id = "87654321"
    for line in resp.split(b"\r\n"):
        if line.lower().startswith(b"session:"):
            session_id = line.split(b":", 1)[1].strip().split(b";")[0].decode()
            break

    # PLAY
    writer.write(f"PLAY {base_url} RTSP/1.0\r\nCSeq: 4\r\nSession: {session_id}\r\n\r\n".encode())
    await writer.drain()
    resp = await _read_rtsp_response(reader)
    assert b"200 OK" in resp, f"PLAY failed: {resp[:200]}"

    return reader, writer


async def _rtsp_play_with_audio(
    port: int,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, int, int]:
    """Connect a minimal RTSP client, SETUP both video and audio, and PLAY.

    Returns (reader, writer, video_channel, audio_channel).
    video RTP → channel 0, video RTCP → channel 1
    audio RTP → channel 2, audio RTCP → channel 3
    Caller is responsible for closing the writer.
    """
    reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=5.0)
    base_url = f"rtsp://127.0.0.1:{port}/intercom"

    # OPTIONS
    writer.write(f"OPTIONS {base_url} RTSP/1.0\r\nCSeq: 1\r\n\r\n".encode())
    await writer.drain()
    resp = await _read_rtsp_response(reader)
    assert b"200 OK" in resp, f"OPTIONS failed: {resp[:200]}"

    # DESCRIBE — save SDP to verify audio track present
    writer.write(f"DESCRIBE {base_url} RTSP/1.0\r\nCSeq: 2\r\nAccept: application/sdp\r\n\r\n".encode())
    await writer.drain()
    resp = await _read_rtsp_response(reader)
    assert b"200 OK" in resp, f"DESCRIBE failed: {resp[:200]}"
    assert b"m=audio" in resp, f"SDP has no audio track:\n{resp.decode(errors='replace')}"
    assert b"PCMA/8000" in resp, "SDP audio track is not PCMA/8000"

    # SETUP video track (ch 0-1)
    writer.write(
        f"SETUP {base_url}/video RTSP/1.0\r\nCSeq: 3\r\nTransport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n\r\n".encode()
    )
    await writer.drain()
    resp = await _read_rtsp_response(reader)
    assert b"200 OK" in resp, f"SETUP video failed: {resp[:200]}"
    session_id = "87654321"
    for line in resp.split(b"\r\n"):
        if line.lower().startswith(b"session:"):
            session_id = line.split(b":", 1)[1].strip().split(b";")[0].decode()
            break

    # SETUP audio track (ch 2-3)
    writer.write(
        f"SETUP {base_url}/audio RTSP/1.0\r\n"
        f"CSeq: 4\r\n"
        f"Session: {session_id}\r\n"
        f"Transport: RTP/AVP/TCP;unicast;interleaved=2-3\r\n\r\n".encode()
    )
    await writer.drain()
    resp = await _read_rtsp_response(reader)
    assert b"200 OK" in resp, f"SETUP audio failed: {resp[:200]}"

    # PLAY
    writer.write(f"PLAY {base_url} RTSP/1.0\r\nCSeq: 5\r\nSession: {session_id}\r\n\r\n".encode())
    await writer.drain()
    resp = await _read_rtsp_response(reader)
    assert b"200 OK" in resp, f"PLAY failed: {resp[:200]}"

    return reader, writer, 0, 2  # video_ch=0, audio_ch=2


# ---------------------------------------------------------------------------
# Video integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_video_call():
    """Start a real video call and verify RTP packets flow from the device.

    Does not involve the RTSP server — tests the raw pipeline:
    TCP signaling → UDP RTP receiver → first NAL received.
    Wake the intercom screen before running.
    """
    if not COMELIT_TOKEN:
        pytest.skip("COMELIT_TOKEN not set")

    from custom_components.comelit_man.auth import authenticate
    from custom_components.comelit_man.client import IconaBridgeClient
    from custom_components.comelit_man.config_reader import get_device_config
    from custom_components.comelit_man.video_call import VideoCallSession

    client = IconaBridgeClient(COMELIT_HOST)
    await client.connect()
    session = None
    try:
        await authenticate(client, COMELIT_TOKEN)
        config = await get_device_config(client)
        await _setup_ctpp(client, config)

        session = VideoCallSession(client, config, auto_timeout=False)
        # start() internally waits for the first video NAL before returning
        receiver = await session.start()

        assert session.active, "Session not active after start()"
        total_pkts = receiver.udp_media_packet_count + receiver.tcp_media_packet_count
        assert total_pkts > 0, "No RTP packets received"
        print(f"\nVideo flowing — UDP={receiver.udp_media_packet_count} TCP={receiver.tcp_media_packet_count} pkts")
    finally:
        if session:
            await session.stop()
        await client.disconnect()


@pytest.mark.asyncio
async def test_rtsp_server_streams_video():
    """Start a real video call, pipe it through LocalRtspServer, connect a
    minimal TCP RTSP client, and verify interleaved RTP frames arrive.

    Covers the full stack: device → UDP RTP → rtp_receiver → rtsp_server
    queues → TCP interleaved → client socket.
    Wake the intercom screen before running.
    """
    if not COMELIT_TOKEN:
        pytest.skip("COMELIT_TOKEN not set")

    from custom_components.comelit_man.auth import authenticate
    from custom_components.comelit_man.client import IconaBridgeClient
    from custom_components.comelit_man.config_reader import get_device_config
    from custom_components.comelit_man.rtsp_server import LocalRtspServer
    from custom_components.comelit_man.video_call import VideoCallSession

    client = IconaBridgeClient(COMELIT_HOST)
    await client.connect()
    rtsp_server = LocalRtspServer()
    session = None
    rtsp_writer = None
    try:
        await authenticate(client, COMELIT_TOKEN)
        config = await get_device_config(client)
        await _setup_ctpp(client, config)

        url = await rtsp_server.start()
        port = rtsp_server._rtsp_port
        print(f"\nRTSP server at {url}")

        # VideoCallSession attaches receiver queues to the RTSP server internally
        session = VideoCallSession(client, config, auto_timeout=False, rtsp_server=rtsp_server)
        await session.start()
        # Unblock PLAY handlers now that video is flowing
        rtsp_server.mark_ready()

        # Connect minimal RTSP client and negotiate to PLAY
        rtsp_reader, rtsp_writer = await _rtsp_play(url, port)

        # Read 10 interleaved RTP frames on video channel (0) — ignore RTCP (1)
        video_frames: list[bytes] = []
        while len(video_frames) < 10:
            channel, payload = await _read_interleaved_frame(rtsp_reader)
            if channel == 0:
                video_frames.append(payload)

        print(f"Received {len(video_frames)} video RTP frames")

        # Validate RTP structure on the first video frame
        rtp = video_frames[0]
        assert len(rtp) >= 12, "RTP packet too short"
        version = (rtp[0] >> 6) & 0x3
        assert version == 2, f"RTP version expected 2, got {version}"
        pt = rtp[1] & 0x7F
        assert pt == 96, f"Unexpected RTP payload type {pt} (expected 96 for H.264)"

        # Send TEARDOWN
        base_url = f"rtsp://127.0.0.1:{port}/intercom"
        rtsp_writer.write(f"TEARDOWN {base_url} RTSP/1.0\r\nCSeq: 6\r\nSession: 87654321\r\n\r\n".encode())
        await rtsp_writer.drain()

    finally:
        if rtsp_writer:
            rtsp_writer.close()
        if session:
            await session.stop()
        await rtsp_server.stop()
        await client.disconnect()


@pytest.mark.asyncio
async def test_video_then_door_open():
    """Start video, open a door mid-stream, verify video keeps flowing.

    This exercises the highest-risk code path: async_open_door_on_ctpp
    sends a 0x1840/0x000D on the live video CTPP channel without
    interrupting the video session.
    Wake the intercom screen before running.
    """
    if not COMELIT_TOKEN:
        pytest.skip("COMELIT_TOKEN not set")

    from custom_components.comelit_man.auth import authenticate
    from custom_components.comelit_man.client import IconaBridgeClient
    from custom_components.comelit_man.config_reader import get_device_config
    from custom_components.comelit_man.rtsp_server import LocalRtspServer
    from custom_components.comelit_man.video_call import VideoCallSession

    client = IconaBridgeClient(COMELIT_HOST)
    await client.connect()
    rtsp_server = LocalRtspServer()
    session = None
    rtsp_writer = None
    try:
        await authenticate(client, COMELIT_TOKEN)
        config = await get_device_config(client)
        assert config.doors, "No doors in config — cannot test door open"
        await _setup_ctpp(client, config)

        url = await rtsp_server.start()
        port = rtsp_server._rtsp_port

        session = VideoCallSession(client, config, auto_timeout=False, rtsp_server=rtsp_server)
        await session.start()
        rtsp_server.mark_ready()

        rtsp_reader, rtsp_writer = await _rtsp_play(url, port)

        # Confirm video is flowing before the door open
        pre_frames: list[bytes] = []
        while len(pre_frames) < 5:
            channel, payload = await _read_interleaved_frame(rtsp_reader)
            if channel == 0:
                pre_frames.append(payload)
        print(f"\n{len(pre_frames)} video frames before door open")

        # Open the door on the active CTPP channel
        door = config.doors[0]
        our_addr = f"{config.apt_address}{config.apt_subaddress}"
        entrance_addr = config.caller_address or our_addr
        print(f"Opening door '{door.name}' (output_index={door.output_index}) on CTPP")
        await session.async_open_door_on_ctpp(our_addr, entrance_addr, door.output_index)
        print(f"Device ACKed relay open — {door.name} triggered")
        print("Checking video continues...")

        # Confirm video keeps flowing after the door open
        post_frames: list[bytes] = []
        while len(post_frames) < 5:
            channel, payload = await _read_interleaved_frame(rtsp_reader)
            if channel == 0:
                post_frames.append(payload)
        print(f"{len(post_frames)} video frames after door open — stream intact")

        assert len(post_frames) == 5, "Video stopped after door open"

        rtsp_writer.write(
            f"TEARDOWN rtsp://127.0.0.1:{port}/intercom RTSP/1.0\r\nCSeq: 6\r\nSession: 87654321\r\n\r\n".encode()
        )
        await rtsp_writer.drain()

    finally:
        if rtsp_writer:
            rtsp_writer.close()
        if session:
            await session.stop()
        await rtsp_server.stop()
        await client.disconnect()


@pytest.mark.asyncio
async def test_rtsp_server_streams_audio():
    """Verify RTSP audio track is negotiated; confirm device sends no audio on HA-initiated calls.

    Live device test confirmed: the device does NOT send PCMA on HA-initiated video
    calls regardless of the answer sequence sent (tested single peer/accept, full
    3-message sequence, and waited through the 30s renewal cycle — 0 PT=8 packets).
    Audio only flows during inbound calls triggered by a visitor pressing the doorbell.

    This test validates:
      - SDP advertises m=audio PCMA/8000 (RTSP plumbing is wired up)
      - Video flows normally alongside the audio track negotiation
      - audio_packet_count == 0 (documents the confirmed device behavior)

    Wake the intercom screen before running. Requires HA integration stopped.
    """
    if not COMELIT_TOKEN:
        pytest.skip("COMELIT_TOKEN not set")

    from custom_components.comelit_man.auth import authenticate
    from custom_components.comelit_man.client import IconaBridgeClient
    from custom_components.comelit_man.config_reader import get_device_config
    from custom_components.comelit_man.rtsp_server import LocalRtspServer
    from custom_components.comelit_man.video_call import VideoCallSession

    client = IconaBridgeClient(COMELIT_HOST)
    await client.connect()
    rtsp_server = LocalRtspServer()
    session = None
    rtsp_writer = None
    try:
        await authenticate(client, COMELIT_TOKEN)
        config = await get_device_config(client)
        await _setup_ctpp(client, config)

        url = await rtsp_server.start()
        port = rtsp_server._rtsp_port
        print(f"\nRTSP server at {url}")

        session = VideoCallSession(client, config, auto_timeout=False, rtsp_server=rtsp_server)
        receiver = await session.start()
        rtsp_server.mark_ready()

        # SETUP both video (ch 0) and audio (ch 2), then PLAY
        rtsp_reader, rtsp_writer, video_ch, audio_ch = await _rtsp_play_with_audio(port)
        print(f"RTSP negotiated: video_ch={video_ch} audio_ch={audio_ch}")

        # Collect video frames for 10s to confirm video pipeline is healthy.
        video_frames: list[bytes] = []
        deadline = asyncio.get_event_loop().time() + 10.0
        while asyncio.get_event_loop().time() < deadline:
            try:
                channel, payload = await asyncio.wait_for(_read_interleaved_frame(rtsp_reader), timeout=2.0)
            except TimeoutError:
                continue
            if channel == video_ch:
                video_frames.append(payload)
            if len(video_frames) >= 10:
                break

        print(f"Collected {len(video_frames)} video frames")
        print(f"RTP receiver audio_packet_count={receiver._audio_packet_count}")

        # Video must be flowing.
        assert video_frames, "No video RTP frames received"

        # The device does NOT send PCMA on HA-initiated calls — confirmed by live
        # device test over 40s including the first renewal cycle. Audio only flows
        # during inbound calls triggered by a visitor pressing the doorbell.
        # This assertion documents the known behavior; if it ever fails it means
        # the device started sending audio and the RTSP plumbing should be validated.
        assert receiver._audio_packet_count == 0, (
            f"Unexpected audio: device sent {receiver._audio_packet_count} PT=8 packets — "
            "update this test and validate the audio pipeline"
        )

        base_url = f"rtsp://127.0.0.1:{port}/intercom"
        rtsp_writer.write(f"TEARDOWN {base_url} RTSP/1.0\r\nCSeq: 6\r\nSession: 87654321\r\n\r\n".encode())
        await rtsp_writer.drain()

    finally:
        if rtsp_writer:
            rtsp_writer.close()
        if session:
            await session.stop()
        await rtsp_server.stop()
        await client.disconnect()


@pytest.mark.asyncio
async def test_inbound_skip_callinit():
    """Inbound doorbell answer: PCAP-correct sequence from Android app capture.

    Key findings from PCAPdroid capture (inbound_call.pcap):
    - Counter seed = fresh int(time.time()), NOT ring_ts
    - callee in ALL CTPP messages = our own apt_address (not entrance_addr)
    - No CALL_INIT sent by us (device initiated the ring)
    - Codec ACK uses codec_param=0x07 (inbound) not 0x27 (outbound)
    - Device sends 0x0008+0x1800+0x0008 bundle; no 0x0002 from device
    - After that bundle: counter jumps by +B5, then +B4 per 0x1840 msg
    - 0x1840/0x0003 sent after RTPC2 open (purpose: unknown, required)
    - App sends 0x0002 call_accepted TO device (reversed from outbound)
    - Outbound audio: same UDP socket, ICONA req_id = device's RTPC req_id

    Pass: audio received from device AND outbound silence frames sent.
    User action: press doorbell within 60s. HA integration must be stopped.
    """
    if not COMELIT_TOKEN:
        pytest.skip("COMELIT_TOKEN not set")

    from custom_components.comelit_man.auth import authenticate
    from custom_components.comelit_man.channels import ChannelType, ViperMessageId
    from custom_components.comelit_man.client import IconaBridgeClient
    from custom_components.comelit_man.config_reader import get_device_config
    from custom_components.comelit_man.protocol import (
        encode_answer_peer,
        encode_call_accepted,
        encode_call_ack,
        encode_call_response_ack,
        encode_rtpc2_ready,
        encode_rtpc_link,
        encode_video_config,
    )
    from custom_components.comelit_man.rtp_receiver import RtpReceiver
    from custom_components.comelit_man.rtsp_server import LocalRtspServer
    from custom_components.comelit_man.vip_listener import parse_ctpp_message

    _CTR_INCR_BYTE4 = 0x00010000
    _CTR_INCR_BYTE5 = 0x01000000
    PREFIX_RING = 0x18C0

    client = IconaBridgeClient(COMELIT_HOST)
    await client.connect()
    rtsp_server = LocalRtspServer()
    receiver: RtpReceiver | None = None
    rtsp_writer = None

    try:
        from custom_components.comelit_man.push import register_push

        await authenticate(client, COMELIT_TOKEN)
        config = await get_device_config(client)

        # --- Full Android pre-ring initialization sequence (PCAP-verified) ---
        # Android opens INFO before CTPP (PCAP t=-66s relative to ring).
        info_ch = await client.open_channel("INFO", ChannelType.INFO)
        await client.send_json(
            info_ch,
            {
                "message": "server-info",
                "message-type": "request",
                "message-id": int(ViperMessageId.SERVER_INFO),
            },
        )
        print("  Android init: INFO server-info done")

        # CTPP+CSPB+ctpp_init (matches Android order)
        init_ts = await _setup_ctpp(client, config)

        # Android closes INFO after ctpp_init (PCAP t=-19.7s)
        await client.close_channel("INFO")

        # Second get-config on existing UCFG channel (PCAP t=-19.5s)
        ucfg = client.get_channel("UCFG")
        assert ucfg is not None, "UCFG channel not open"
        await client.send_json(
            ucfg,
            {
                "message": "get-configuration",
                "addressbooks": "all",
                "message-type": "request",
                "message-id": 16,
            },
        )
        print("  Android init: UCFG second get-config done")

        # FRCG rcg-get-params (PCAP t=-19.4s, Android face-recognition init)
        frcg_ch = await client.open_channel("FRCG", ChannelType.UAUT, wire_name="FRCG")
        await client.send_json(
            frcg_ch,
            {
                "message": "rcg-get-params",
                "message-type": "request",
                "message-id": 121,
            },
        )
        print("  Android init: FRCG rcg-get-params done")

        # Second UAUT re-auth then close (PCAP t=-19.3s).
        # Skipped: device does not respond to second UAUT on this firmware.

        # Second UCFG open — stays open, no messages (PCAP t=-19.2s)
        await client.open_channel("UCFG2", ChannelType.UCFG, wire_name="UCFG")
        print("  Android init: UCFG2 opened (stays open)")

        # PUSH registration — last step before ring (matches Android order)
        await register_push(client, config, lambda e: None)
        print("  Android init: PUSH registered — ready for ring")

        ctpp = client.get_channel("CTPP")
        assert ctpp is not None, "CTPP channel not open"

        url = await rtsp_server.start()
        port = rtsp_server._rtsp_port

        # Addresses: use config values (consistent with what CTPP was opened with).
        # callee = our own base apt_address in all inbound CTPP messages (PCAP-verified).
        our_addr = f"{config.apt_address}{config.apt_subaddress}"
        our_base_addr = config.apt_address
        # ack_ts for keepalive renewals: device increments counter by 0x01010000 from init_ts.
        # Any renewal not ACKed causes the device session to go stale (stops accepting events).
        _renewal_ack_ts = (init_ts + 0x01010000) & 0xFFFFFFFF

        # Pre-register device RTPC placeholder before ring so client auto-responds
        # when device opens its own RTPC channel after video config.
        device_rtpc = client.register_placeholder_channel("RTPC_DEVICE")

        print("\n=== INBOUND ANSWER TEST (PCAP-correct) ===")
        print(f"RTSP at {url}  our_addr={our_addr}  our_base={our_base_addr}")
        print("Press the doorbell now (60s window)...")

        # --- Wait for ring ---
        t_ring = 0.0
        entrance_addr = ""
        fresh_ts = 0
        deadline = asyncio.get_running_loop().time() + 60.0
        while asyncio.get_running_loop().time() < deadline:
            remaining = deadline - asyncio.get_running_loop().time()
            try:
                data = await asyncio.wait_for(ctpp.response_queue.get(), timeout=min(remaining, 5.0))
            except TimeoutError:
                continue
            msg = parse_ctpp_message(data)
            if msg and msg["prefix"] == PREFIX_RING:
                addrs = msg.get("addresses", [])
                entrance_addr = addrs[0] if addrs else ""
                t_ring = asyncio.get_running_loop().time()
                # fresh_ts is derived from ring_ts using the device's timestamp transform
                # (PCAP2-verified): set high bit of b[0], swap b[2]/b[3], increment new b[3].
                # This is NOT int(time.time()) — the device uses its own proprietary clock.
                _rb = bytearray(struct.pack("<I", msg["timestamp"]))
                _rb[0] |= 0x80
                _rb[2], _rb[3] = _rb[3], (_rb[2] + 1) & 0xFF
                fresh_ts = struct.unpack("<I", bytes(_rb))[0]
                print(f"\nRing! ring_ts=0x{msg['timestamp']:08X} entrance={entrance_addr}")
                print(f"  fresh_ts=0x{fresh_ts:08X} (transform of ring_ts)")
                break
            elif msg and msg.get("prefix") == 0x1860 and msg.get("action") == 0x0010:
                # VIP keepalive renewal — must ACK to keep the session alive.
                # Device sends every ~20s; unACKed renewals cause it to ignore our answer.
                # Log bytes[10:12] to verify capability bytes match our ctpp_init (0x18C2 on PCAP device).
                cap_bytes = data[10:12].hex(" ") if len(data) >= 12 else "??"
                await client.send_binary(ctpp, encode_call_response_ack(our_addr, our_base_addr, _renewal_ack_ts))
                await client.send_binary(
                    ctpp, encode_call_response_ack(our_addr, our_base_addr, _renewal_ack_ts, prefix=0x1820)
                )
                print(
                    f"  [waiting] ACK'd keepalive renewal (ack_ts=0x{_renewal_ack_ts:08X}) cap_bytes={cap_bytes} full={data.hex(' ')}"
                )
        else:
            pytest.skip("No ring within 60s — press doorbell to run this test")

        def elapsed() -> float:
            return asyncio.get_running_loop().time() - t_ring

        # --- Step 1: ACK the ring (fresh_ts, callee=our_base_addr) ---
        await client.send_binary(ctpp, encode_call_response_ack(our_addr, our_base_addr, fresh_ts))
        print(f"  [{elapsed():.2f}s] Sent 0x1800 ACK ts=0x{fresh_ts:08X}")

        # --- Steps 2-3+5: BURST — RTPC OPEN + UDPM OPEN + codec ACK sent together ---
        # PCAP shows Android app sent all 3 within 8ms, before device ACK'd any channel.
        # Device requires codec ACK to arrive while channels are still in flight (t=19ms in PCAP,
        # device sent RTPC ACK at t=27ms). Awaiting channel ACKs sequentially delays codec ACK
        # to t=30ms — after device processes channels — and the device ignores it.
        codec_ack = encode_call_ack(our_addr, our_base_addr, fresh_ts, codec_param=0x07)
        rtpc1_task = asyncio.create_task(client.open_channel("RTPC", ChannelType.UAUT, trailing_byte=1))
        await asyncio.sleep(0)  # let RTPC task send its open packet
        udpm_task = asyncio.create_task(client.open_channel("UDPM", ChannelType.UAUT, trailing_byte=1))
        await asyncio.sleep(0)  # let UDPM task send its open packet
        await client.send_binary(ctpp, codec_ack)  # codec ACK before any channel ACKs arrive
        print(f"  [{elapsed():.3f}s] Burst sent: RTPC OPEN + UDPM OPEN + codec ACK (param=0x07)")

        # Collect channel ACKs (already in flight)
        rtpc1 = await rtpc1_task
        udpm = await udpm_task
        udpm_token = 0
        if len(udpm.open_response_body) >= 18:
            udpm_token = struct.unpack_from("<H", udpm.open_response_body, 16)[0]
        print(
            f"  [{elapsed():.3f}s] Channels ACK'd: RTPC=0x{rtpc1.server_channel_id:04X} UDPM token=0x{udpm_token:04X}"
        )

        # --- Step 4: RTP receiver setup (after burst, before bundle wait) ---
        receiver = RtpReceiver(
            client.host,
            client.port,
            control_req_id=udpm.server_channel_id,
            media_req_id=0,
            udpm_token=udpm_token,
        )
        receiver.attach_rtsp_queues(rtsp_server.nal_queue, rtsp_server.audio_queue, rtp_queue=rtsp_server.rtp_queue)
        await receiver.start_control()
        receiver.start_keepalive()

        # --- Step 7: Wait for device response bundle (must come before ACK2) ---
        print("  Waiting for device response bundle...")
        try:
            async with asyncio.timeout(10.0):
                while True:
                    data = await ctpp.response_queue.get()
                    if len(data) < 2:
                        continue
                    msg_type = struct.unpack_from("<H", data, 0)[0]
                    action = struct.unpack_from(">H", data, 6)[0] if len(data) >= 8 else 0
                    print(f"  [{elapsed():.2f}s] Dev: 0x{msg_type:04X}/0x{action:04X}  hex={data.hex(' ')}")
                    if msg_type == PREFIX_RING and action == 0x0029:
                        await client.send_binary(ctpp, encode_call_response_ack(our_addr, our_base_addr, fresh_ts))
                        print(f"  [{elapsed():.2f}s] ACK'd 0x0029 with fresh_ts=0x{fresh_ts:08X}")
                        continue
                    if msg_type != PREFIX_RING:
                        break  # got a non-ring response — device acknowledged
        except TimeoutError:
            print(f"  [{elapsed():.2f}s] WARNING: no device response in 10s — proceeding anyway")

        # Drain any remaining bundle messages
        await asyncio.sleep(0.05)
        while not ctpp.response_queue.empty():
            try:
                data = ctpp.response_queue.get_nowait()
                msg_type = struct.unpack_from("<H", data, 0)[0] if len(data) >= 2 else 0
                action = struct.unpack_from(">H", data, 6)[0] if len(data) >= 8 else 0
                print(f"  [{elapsed():.2f}s] Dev bundle drain: 0x{msg_type:04X}/0x{action:04X}")
            except asyncio.QueueEmpty:
                break

        # --- Step 8: ACK2 (fresh_ts+B5) + open RTPC2 simultaneously (PCAP2-verified) ---
        call_counter = (fresh_ts + _CTR_INCR_BYTE5) & 0xFFFFFFFF
        await client.send_binary(ctpp, encode_call_response_ack(our_addr, our_base_addr, call_counter))
        print(f"  [{elapsed():.2f}s] Sent 0x1800 ACK2 counter=0x{call_counter:08X} (+B5)")
        rtpc2_task = asyncio.create_task(
            client.open_channel("RTPC2", ChannelType.UAUT, trailing_byte=1, wire_name="RTPC")
        )
        await asyncio.sleep(0)  # let RTPC2 task send its open packet

        # --- Step 6 (retransmit): Codec ACK retransmit with ts=fresh_ts+B5 (PCAP2-verified) ---
        # PCAP2: retransmit uses same counter as ACK2, NOT the original fresh_ts.
        codec_ack_retx = encode_call_ack(our_addr, our_base_addr, call_counter, codec_param=0x07)
        await client.send_binary(ctpp, codec_ack_retx)
        print(f"  [{elapsed():.3f}s] Codec ACK retransmit ts=0x{call_counter:08X}")

        # Await RTPC2 channel ACK
        rtpc2 = await rtpc2_task
        media_req_id = rtpc2.server_channel_id
        print(f"  [{elapsed():.2f}s] RTPC2 media_req_id=0x{media_req_id:04X}")
        receiver.set_media_req_id(media_req_id)
        await receiver.start_media()

        # --- Step 10: RTPC2-ready (+B4) ---
        call_counter = (call_counter + _CTR_INCR_BYTE4) & 0xFFFFFFFF
        await client.send_binary(ctpp, encode_rtpc2_ready(our_addr, our_base_addr, call_counter))
        print(f"  [{elapsed():.2f}s] Sent 0x1840/0x0003 rtpc2_ready counter=0x{call_counter:08X}")

        # --- Step 11: RTPC link (+B4, no extra ACK before this per PCAP2) ---
        call_counter = (call_counter + _CTR_INCR_BYTE4) & 0xFFFFFFFF
        await client.send_binary(ctpp, encode_rtpc_link(our_addr, our_base_addr, rtpc1.server_channel_id, call_counter))
        print(f"  [{elapsed():.2f}s] Sent RTPC link counter=0x{call_counter:08X}")

        # --- Step 12: Video config (+B4) ---
        # PCAP2-verified: inbound video_config uses 320x240 for both primary and secondary.
        call_counter = (call_counter + _CTR_INCR_BYTE4) & 0xFFFFFFFF
        vid_config = encode_video_config(our_addr, our_base_addr, media_req_id, call_counter, width=320, height=240)
        await client.send_binary(ctpp, vid_config)
        print(f"  [{elapsed():.2f}s] Sent video config counter=0x{call_counter:08X}")

        # Start media pipeline now so it's ready before device sends first RTP
        receiver.set_media_req_id(media_req_id)
        await receiver.start_media()

        # --- Step 13: Retransmit video config after 3s (+B4, matches PCAP2 ~3s interval) ---
        await asyncio.sleep(3.0)
        call_counter = (call_counter + _CTR_INCR_BYTE4) & 0xFFFFFFFF
        await client.send_binary(
            ctpp, encode_video_config(our_addr, our_base_addr, media_req_id, call_counter, width=320, height=240)
        )
        print(f"  [{elapsed():.2f}s] Retransmitted video config counter=0x{call_counter:08X}")

        # Brief wait for device to process before PEER
        await asyncio.sleep(0.4)

        # --- Step 14: PEER (inbound=True: caller+our_base_addr after separator, PCAP2-verified 48B) ---
        call_counter = (call_counter + _CTR_INCR_BYTE4) & 0xFFFFFFFF
        await client.send_binary(ctpp, encode_answer_peer(our_addr, our_base_addr, call_counter, inbound=True))
        print(f"  [{elapsed():.2f}s] Sent PEER counter=0x{call_counter:08X}")

        # --- Step 15: call_accepted 0x0002 TO device (+B4) ---
        call_counter = (call_counter + _CTR_INCR_BYTE4) & 0xFFFFFFFF
        await client.send_binary(ctpp, encode_call_accepted(our_addr, our_base_addr, call_counter))
        print(f"  [{elapsed():.2f}s] Sent call_accepted (0x0002) counter=0x{call_counter:08X}")

        # --- Step 16: ACK device rtpc_link + PEER, then wait for device RTPC open ---
        # PCAP2 ordering: device sends 0x1840/0x000A (rtpc_link) and 0x1840/0x000E (PEER)
        # on the CTPP channel BEFORE it opens its RTPC via ABCD. We must ACK those first;
        # device opens RTPC only after receiving our ACKs. Previous code had the drain loop
        # after open_event.wait() — that was a deadlock.
        print("  Waiting for device signaling (rtpc_link + PEER) then RTPC open...")
        acked_rtpc_link = False
        acked_peer = False
        drain_deadline = asyncio.get_running_loop().time() + 10.0
        while not (acked_rtpc_link and acked_peer):
            remaining = drain_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                data = await asyncio.wait_for(ctpp.response_queue.get(), timeout=remaining)
            except TimeoutError:
                break
            if len(data) < 2:
                continue
            msg_type = struct.unpack_from("<H", data, 0)[0]
            action = struct.unpack_from(">H", data, 6)[0] if len(data) >= 8 else 0
            dev_ts = struct.unpack_from("<I", data, 2)[0] if len(data) >= 6 else 0
            print(f"  [{elapsed():.2f}s] Dev: 0x{msg_type:04X}/0x{action:04X} ts=0x{dev_ts:08X}")
            if msg_type == 0x1840:
                _rb = bytearray(struct.pack("<I", dev_ts))
                _rb[0] |= 0x80
                _rb[2], _rb[3] = _rb[3], (_rb[2] + 1) & 0xFF
                ack_ts = struct.unpack("<I", bytes(_rb))[0]
                await client.send_binary(ctpp, encode_call_response_ack(our_addr, our_base_addr, ack_ts))
                print(f"  [{elapsed():.2f}s] ACK'd 0x{action:04X} ts=0x{ack_ts:08X}")
                if action == 0x000A:
                    acked_rtpc_link = True
                elif action == 0x000E:
                    acked_peer = True

        device_rtpc_req_id = 0
        try:
            async with asyncio.timeout(5.0):
                await device_rtpc.open_event.wait()
            device_rtpc_req_id = device_rtpc.server_channel_id
            print(f"  [{elapsed():.2f}s] Device RTPC=0x{device_rtpc_req_id:04X}")
            receiver.start_audio_sender(device_rtpc_req_id)
            print(f"  [{elapsed():.2f}s] Started audio sender req_id=0x{device_rtpc_req_id:04X}")
        except TimeoutError:
            print(f"  [{elapsed():.2f}s] WARNING: device RTPC not received — skipping audio sender")

        # --- Route TCP media from RTPC1+RTPC2 into RtpReceiver ---
        # Inbound calls: device sends H.264 video on RTPC2 (TCP) and PCMA audio on RTPC1 (TCP).
        # Client strips the ICONA header before queuing, so data arrives as raw RTP.
        async def _tcp_media_router() -> None:
            while True:
                for ch in (rtpc1, rtpc2):
                    try:
                        data = ch.response_queue.get_nowait()
                        if len(data) >= 12:
                            receiver.receive_tcp_rtp(data)
                    except asyncio.QueueEmpty:
                        pass
                await asyncio.sleep(0.001)

        tcp_media_task = asyncio.create_task(_tcp_media_router())

        # --- Collect RTSP frames ---
        rtsp_server.mark_ready()
        rtsp_reader, rtsp_writer, video_ch, audio_ch = await _rtsp_play_with_audio(port)
        print(f"  [{elapsed():.2f}s] RTSP PLAY: video_ch={video_ch} audio_ch={audio_ch}")

        video_frames = 0
        audio_frames = 0
        collect_deadline = asyncio.get_running_loop().time() + 15.0
        while asyncio.get_running_loop().time() < collect_deadline:
            try:
                channel, _payload = await asyncio.wait_for(_read_interleaved_frame(rtsp_reader), timeout=2.0)
            except TimeoutError:
                continue
            if channel == video_ch:
                video_frames += 1
            elif channel == audio_ch:
                audio_frames += 1

        tcp_media_task.cancel()

        print(f"\n=== RESULTS [{elapsed():.1f}s] ===")
        print(f"  Video frames (RTSP):           {video_frames}")
        print(f"  Audio frames (RTSP):           {audio_frames}")
        print(f"  Audio received from device:    {receiver._audio_packet_count}")
        print(f"  Audio sent to device:          {receiver.audio_sent_count}")

        base_url = f"rtsp://127.0.0.1:{port}/intercom"
        with contextlib.suppress(Exception):
            rtsp_writer.write(f"TEARDOWN {base_url} RTSP/1.0\r\nCSeq: 9\r\nSession: 87654321\r\n\r\n".encode())
            await rtsp_writer.drain()

        assert receiver._audio_packet_count > 0, (
            "No PCMA audio received from device — signaling sequence incorrect or timing issue."
        )
        assert receiver.audio_sent_count > 0, (
            "No audio sent to device — device RTPC did not open (device_rtpc.open_event never fired)."
        )

    finally:
        if rtsp_writer:
            with contextlib.suppress(Exception):
                rtsp_writer.close()
        if receiver:
            await receiver.stop()
        await rtsp_server.stop()
        await client.disconnect()
