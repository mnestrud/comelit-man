"""RTSP camera URL discovery from device configuration."""

from __future__ import annotations

import logging
from urllib.parse import urlparse, urlunparse

from .models import Camera, DeviceConfig

_LOGGER = logging.getLogger(__name__)


def get_cameras(config: DeviceConfig) -> list[Camera]:
    """Return all cameras from the device configuration."""
    return config.cameras


def get_rtsp_url(camera: Camera, device_host: str | None = None) -> str:
    """Get the full RTSP URL for a camera, optionally fixing the host.

    Some devices return RTSP URLs with internal addresses. If device_host
    is provided, the URL host will be replaced with it.
    """
    url = camera.rtsp_url
    if not url:
        return ""

    if device_host:
        parsed = urlparse(url)
        # Replace the hostname but keep the port if present
        if parsed.hostname:
            netloc = device_host
            if parsed.port:
                netloc = f"{device_host}:{parsed.port}"
            if camera.rtsp_user:
                auth = camera.rtsp_user
                if camera.rtsp_password:
                    auth = f"{camera.rtsp_user}:{camera.rtsp_password}"
                netloc = f"{auth}@{netloc}"
            url = urlunparse(parsed._replace(netloc=netloc))

    return url
