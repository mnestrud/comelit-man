"""Privacy-safe INFO channel metadata discovery."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

from .channels import ChannelType, ViperMessageId
from .client import IconaBridgeClient
from .exceptions import ProtocolError


class ServerCapability(StrEnum):
    """Known, non-identifying capabilities advertised by the INFO channel."""

    USER_AUTH = "user-auth-channel"
    CONFIGURATION = "configuration-channel"
    PUSH_NOTIFICATIONS = "push-notifications-channel"
    RTSP_OVER_VIPER = "rtsp-over-viper-channel"


@dataclass(frozen=True, slots=True)
class ServerInfo:
    """Allowlisted, non-identifying metadata returned by ``server-info``."""

    model: str | None = None
    firmware_version: str | None = None
    capabilities: frozenset[ServerCapability] = frozenset()
    user_auth_encryption_required: bool | None = None
    configuration_api_version: int | None = None
    configuration_direct_link: bool | None = None
    configuration_internal_unit: bool | None = None
    configuration_internal_unit_buttons: bool | None = None


def _as_mapping(value: object) -> Mapping[str, object]:
    """Return a mapping for a JSON object, or an empty mapping otherwise."""
    return value if isinstance(value, dict) else {}


def _optional_string(data: Mapping[str, object], key: str) -> str | None:
    """Return an optional non-empty string without coercing other JSON types."""
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _optional_bool(data: Mapping[str, object], key: str) -> bool | None:
    """Return an optional JSON boolean without coercion."""
    value = data.get(key)
    return value if isinstance(value, bool) else None


def _optional_int(data: Mapping[str, object], key: str) -> int | None:
    """Return an optional JSON integer, excluding booleans."""
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_server_info_response(response: Mapping[str, object]) -> ServerInfo:
    """Parse an INFO response into a strict allowlist.

    Serial numbers, addresses, host information, directory data, unknown
    capability names, and the raw response are deliberately discarded.
    """
    if (
        response.get("message") != "server-info"
        or response.get("message-id") != int(ViperMessageId.SERVER_INFO)
        or response.get("message-type") != "response"
        or response.get("response-code") != 200
    ):
        raise ProtocolError("Device returned an invalid server-info response")

    capabilities: set[ServerCapability] = set()
    raw_capabilities = response.get("capabilities")
    if isinstance(raw_capabilities, list):
        for value in raw_capabilities:
            if not isinstance(value, str):
                continue
            try:
                capabilities.add(ServerCapability(value))
            except ValueError:
                continue

    user_auth = _as_mapping(response.get("user-auth-channel"))
    configuration = _as_mapping(response.get("configuration-channel"))

    return ServerInfo(
        model=_optional_string(response, "model"),
        firmware_version=_optional_string(response, "version"),
        capabilities=frozenset(capabilities),
        user_auth_encryption_required=_optional_bool(user_auth, "encryption-required"),
        configuration_api_version=_optional_int(configuration, "api-version"),
        configuration_direct_link=_optional_bool(configuration, "direct-link-cfg"),
        configuration_internal_unit=_optional_bool(configuration, "internal-unit-cfg"),
        configuration_internal_unit_buttons=_optional_bool(configuration, "iu-buttons-cfg"),
    )


async def get_server_info(client: IconaBridgeClient) -> ServerInfo:
    """Query allowlisted metadata over an already authenticated connection.

    The caller must first authenticate this same client through the normal UAUT
    flow. This function does not connect, discover devices, or accept a target;
    it can only query the host already configured on ``client``.
    """
    if not client.authenticated or client.get_channel("UAUT") is None:
        raise ProtocolError("Server info requires successful UAUT authentication")

    channel = client.get_channel("INFO")
    opened_here = channel is None
    if channel is None:
        try:
            channel = await client.open_channel("INFO", ChannelType.INFO)
        except BaseException:
            client.remove_channel("INFO")
            raise

    try:
        response = await client.send_json(
            channel,
            {
                "message": "server-info",
                "message-type": "request",
                "message-id": int(ViperMessageId.SERVER_INFO),
            },
        )
        return parse_server_info_response(response)
    finally:
        if opened_here:
            # The metadata result or the original query exception is more
            # useful than a secondary best-effort channel-close failure.
            with contextlib.suppress(Exception):
                await client.close_channel("INFO")
