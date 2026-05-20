"""Unit tests for Channel dataclass."""

from __future__ import annotations

import asyncio

from custom_components.comelit_man.channels import Channel, ChannelType


class TestChannelNextSequence:
    def test_initial_sequence_is_one(self):
        ch = Channel(name="CTPP", channel_type=ChannelType.CTPP, request_id=1)
        assert ch.sequence == 1

    def test_next_sequence_returns_current_and_advances_by_two(self):
        ch = Channel(name="CTPP", channel_type=ChannelType.CTPP, request_id=1)
        seq1 = ch.next_sequence()
        assert seq1 == 1
        assert ch.sequence == 3

    def test_next_sequence_advances_monotonically(self):
        ch = Channel(name="CTPP", channel_type=ChannelType.CTPP, request_id=1)
        seq1 = ch.next_sequence()
        seq2 = ch.next_sequence()
        seq3 = ch.next_sequence()
        assert seq1 == 1
        assert seq2 == 3
        assert seq3 == 5
        assert ch.sequence == 7

    def test_default_fields_are_independent_between_instances(self):
        ch1 = Channel(name="A", channel_type=ChannelType.CTPP, request_id=1)
        ch2 = Channel(name="B", channel_type=ChannelType.CTPP, request_id=2)
        ch1.next_sequence()
        assert ch1.sequence == 3
        assert ch2.sequence == 1  # ch2 unaffected

    def test_open_event_is_asyncio_event(self):
        ch = Channel(name="CTPP", channel_type=ChannelType.CTPP, request_id=1)
        assert isinstance(ch.open_event, asyncio.Event)

    def test_response_queue_is_asyncio_queue(self):
        ch = Channel(name="CTPP", channel_type=ChannelType.CTPP, request_id=1)
        assert isinstance(ch.response_queue, asyncio.Queue)
