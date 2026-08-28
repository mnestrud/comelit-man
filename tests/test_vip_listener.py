"""Unit tests for VipEventListener and parse_ctpp_message."""

from __future__ import annotations

import asyncio
import contextlib
import struct
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.comelit_man.models import DeviceConfig, PushEvent
from custom_components.comelit_man.vip_listener import (
    ACTION_CLOSED,
    ACTION_CONNECTED,
    ACTION_DOOR_OPENED,
    ACTION_IDLE,
    ACTION_IN_ALERTING,
    ACTION_OUT_ALERTING,
    ACTION_REGISTRATION_RENEWAL,
    MIN_MSG_SIZE,
    PREFIX_ACK,
    PREFIX_CALL_INIT,
    PREFIX_VIDEO_EVENT,
    PREFIX_VIP_EVENT,
    VipEventListener,
    parse_ctpp_message,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctpp_msg(
    prefix: int,
    timestamp: int,
    action: int,
    flags: int | None = None,
    addresses: list[str] | None = None,
) -> bytes:
    """Build a binary CTPP message for testing."""
    buf = bytearray()
    buf += struct.pack("<H", prefix)
    buf += struct.pack("<I", timestamp)
    buf += struct.pack(">H", action)
    if flags is not None:
        buf += struct.pack(">H", flags)
    if addresses:
        buf += b"\xff\xff\xff\xff"
        for addr in addresses:
            buf += addr.encode("ascii") + b"\x00"
    return bytes(buf)


def _make_config(apt_address: str = "SB000006", apt_subaddress: int = 1) -> DeviceConfig:
    return DeviceConfig(apt_address=apt_address, apt_subaddress=apt_subaddress)


def _make_listener(
    callback=None,
    apt_address: str = "SB000006",
    apt_subaddress: int = 1,
    init_ts: int = 0x12000000,
    on_inbound_ring=None,
    on_call_idle=None,
) -> VipEventListener:
    client = MagicMock()
    client.send_binary = AsyncMock()
    config = _make_config(apt_address, apt_subaddress)
    cb = callback or MagicMock()
    listener = VipEventListener(
        client,
        config,
        cb,
        init_ts=init_ts,
        on_inbound_ring=on_inbound_ring,
        on_call_idle=on_call_idle,
    )
    # Attach a fake open channel so send_binary works
    listener._channel = MagicMock()
    listener._channel.response_queue = asyncio.Queue()
    return listener


# ---------------------------------------------------------------------------
# parse_ctpp_message
# ---------------------------------------------------------------------------


class TestParseCtppMessage:
    def test_returns_none_for_too_short(self):
        for n in range(MIN_MSG_SIZE):
            assert parse_ctpp_message(b"\x00" * n) is None

    def test_parses_prefix_le16(self):
        data = _make_ctpp_msg(PREFIX_VIP_EVENT, 0x12345678, 0x0001)
        msg = parse_ctpp_message(data)
        assert msg["prefix"] == PREFIX_VIP_EVENT

    def test_parses_timestamp_le32(self):
        data = _make_ctpp_msg(PREFIX_VIP_EVENT, 0xDEADBEEF, 0x0002)
        msg = parse_ctpp_message(data)
        assert msg["timestamp"] == 0xDEADBEEF

    def test_parses_action_be16(self):
        data = _make_ctpp_msg(PREFIX_VIP_EVENT, 0, ACTION_IN_ALERTING)
        msg = parse_ctpp_message(data)
        assert msg["action"] == ACTION_IN_ALERTING

    def test_no_flags_when_less_than_10_bytes(self):
        # 8 bytes exactly — no flags
        data = b"\x60\x18" + struct.pack("<I", 0) + struct.pack(">H", 0)
        assert len(data) == MIN_MSG_SIZE
        msg = parse_ctpp_message(data)
        assert "flags" not in msg

    def test_parses_flags_when_ge_10_bytes(self):
        data = _make_ctpp_msg(PREFIX_VIP_EVENT, 0, 0, flags=0xABCD)
        msg = parse_ctpp_message(data)
        assert msg["flags"] == 0xABCD

    def test_extracts_sb_addresses(self):
        data = _make_ctpp_msg(PREFIX_VIP_EVENT, 0, 0, flags=0, addresses=["SB000001", "SB000006"])
        msg = parse_ctpp_message(data)
        assert "SB000001" in msg["addresses"]
        assert "SB000006" in msg["addresses"]

    def test_no_addresses_when_none_present(self):
        data = _make_ctpp_msg(PREFIX_VIP_EVENT, 0, 0, flags=0)
        msg = parse_ctpp_message(data)
        assert msg["addresses"] == []

    def test_raw_bytes_included(self):
        data = _make_ctpp_msg(PREFIX_CALL_INIT, 42, 0)
        msg = parse_ctpp_message(data)
        assert msg["raw"] == data

    def test_minimum_size_exactly_parses(self):
        data = b"\x60\x18\x00\x00\x00\x00\x00\x01"  # 8 bytes
        msg = parse_ctpp_message(data)
        assert msg is not None
        assert msg["action"] == 1


# ---------------------------------------------------------------------------
# VipEventListener._fire_event — deduplication
# ---------------------------------------------------------------------------


class TestFireEvent:
    def test_fires_callback_with_push_event(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        listener._fire_event("ring", ["SB000001"])

        cb.assert_called_once()
        event: PushEvent = cb.call_args[0][0]
        assert event.event_type == "ring"
        assert event.apt_address == "SB000001"

    def test_first_address_used_as_apt_address(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        listener._fire_event("ring", ["SB000001", "SB000006"])

        event: PushEvent = cb.call_args[0][0]
        assert event.apt_address == "SB000001"

    def test_empty_addresses_gives_empty_apt_address(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        listener._fire_event("ring", [])

        event: PushEvent = cb.call_args[0][0]
        assert event.apt_address == ""

    def test_duplicate_within_window_suppressed(self):
        cb = MagicMock()
        listener = _make_listener(cb)
        listener._dedup_window = 10.0

        listener._fire_event("ring", [])
        listener._fire_event("ring", [])

        cb.assert_called_once()

    def test_different_event_types_not_deduplicated(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        listener._fire_event("ring", [])
        listener._fire_event("door_opened", [])

        assert cb.call_count == 2

    def test_fires_again_after_dedup_window(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        # Pre-seed the last_fired time so it appears old
        listener._last_fired["ring"] = time.time() - 20.0
        listener._dedup_window = 10.0

        listener._fire_event("ring", [])

        cb.assert_called_once()

    def test_raw_includes_addresses_and_source(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        listener._fire_event("ring", ["SB000001"])

        event: PushEvent = cb.call_args[0][0]
        assert event.raw["source"] == "ctpp_vip"
        assert event.raw["addresses"] == ["SB000001"]

    def test_callback_exception_does_not_propagate(self):
        cb = MagicMock(side_effect=RuntimeError("boom"))
        listener = _make_listener(cb)

        # Should not raise
        listener._fire_event("ring", [])


# ---------------------------------------------------------------------------
# VipEventListener._handle_vip_event
# ---------------------------------------------------------------------------


class TestHandleVipEvent:
    def _msg(self, prefix: int, action: int, addresses: list[str] | None = None) -> dict:
        return {
            "prefix": prefix,
            "timestamp": 0,
            "action": action,
            "flags": 0,
            "addresses": addresses or [],
        }

    def test_call_init_fires_doorbell_ring(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        listener._handle_vip_event(self._msg(PREFIX_CALL_INIT, 0))

        cb.assert_called_once()
        assert cb.call_args[0][0].event_type == "ring"

    def test_vip_event_in_alerting_fires_doorbell_ring(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        listener._handle_vip_event(self._msg(PREFIX_VIP_EVENT, ACTION_IN_ALERTING))

        cb.assert_called_once()
        assert cb.call_args[0][0].event_type == "ring"

    def test_vip_event_door_opened_fires_door_opened(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        listener._handle_vip_event(self._msg(PREFIX_VIP_EVENT, ACTION_DOOR_OPENED))

        cb.assert_called_once()
        assert cb.call_args[0][0].event_type == "door_opened"

    def test_vip_event_connected_does_not_fire(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        listener._handle_vip_event(self._msg(PREFIX_VIP_EVENT, ACTION_CONNECTED))

        cb.assert_not_called()

    def test_vip_event_closed_does_not_fire(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        listener._handle_vip_event(self._msg(PREFIX_VIP_EVENT, ACTION_CLOSED))

        cb.assert_not_called()

    def test_vip_event_idle_does_not_fire(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        listener._handle_vip_event(self._msg(PREFIX_VIP_EVENT, ACTION_IDLE))

        cb.assert_not_called()

    def test_vip_event_out_alerting_does_not_fire(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        listener._handle_vip_event(self._msg(PREFIX_VIP_EVENT, ACTION_OUT_ALERTING))

        cb.assert_not_called()

    def test_vip_event_zero_action_does_not_fire(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        listener._handle_vip_event(self._msg(PREFIX_VIP_EVENT, 0))

        cb.assert_not_called()

    def test_prefix_event_with_nonzero_action_does_not_fire(self):
        """0x1840 events are call-related internals, not user-visible events."""
        cb = MagicMock()
        listener = _make_listener(cb)

        listener._handle_vip_event(self._msg(PREFIX_VIDEO_EVENT, 0x0001))

        cb.assert_not_called()

    def test_ack_prefix_does_not_fire(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        listener._handle_vip_event(self._msg(PREFIX_ACK, 0x0001))

        cb.assert_not_called()


# ---------------------------------------------------------------------------
# VipEventListener._process_message
# ---------------------------------------------------------------------------


class TestProcessMessage:
    @pytest.mark.asyncio
    async def test_too_short_message_ignored(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        await listener._process_message(b"\x60\x18")  # only 2 bytes

        cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_registration_renewal_sends_ack_pair_not_event(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        data = _make_ctpp_msg(PREFIX_VIP_EVENT, 0x12345678, ACTION_REGISTRATION_RENEWAL, flags=0)
        await listener._process_message(data)

        # No user-visible event
        cb.assert_not_called()
        # send_binary called twice (ACK + CONFIRM)
        assert listener._client.send_binary.await_count == 2

    @pytest.mark.asyncio
    async def test_door_opened_fires_event_without_ack(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        data = _make_ctpp_msg(PREFIX_VIP_EVENT, 0x12345678, ACTION_DOOR_OPENED, flags=0, addresses=["SB000006"])
        await listener._process_message(data)

        # Event fired
        cb.assert_called_once()
        assert cb.call_args[0][0].event_type == "door_opened"
        # No ACK is sent — door_opened does not require one; the device
        # retransmits briefly and stops on its own, and any ACK we send
        # for this event gets rejected.
        listener._client.send_binary.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_call_init_fires_doorbell_ring(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        data = _make_ctpp_msg(PREFIX_CALL_INIT, 0xABCD, 0, flags=0)
        await listener._process_message(data)

        cb.assert_called_once()
        assert cb.call_args[0][0].event_type == "ring"

    @pytest.mark.asyncio
    async def test_in_alerting_fires_doorbell_ring(self):
        cb = MagicMock()
        listener = _make_listener(cb)

        data = _make_ctpp_msg(PREFIX_VIP_EVENT, 0, ACTION_IN_ALERTING, flags=0)
        await listener._process_message(data)

        cb.assert_called_once()
        assert cb.call_args[0][0].event_type == "ring"

    @pytest.mark.asyncio
    async def test_renewal_ack_uses_init_ts_plus_ctr_incr(self):
        """Renewal ACK timestamp must be init_ts + 0x01010000 — PCAP-verified.

        The client derives outgoing ACK timestamps from its OWN init_ts, not
        from the device's renewal timestamp. Using the device ts causes the
        device to reject the ACK and retransmit until it gives up.
        """
        cb = MagicMock()
        init_ts = 0x12000000
        listener = _make_listener(cb, init_ts=init_ts)

        # Device renewal timestamp is completely different — listener must ignore it
        device_ts = 0xE869C888
        data = _make_ctpp_msg(PREFIX_VIP_EVENT, device_ts, ACTION_REGISTRATION_RENEWAL, flags=0)

        sent_payloads: list[bytes] = []

        async def capture_send(channel, payload):
            sent_payloads.append(payload)

        listener._client.send_binary = AsyncMock(side_effect=capture_send)
        await listener._process_message(data)

        assert len(sent_payloads) == 2
        expected_ts = (init_ts + 0x01010000) & 0xFFFFFFFF
        for payload in sent_payloads:
            actual_ts = struct.unpack_from("<I", payload, 2)[0]
            assert actual_ts == expected_ts

    @pytest.mark.asyncio
    async def test_renewal_ack_addresses_extracted_from_renewal_message(self):
        """Renewal ACK caller/callee must come from the renewal message, not config.

        The device's binary VIP address may differ from the apt-address returned
        by the JSON config API.  The renewal embeds the device's own address:
        the full address (base+subaddress) appears twice, the base address once.
        The ACK must mirror those addresses back — not the config addresses.
        """
        cb = MagicMock()
        # Config uses SB000006; device's VIP address is SB000003.
        listener = _make_listener(cb, apt_address="SB000006", apt_subaddress=1, init_ts=0x12000000)

        # Renewal from device: addr_with_sub=SB0000031 (×2), apt_addr=SB000003 (×1)
        # (mirrors what encode_ctpp_init embeds, using the device's own VIP address)
        device_renewal_addrs = ["SB0000031", "SB000003", "SB0000031"]
        data = _make_ctpp_msg(
            PREFIX_VIP_EVENT,
            0xDEADBEEF,
            ACTION_REGISTRATION_RENEWAL,
            flags=0,
            addresses=device_renewal_addrs,
        )

        sent_payloads: list[bytes] = []

        async def capture_send(channel, payload):
            sent_payloads.append(payload)

        listener._client.send_binary = AsyncMock(side_effect=capture_send)
        await listener._process_message(data)

        assert len(sent_payloads) == 2
        # Decode caller and callee from each payload.
        # ACK format: [prefix LE16][ts LE32][action BE16][0xFFFFFFFF][caller\0][callee\0\0]
        for payload in sent_payloads:
            # Skip prefix(2) + ts(4) + action(2) + 0xFFFFFFFF(4) = 12 bytes
            rest = payload[12:]
            parts = rest.split(b"\x00")
            caller = parts[0].decode("ascii")
            callee = parts[1].decode("ascii")
            assert caller == "SB0000031", f"Expected SB0000031, got {caller}"
            assert callee == "SB000003", f"Expected SB000003, got {callee}"

    @pytest.mark.asyncio
    async def test_renewal_ack_falls_back_to_config_when_no_addresses(self):
        """When renewal has no addresses, ACK falls back to config apt_address."""
        cb = MagicMock()
        listener = _make_listener(cb, apt_address="SB000006", apt_subaddress=1, init_ts=0x12000000)

        data = _make_ctpp_msg(PREFIX_VIP_EVENT, 0x12345678, ACTION_REGISTRATION_RENEWAL, flags=0)

        sent_payloads: list[bytes] = []

        async def capture_send(channel, payload):
            sent_payloads.append(payload)

        listener._client.send_binary = AsyncMock(side_effect=capture_send)
        await listener._process_message(data)

        assert len(sent_payloads) == 2
        for payload in sent_payloads:
            rest = payload[12:]
            parts = rest.split(b"\x00")
            caller = parts[0].decode("ascii")
            callee = parts[1].decode("ascii")
            assert caller == "SB0000061"
            assert callee == "SB000006"

    @pytest.mark.asyncio
    async def test_send_ack_failure_does_not_raise(self):
        """ACK send failure is logged but must not propagate."""
        cb = MagicMock()
        listener = _make_listener(cb)
        listener._client.send_binary = AsyncMock(side_effect=OSError("network error"))

        data = _make_ctpp_msg(PREFIX_VIP_EVENT, 0, ACTION_DOOR_OPENED, flags=0)
        # Must not raise
        await listener._process_message(data)

    @pytest.mark.asyncio
    async def test_call_init_no_ack_when_inbound_ring_callback_set(self):
        """With on_inbound_ring set, PREFIX_CALL_INIT must NOT send any ACK.

        The inbound answer sequence sends its own correctly-timed fresh_ts ACK
        (step 1). Sending the wrong ACK here races the answer sequence.
        """
        ring_cb = MagicMock()
        listener = _make_listener(on_inbound_ring=ring_cb)

        data = _make_ctpp_msg(PREFIX_CALL_INIT, 0xABCD1234, 0, flags=0, addresses=["SB100001"])
        await listener._process_message(data)

        listener._client.send_binary.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_call_init_calls_inbound_ring_callback_with_entrance_and_ts(self):
        """With on_inbound_ring set, it is called with (entrance_addr, ring_ts)."""
        ring_cb = MagicMock()
        listener = _make_listener(on_inbound_ring=ring_cb)

        ring_ts = 0xABCD1234
        data = _make_ctpp_msg(PREFIX_CALL_INIT, ring_ts, 0, flags=0, addresses=["SB100001"])
        await listener._process_message(data)

        ring_cb.assert_called_once_with("SB100001", ring_ts)

    @pytest.mark.asyncio
    async def test_call_init_sends_ack_when_no_inbound_callback(self):
        """Without on_inbound_ring, PREFIX_CALL_INIT sends the event ACK (legacy path)."""
        cb = MagicMock()
        listener = _make_listener(cb)  # on_inbound_ring=None by default

        data = _make_ctpp_msg(PREFIX_CALL_INIT, 0xABCD, 0, flags=0, addresses=["SB100001"])
        await listener._process_message(data)

        listener._client.send_binary.assert_awaited_once()


# ---------------------------------------------------------------------------
# VipEventListener.stop
# ---------------------------------------------------------------------------


class TestVipListenerStop:
    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        listener = _make_listener()

        async def _forever():
            await asyncio.sleep(9999)

        listener._task = asyncio.create_task(_forever())
        await listener.stop()

        assert listener._task is None

    @pytest.mark.asyncio
    async def test_stop_safe_when_no_task(self):
        listener = _make_listener()
        listener._task = None
        await listener.stop()  # must not raise


# ---------------------------------------------------------------------------
# VipEventListener._listen_loop
# ---------------------------------------------------------------------------


class TestVipListenerStart:
    @pytest.mark.asyncio
    async def test_start_raises_when_no_ctpp_channel(self):
        listener = _make_listener()
        listener._client.get_channel = MagicMock(return_value=None)

        with pytest.raises(RuntimeError, match="CTPP channel not open"):
            await listener.start()

    @pytest.mark.asyncio
    async def test_start_success_creates_task(self):
        listener = _make_listener()
        fake_channel = MagicMock()
        fake_channel.response_queue = asyncio.Queue()
        listener._client.get_channel = MagicMock(return_value=fake_channel)

        await listener.start()

        assert listener._task is not None
        assert not listener._task.done()
        # Clean up
        await listener.stop()


class TestListenLoop:
    @pytest.mark.asyncio
    async def test_listen_loop_dispatches_message(self):
        """Loop processes a queued message and fires the callback."""
        cb = MagicMock()
        listener = _make_listener(cb)
        data = _make_ctpp_msg(PREFIX_VIP_EVENT, 0, ACTION_IN_ALERTING, flags=0)
        await listener._channel.response_queue.put(data)

        task = asyncio.create_task(listener._listen_loop())
        await asyncio.sleep(0.05)  # let the loop process the message
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        cb.assert_called_once()
        assert cb.call_args[0][0].event_type == "ring"

    @pytest.mark.asyncio
    async def test_listen_loop_blocks_until_cancelled(self):
        """Loop blocks on empty queue and only exits via task cancellation."""
        listener = _make_listener()

        task = asyncio.create_task(listener._listen_loop())
        await asyncio.sleep(0)
        assert not task.done()  # still blocked on queue.get()

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert task.done()

    @pytest.mark.asyncio
    async def test_listen_loop_cancelled_exits_cleanly(self):
        """CancelledError exits the loop without propagating to the caller."""
        listener = _make_listener()

        task = asyncio.create_task(listener._listen_loop())
        await asyncio.sleep(0)
        task.cancel()
        await task  # must return normally, not raise


# ---------------------------------------------------------------------------
# _process_message — retransmit logging paths
# ---------------------------------------------------------------------------


class TestProcessMessageRetransmit:
    @pytest.mark.asyncio
    async def test_retransmit_non_video_tail_no_exception(self):
        """Non-video-tail retransmit (PREFIX_VIP_EVENT) is handled without error."""
        cb = MagicMock()
        listener = _make_listener(cb)
        listener._retransmit_window = 30.0

        ts = 0xABCDEF01
        data = _make_ctpp_msg(PREFIX_VIP_EVENT, ts, ACTION_IN_ALERTING, flags=0)

        await listener._process_message(data)
        await listener._process_message(data)

        assert cb.call_count >= 1

    @pytest.mark.asyncio
    async def test_retransmit_video_tail_no_exception(self):
        """Video-tail retransmit (PREFIX_VIDEO_EVENT) is handled without error."""
        cb = MagicMock()
        listener = _make_listener(cb)
        listener._retransmit_window = 30.0

        ts = 0xABCDEF02
        data = _make_ctpp_msg(PREFIX_VIDEO_EVENT, ts, 0x0001, flags=0)

        await listener._process_message(data)
        await listener._process_message(data)


# ---------------------------------------------------------------------------
# _send_event_ack — exception path
# ---------------------------------------------------------------------------


class TestSendEventAckException:
    @pytest.mark.asyncio
    async def test_send_event_ack_exception_does_not_propagate(self):
        """ACK send failure for a non-door-opened event is swallowed."""
        cb = MagicMock()
        listener = _make_listener(cb)
        listener._client.send_binary = AsyncMock(side_effect=OSError("net error"))

        data = _make_ctpp_msg(PREFIX_VIP_EVENT, 0x12345678, ACTION_IN_ALERTING, flags=0)
        await listener._process_message(data)  # must not raise


# ---------------------------------------------------------------------------
# _send_event_ack — payload-level: timestamp and callee correctness
# ---------------------------------------------------------------------------


class TestSendEventAckPayload:
    """Verify _send_event_ack sends transform(device_ts) and apt_addr callee.

    These tests decode the raw bytes sent to the wire and assert the correct
    values — they would have failed before the fix that changed _ack_ts to
    _transform_device_ts(msg["timestamp"]) and entrance_addr to apt_addr.
    """

    @pytest.mark.asyncio
    async def test_call_init_ack_timestamp_is_transform_of_device_ts(self):
        """PREFIX_CALL_INIT ACK timestamp == transform(device_ts), not init_ts+ctr."""
        from custom_components.comelit_man.video_call import _transform_device_ts

        init_ts = 0x12000000
        device_ts = 0xA4C6B83B
        listener = _make_listener(init_ts=init_ts)  # on_inbound_ring=None → ACK is sent

        sent_payloads: list[bytes] = []

        async def capture(channel, payload):
            sent_payloads.append(payload)

        listener._client.send_binary = AsyncMock(side_effect=capture)
        data = _make_ctpp_msg(PREFIX_CALL_INIT, device_ts, 0, flags=0)
        await listener._process_message(data)

        assert len(sent_payloads) == 1
        actual_ts = struct.unpack_from("<I", sent_payloads[0], 2)[0]
        expected_ts = _transform_device_ts(device_ts)
        wrong_ts = (init_ts + 0x01010000) & 0xFFFFFFFF
        assert actual_ts == expected_ts
        assert actual_ts != wrong_ts

    @pytest.mark.asyncio
    async def test_call_init_ack_callee_is_apt_addr_not_entrance_addr(self):
        """PREFIX_CALL_INIT ACK callee == apt_addr (base), not the entrance address."""
        apt_addr = "SB000006"
        entrance_addr = "SB100001"
        listener = _make_listener(apt_address=apt_addr, apt_subaddress=1)

        sent_payloads: list[bytes] = []

        async def capture(channel, payload):
            sent_payloads.append(payload)

        listener._client.send_binary = AsyncMock(side_effect=capture)
        data = _make_ctpp_msg(PREFIX_CALL_INIT, 0xABCD1234, 0, flags=0, addresses=[entrance_addr])
        await listener._process_message(data)

        assert len(sent_payloads) == 1
        # ACK format: prefix(2) + ts(4) + action(2) + 0xFFFFFFFF(4) = 12 bytes header
        rest = sent_payloads[0][12:]
        parts = rest.split(b"\x00")
        callee = parts[1].decode("ascii")
        assert callee == apt_addr
        assert callee != entrance_addr

    @pytest.mark.asyncio
    async def test_vip_event_ack_timestamp_is_transform_of_device_ts(self):
        """ACTION_IN_ALERTING ACK timestamp == transform(device_ts), not init_ts+ctr."""
        from custom_components.comelit_man.video_call import _transform_device_ts

        init_ts = 0x12000000
        device_ts = 0xDEADBEEF
        listener = _make_listener(init_ts=init_ts)

        sent_payloads: list[bytes] = []

        async def capture(channel, payload):
            sent_payloads.append(payload)

        listener._client.send_binary = AsyncMock(side_effect=capture)
        data = _make_ctpp_msg(PREFIX_VIP_EVENT, device_ts, ACTION_IN_ALERTING, flags=0)
        await listener._process_message(data)

        assert len(sent_payloads) == 1
        actual_ts = struct.unpack_from("<I", sent_payloads[0], 2)[0]
        expected_ts = _transform_device_ts(device_ts)
        wrong_ts = (init_ts + 0x01010000) & 0xFFFFFFFF
        assert actual_ts == expected_ts
        assert actual_ts != wrong_ts

    @pytest.mark.asyncio
    async def test_vip_event_ack_callee_is_apt_addr_not_entrance_addr(self):
        """ACTION_IN_ALERTING ACK callee == apt_addr (base), not the entrance address."""
        apt_addr = "SB000006"
        entrance_addr = "SB100001"
        listener = _make_listener(apt_address=apt_addr, apt_subaddress=1)

        sent_payloads: list[bytes] = []

        async def capture(channel, payload):
            sent_payloads.append(payload)

        listener._client.send_binary = AsyncMock(side_effect=capture)
        data = _make_ctpp_msg(PREFIX_VIP_EVENT, 0x12345678, ACTION_IN_ALERTING, flags=0, addresses=[entrance_addr])
        await listener._process_message(data)

        assert len(sent_payloads) == 1
        rest = sent_payloads[0][12:]
        parts = rest.split(b"\x00")
        callee = parts[1].decode("ascii")
        assert callee == apt_addr
        assert callee != entrance_addr


# ---------------------------------------------------------------------------
# _send_renewal_ack — exception path
# ---------------------------------------------------------------------------


class TestSendRenewalAckException:
    @pytest.mark.asyncio
    async def test_send_renewal_ack_exception_does_not_propagate(self):
        """Renewal ACK send failure is swallowed."""
        cb = MagicMock()
        listener = _make_listener(cb)
        listener._client.send_binary = AsyncMock(side_effect=OSError("net error"))

        data = _make_ctpp_msg(PREFIX_VIP_EVENT, 0x12345678, ACTION_REGISTRATION_RENEWAL, flags=0)
        await listener._process_message(data)  # must not raise


# ---------------------------------------------------------------------------
# _handle_vip_event — unknown action + video event debug path
# ---------------------------------------------------------------------------


class TestProcessMessageDebugRaw:
    @pytest.mark.asyncio
    async def test_debug_raw_logged_when_debug_enabled(self):
        """_LOGGER.debug('VIP raw: ...') fires when DEBUG logging is enabled (line 247)."""
        from custom_components.comelit_man import vip_listener as vip_module

        cb = MagicMock()
        listener = _make_listener(cb)

        data = _make_ctpp_msg(PREFIX_VIP_EVENT, 0, ACTION_IN_ALERTING, flags=0)

        with patch.object(vip_module._LOGGER, "isEnabledFor", return_value=True):
            await listener._process_message(data)

        # No exception and callback was called (event fired)
        cb.assert_called_once()


class TestHandleVipEventExtraPaths:
    def test_unknown_vip_fsm_action_does_not_fire(self):
        cb = MagicMock()
        listener = _make_listener(cb)
        msg = {
            "prefix": PREFIX_VIP_EVENT,
            "timestamp": 0,
            "action": 0x00FF,
            "flags": 0,
            "addresses": [],
        }
        listener._handle_vip_event(msg)
        cb.assert_not_called()

    def test_video_event_prefix_reaches_debug_log_path(self):
        cb = MagicMock()
        listener = _make_listener(cb)
        msg = {
            "prefix": PREFIX_VIDEO_EVENT,
            "timestamp": 0,
            "action": 0x0007,
            "flags": 0,
            "addresses": [],
        }
        listener._handle_vip_event(msg)
        cb.assert_not_called()


# ---------------------------------------------------------------------------
# on_call_idle dispatch (Phase 1: missed-call signal forwarding)
# ---------------------------------------------------------------------------


class TestOnCallIdleDispatch:
    def test_idle_frame_invokes_callback_with_addresses(self):
        """0x1840/0x0000 goes to on_call_idle with the parsed address list."""
        idle_calls = []
        listener = _make_listener(on_call_idle=idle_calls.append)
        msg = parse_ctpp_message(
            _make_ctpp_msg(PREFIX_VIDEO_EVENT, 0x1000, ACTION_IDLE, flags=0, addresses=["SB100001", "SB000006"])
        )
        listener._handle_vip_event(msg)
        assert idle_calls == [["SB100001", "SB000006"]]

    def test_idle_frame_without_callback_logs_only(self):
        """No on_call_idle configured — frame falls through to the log path."""
        cb = MagicMock()
        listener = _make_listener(callback=cb)
        msg = parse_ctpp_message(_make_ctpp_msg(PREFIX_VIDEO_EVENT, 0x1000, ACTION_IDLE, flags=0))
        listener._handle_vip_event(msg)  # must not raise
        cb.assert_not_called()

    def test_non_idle_video_event_not_forwarded(self):
        """Other 0x1840 actions (codec etc.) do not hit on_call_idle."""
        idle_calls = []
        listener = _make_listener(on_call_idle=idle_calls.append)
        msg = parse_ctpp_message(_make_ctpp_msg(PREFIX_VIDEO_EVENT, 0x1000, 0x0008, flags=0))
        listener._handle_vip_event(msg)
        assert idle_calls == []

    def test_vip_idle_not_forwarded(self):
        """0x1860 with action 0 is not a video-idle frame — not forwarded."""
        idle_calls = []
        listener = _make_listener(on_call_idle=idle_calls.append)
        msg = parse_ctpp_message(_make_ctpp_msg(PREFIX_VIP_EVENT, 0x1000, ACTION_IDLE, flags=0))
        listener._handle_vip_event(msg)
        assert idle_calls == []

    def test_callback_exception_swallowed(self):
        """An exception inside on_call_idle is logged, not raised."""

        def boom(addresses):
            raise RuntimeError("boom")

        listener = _make_listener(on_call_idle=boom)
        msg = parse_ctpp_message(_make_ctpp_msg(PREFIX_VIDEO_EVENT, 0x1000, ACTION_IDLE, flags=0))
        listener._handle_vip_event(msg)  # must not raise

    def test_ring_event_not_affected(self):
        """CALL_INIT still goes to on_inbound_ring, untouched by on_call_idle."""
        rings = []
        idle_calls = []
        listener = _make_listener(
            on_inbound_ring=lambda addr, ts: rings.append((addr, ts)),
            on_call_idle=idle_calls.append,
        )
        msg = parse_ctpp_message(_make_ctpp_msg(PREFIX_CALL_INIT, 0x2000, 0x0028, flags=0, addresses=["SB100001"]))
        listener._handle_vip_event(msg)
        assert rings == [("SB100001", 0x2000)]
        assert idle_calls == []
