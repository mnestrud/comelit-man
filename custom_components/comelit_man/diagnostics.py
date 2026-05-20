"""Diagnostics support for Comelit Man."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import ComelitLocalConfigEntry

REDACT_KEYS = {"token", "password"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ComelitLocalConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "config": async_redact_data(dict(entry.data), REDACT_KEYS),
        "device_config": (
            {
                "apt_address": coordinator.device_config.apt_address,
                "apt_subaddress": coordinator.device_config.apt_subaddress,
                "caller_address": coordinator.device_config.caller_address,
                "door_count": len(coordinator.device_config.doors),
                "camera_count": len(coordinator.device_config.cameras),
            }
            if coordinator.device_config
            else None
        ),
        "connection": {
            "connected": bool(coordinator._client and coordinator._client.connected),
            "connection_lost": coordinator._connection_lost,
            "vip_listener_active": coordinator._vip_listener is not None,
        },
        "video": {
            "session_active": bool(
                coordinator.video_session and coordinator.video_session.active
            ),
            "rtsp_url": coordinator.rtsp_url,
            "rtsp_server_running": coordinator.rtsp_server is not None,
        },
    }
