"""Small helpers for validating RTP packets and locating their payload."""

from __future__ import annotations

import struct

RTP_FIXED_HEADER_SIZE = 12


def rtp_payload_bounds(packet: bytes) -> tuple[int, int] | None:
    """Return the media payload bounds for a valid RTP v2 packet.

    The returned end offset excludes RTP padding.  ``None`` indicates a
    truncated or otherwise malformed packet.
    """
    packet_size = len(packet)
    if packet_size < RTP_FIXED_HEADER_SIZE:
        return None

    first_byte = packet[0]
    if first_byte >> 6 != 2:
        return None

    payload_start = RTP_FIXED_HEADER_SIZE + (first_byte & 0x0F) * 4
    if payload_start > packet_size:
        return None

    if first_byte & 0x10:
        if payload_start + 4 > packet_size:
            return None
        extension_words = struct.unpack_from("!H", packet, payload_start + 2)[0]
        payload_start += 4 + extension_words * 4
        if payload_start > packet_size:
            return None

    payload_end = packet_size
    if first_byte & 0x20:
        padding_size = packet[-1]
        if padding_size == 0 or padding_size > packet_size - payload_start:
            return None
        payload_end -= padding_size

    return payload_start, payload_end
