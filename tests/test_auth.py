"""Unit tests for authentication flow."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.comelit_man.auth import authenticate
from custom_components.comelit_man.channels import Channel, ChannelType, ViperMessageId
from custom_components.comelit_man.client import IconaBridgeClient
from custom_components.comelit_man.exceptions import AuthenticationError, ConnectionComelitError


def _auth_response(code: int = 200, reason: str = "OK") -> dict[str, object]:
    """Return a complete synthetic UAUT response envelope."""
    return {
        "message": "access",
        "message-id": int(ViperMessageId.UAUT),
        "message-type": "response",
        "response-code": code,
        "response-string": reason,
    }


def _open_uaut_channel(channel_id: int = 42) -> Channel:
    """Return an open synthetic UAUT channel."""
    channel = Channel(name="UAUT", channel_type=ChannelType.UAUT, request_id=1)
    channel.server_channel_id = channel_id
    channel.is_open = True
    return channel


class TestAuthenticate:
    @pytest.mark.asyncio
    async def test_authenticate_success(self):
        """authenticate() succeeds when response-code is 200."""
        channel = MagicMock()
        client = IconaBridgeClient("device.invalid")
        client._connected = True
        client.open_channel = AsyncMock(return_value=channel)
        client.send_json = AsyncMock(return_value=_auth_response())

        await authenticate(client, "abcdef1234567890abcdef1234567890")

        client.open_channel.assert_called_once_with("UAUT", client.open_channel.call_args[0][1])
        client.send_json.assert_called_once()
        msg = client.send_json.call_args[0][1]
        assert msg["message"] == "access"
        assert msg["user-token"] == "abcdef1234567890abcdef1234567890"
        assert client.authenticated

    @pytest.mark.asyncio
    async def test_authenticate_raises_on_non_200(self):
        """authenticate() raises AuthenticationError when response-code != 200."""
        channel = MagicMock()
        client = IconaBridgeClient("device.invalid")
        client._connected = True
        client._set_authenticated(True)
        client.open_channel = AsyncMock(return_value=channel)
        client.send_json = AsyncMock(return_value=_auth_response(403, "Forbidden"))

        with pytest.raises(AuthenticationError, match="403"):
            await authenticate(client, "sometoken")

        assert not client.authenticated

    @pytest.mark.asyncio
    async def test_authenticate_raises_on_missing_code(self):
        """authenticate() raises AuthenticationError when response-code is absent (defaults to 0)."""
        channel = MagicMock()
        client = MagicMock()
        client.open_channel = AsyncMock(return_value=channel)
        client.send_json = AsyncMock(
            return_value={
                "message": "access",
                "message-id": int(ViperMessageId.UAUT),
                "message-type": "response",
            }
        )

        with pytest.raises(AuthenticationError, match="Authentication failed"):
            await authenticate(client, "sometoken")

    @pytest.mark.asyncio
    async def test_authenticate_includes_reason_in_error(self):
        """AuthenticationError message includes response-string from device."""
        channel = MagicMock()
        client = MagicMock()
        client.open_channel = AsyncMock(return_value=channel)
        client.send_json = AsyncMock(return_value=_auth_response(401, "Invalid token"))

        with pytest.raises(AuthenticationError, match="Invalid token"):
            await authenticate(client, "badtoken")

    @pytest.mark.asyncio
    async def test_authenticate_sends_correct_channel_type(self):
        """authenticate() opens the UAUT channel."""
        channel = MagicMock()
        client = MagicMock()
        client.open_channel = AsyncMock(return_value=channel)
        client.send_json = AsyncMock(return_value=_auth_response())

        await authenticate(client, "token")

        client.open_channel.assert_called_once_with("UAUT", ChannelType.UAUT)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("message", "not-access"),
            ("message", None),
            ("message-id", 999),
            ("message-id", None),
            ("message-type", "request"),
            ("message-type", None),
        ],
    )
    @pytest.mark.asyncio
    async def test_authenticate_rejects_invalid_success_envelope(self, field, value):
        """A 200 response is not authentication without the full UAUT envelope."""
        client = IconaBridgeClient("device.invalid")
        client._connected = True
        client._set_authenticated(True)
        client.open_channel = AsyncMock(return_value=_open_uaut_channel())
        response = _auth_response()
        if value is None:
            response.pop(field)
        else:
            response[field] = value
        client.send_json = AsyncMock(return_value=response)

        with pytest.raises(AuthenticationError, match="invalid response"):
            await authenticate(client, "synthetic-token")

        assert not client.authenticated

    @pytest.mark.parametrize("failure_point", ["open", "send"])
    @pytest.mark.asyncio
    async def test_authenticate_exception_leaves_state_false(self, failure_point):
        """Open and send failures must clear any prior authenticated state."""
        client = IconaBridgeClient("device.invalid")
        client._connected = True
        client._set_authenticated(True)
        if failure_point == "open":
            client.open_channel = AsyncMock(side_effect=ConnectionComelitError("synthetic open failure"))
            client.send_json = AsyncMock()
        else:
            client.open_channel = AsyncMock(return_value=_open_uaut_channel())
            client.send_json = AsyncMock(side_effect=ConnectionComelitError("synthetic send failure"))

        with pytest.raises(ConnectionComelitError):
            await authenticate(client, "synthetic-token")

        assert not client.authenticated

    @pytest.mark.asyncio
    async def test_authenticate_never_logs_token_or_raw_response(self, caplog):
        """Authentication logs contain framing metadata, never request or response secrets."""
        token = "SYNTHETIC-PRIVATE-TOKEN-MARKER"
        channel = _open_uaut_channel()
        client = IconaBridgeClient("device.invalid")
        client._connected = True
        writer = MagicMock()
        writer.drain = AsyncMock()
        client._writer = writer
        client.open_channel = AsyncMock(return_value=channel)

        response = _auth_response()
        response["echoed-secret"] = token
        response_body = json.dumps(response, separators=(",", ":")).encode()

        async def respond() -> None:
            for _ in range(100):
                if channel.server_channel_id in client._callbacks:
                    client._dispatch(channel.server_channel_id, response_body)
                    return
                await asyncio.sleep(0)
            pytest.fail("Authentication callback was not registered")

        response_task = asyncio.create_task(respond())
        with caplog.at_level(logging.DEBUG):
            await authenticate(client, token)
        await response_task

        assert client.authenticated
        assert token not in caplog.text
