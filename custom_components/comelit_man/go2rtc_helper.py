"""Access to the go2rtc server that Home Assistant manages.

On docker/Container installs HA runs its own go2rtc binary which listens on a
unix socket with random Basic-auth credentials (no TCP port, so the classic
``http://127.0.0.1:1984`` endpoint is dead).  The authenticated
``ClientSession`` and base URL are stored in ``hass.data`` under the
``go2rtc`` ``HassKey`` by ``homeassistant.components.go2rtc``; use that when
available, and fall back to the legacy TCP API otherwise (e.g. a user-run
go2rtc bound to 127.0.0.1:1984).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.util.hass_dict import HassKey

if TYPE_CHECKING:
    import aiohttp
    from homeassistant.core import HomeAssistant

DATA_GO2RTC = HassKey("go2rtc")
LEGACY_API_URL = "http://127.0.0.1:1984"


def get_go2rtc(
    hass: HomeAssistant,
    fallback_session: aiohttp.ClientSession,
) -> tuple[aiohttp.ClientSession, str]:
    """Return (session, base_url) for talking to the go2rtc API.

    Prefers HA's managed go2rtc instance (unix socket + auth) when HA has
    one; otherwise uses the shared HA client session against the legacy
    TCP endpoint.
    """
    config = hass.data.get(DATA_GO2RTC)
    if config is not None and getattr(config, "session", None) is not None:
        return config.session, config.url.rstrip("/")
    return fallback_session, LEGACY_API_URL
