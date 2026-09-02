"""Tests for privacy-safe INFO channel metadata discovery."""

from __future__ import annotations

from dataclasses import fields
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.comelit_man.channels import Channel, ChannelType, ViperMessageId
from custom_components.comelit_man.client import IconaBridgeClient
from custom_components.comelit_man.exceptions import ProtocolError
from custom_components.comelit_man.info import (
    ServerCapability,
    ServerInfo,
    get_server_info,
    parse_server_info_response,
)


def _open_channel(name: str, channel_type: ChannelType, channel_id: int) -> Channel:
    channel = Channel(name=name, channel_type=channel_type, request_id=1)
    channel.server_channel_id = channel_id
    channel.is_open = True
    return channel


def _synthetic_response() -> dict:
    return {
        "message": "server-info",
        "message-id": int(ViperMessageId.SERVER_INFO),
        "message-type": "response",
        "response-code": 200,
        "response-string": "OK",
        "model": "SyntheticModel",
        "version": "9.8.7-test",
        "serial-code": "PRIVATE-SERIAL-MARKER",
        "host": "private-unit.invalid",
        "vip-address": "PRIVATE-VIP-MARKER",
        "directory": [{"name": "PRIVATE-DIRECTORY-MARKER"}],
        "capabilities": [
            "user-auth-channel",
            "configuration-channel",
            "push-notifications-channel",
            "rtsp-over-viper-channel",
            "private-site-capability",
        ],
        "user-auth-channel": {
            "encryption-required": False,
            "token": "PRIVATE-TOKEN-MARKER",
        },
        "configuration-channel": {
            "api-version": 7,
            "direct-link-cfg": True,
            "internal-unit-cfg": False,
            "iu-buttons-cfg": True,
            "apartment-address": "PRIVATE-ADDRESS-MARKER",
        },
        "private-extension": {
            "targets": ["PRIVATE-TARGET-MARKER"],
        },
    }


def test_parse_server_info_returns_only_allowlisted_metadata():
    info = parse_server_info_response(_synthetic_response())

    assert info == ServerInfo(
        model="SyntheticModel",
        firmware_version="9.8.7-test",
        capabilities=frozenset(
            {
                ServerCapability.USER_AUTH,
                ServerCapability.CONFIGURATION,
                ServerCapability.PUSH_NOTIFICATIONS,
                ServerCapability.RTSP_OVER_VIPER,
            }
        ),
        user_auth_encryption_required=False,
        configuration_api_version=7,
        configuration_direct_link=True,
        configuration_internal_unit=False,
        configuration_internal_unit_buttons=True,
    )

    retained = repr(info)
    for private_marker in (
        "PRIVATE-SERIAL-MARKER",
        "private-unit.invalid",
        "PRIVATE-VIP-MARKER",
        "PRIVATE-DIRECTORY-MARKER",
        "private-site-capability",
        "PRIVATE-TOKEN-MARKER",
        "PRIVATE-ADDRESS-MARKER",
        "PRIVATE-TARGET-MARKER",
    ):
        assert private_marker not in retained

    assert "raw" not in {field.name for field in fields(ServerInfo)}
    assert "serial" not in {field.name for field in fields(ServerInfo)}


def test_parse_server_info_does_not_coerce_malformed_capability_values():
    response = _synthetic_response()
    response["model"] = 123
    response["version"] = False
    response["capabilities"] = ["configuration-channel", 20, {"name": "user-auth-channel"}]
    response["user-auth-channel"] = {"encryption-required": "false"}
    response["configuration-channel"] = {
        "api-version": True,
        "direct-link-cfg": 1,
    }
    response["private-extension"] = "not-an-object"

    info = parse_server_info_response(response)

    assert info.model is None
    assert info.firmware_version is None
    assert info.capabilities == frozenset({ServerCapability.CONFIGURATION})
    assert info.user_auth_encryption_required is None
    assert info.configuration_api_version is None
    assert info.configuration_direct_link is None


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"message": "other", "response-code": 200},
        {
            "message": "server-info",
            "message-id": 4,
            "message-type": "response",
            "response-code": 200,
        },
        {"message": "server-info", "response-code": 403, "response-string": "PRIVATE-REASON-MARKER"},
    ],
)
def test_parse_server_info_rejects_invalid_responses_without_echoing_data(response):
    with pytest.raises(ProtocolError, match="invalid server-info") as exc_info:
        parse_server_info_response(response)

    assert "PRIVATE-REASON-MARKER" not in str(exc_info.value)


async def test_get_server_info_uses_authenticated_client_and_closes_owned_channel():
    uaut = _open_channel("UAUT", ChannelType.UAUT, 41)
    info_channel = _open_channel("INFO", ChannelType.INFO, 42)
    client = MagicMock(spec=IconaBridgeClient)
    client.authenticated = True
    client.get_channel.side_effect = lambda name: uaut if name == "UAUT" else None
    client.open_channel = AsyncMock(return_value=info_channel)
    client.send_json = AsyncMock(return_value=_synthetic_response())
    client.close_channel = AsyncMock()

    result = await get_server_info(client)

    assert result.model == "SyntheticModel"
    client.open_channel.assert_awaited_once_with("INFO", ChannelType.INFO)
    client.send_json.assert_awaited_once_with(
        info_channel,
        {
            "message": "server-info",
            "message-type": "request",
            "message-id": 20,
        },
    )
    client.close_channel.assert_awaited_once_with("INFO")


async def test_get_server_info_requires_successful_uaut_authentication():
    uaut = _open_channel("UAUT", ChannelType.UAUT, 41)
    client = MagicMock(spec=IconaBridgeClient)
    client.authenticated = False
    client.get_channel.return_value = uaut
    client.open_channel = AsyncMock()
    client.send_json = AsyncMock()

    with pytest.raises(ProtocolError, match="successful UAUT authentication"):
        await get_server_info(client)

    client.open_channel.assert_not_awaited()
    client.send_json.assert_not_awaited()


async def test_get_server_info_reuses_existing_info_channel():
    uaut = _open_channel("UAUT", ChannelType.UAUT, 41)
    info_channel = _open_channel("INFO", ChannelType.INFO, 42)
    client = MagicMock(spec=IconaBridgeClient)
    client.authenticated = True
    client.get_channel.side_effect = lambda name: {"UAUT": uaut, "INFO": info_channel}.get(name)
    client.open_channel = AsyncMock()
    client.send_json = AsyncMock(return_value=_synthetic_response())
    client.close_channel = AsyncMock()

    await get_server_info(client)

    client.open_channel.assert_not_awaited()
    client.close_channel.assert_not_awaited()


async def test_get_server_info_returns_result_when_owned_channel_close_fails():
    uaut = _open_channel("UAUT", ChannelType.UAUT, 41)
    info_channel = _open_channel("INFO", ChannelType.INFO, 42)
    client = MagicMock(spec=IconaBridgeClient)
    client.authenticated = True
    client.get_channel.side_effect = lambda name: uaut if name == "UAUT" else None
    client.open_channel = AsyncMock(return_value=info_channel)
    client.send_json = AsyncMock(return_value=_synthetic_response())
    client.close_channel = AsyncMock(side_effect=ProtocolError("PRIVATE-CLOSE-MARKER"))

    result = await get_server_info(client)

    assert result.model == "SyntheticModel"
    assert "PRIVATE-CLOSE-MARKER" not in repr(result)
    client.close_channel.assert_awaited_once_with("INFO")


async def test_get_server_info_preserves_query_error_when_owned_channel_close_fails():
    uaut = _open_channel("UAUT", ChannelType.UAUT, 41)
    info_channel = _open_channel("INFO", ChannelType.INFO, 42)
    client = MagicMock(spec=IconaBridgeClient)
    client.authenticated = True
    client.get_channel.side_effect = lambda name: uaut if name == "UAUT" else None
    client.open_channel = AsyncMock(return_value=info_channel)
    client.send_json = AsyncMock(side_effect=ProtocolError("original query failure"))
    client.close_channel = AsyncMock(side_effect=ProtocolError("secondary close failure"))

    with pytest.raises(ProtocolError, match="original query failure") as exc_info:
        await get_server_info(client)

    assert "secondary close failure" not in str(exc_info.value)
    client.close_channel.assert_awaited_once_with("INFO")


async def test_get_server_info_removes_info_registration_when_open_fails():
    uaut = _open_channel("UAUT", ChannelType.UAUT, 41)
    client = MagicMock(spec=IconaBridgeClient)
    client.authenticated = True
    client.get_channel.side_effect = lambda name: uaut if name == "UAUT" else None
    client.open_channel = AsyncMock(side_effect=ProtocolError("synthetic open failure"))

    with pytest.raises(ProtocolError, match="synthetic open failure"):
        await get_server_info(client)

    client.remove_channel.assert_called_once_with("INFO")
    client.send_json.assert_not_awaited()
