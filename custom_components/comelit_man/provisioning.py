"""Create a dedicated device user and mint its ViP token, entirely locally.

Why this exists: a token lifted from the device's backup belongs to whichever
identity already owns it — typically the phone running the Comelit app.  Two
listeners sharing one ViP identity kick each other off, so Home Assistant
should hold its own.

The flow, all on the LAN:

1. Log in to the device web UI (port 8080).
2. Read the user table from a configuration backup and pick a free slot.
3. Create an "Apps"-class user in that slot.
4. Ask the device to generate an activation code for it.
5. Redeem the code on the UAUT channel (port 64100), which returns the token.

Slot discovery deliberately reads the backup rather than probing per-slot
pairing files: an already-activated user has no pending pairing file, so a
probe would report its slot as free and overwrite a real user.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import NamedTuple
from urllib.parse import quote

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .exceptions import TokenExtractionError
from .token import download_latest_backup, login, read_users_cfg

_LOGGER = logging.getLogger(__name__)

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=10)
_ACTIVATION_TIMEOUT = 8.0

# users.cfg layout (verified on 6701W fw 2.x):
#   mspUsersMap.<map>.<slot> = 4:2:<a> 5:2:<kind> 6:4:"<name>" ...
#                              9:4:"<token>" ... 11:4:"<email>" ...
# Field 6 is the display name, 9 the 32-hex ViP token, 11 the email.  A slot
# with neither a name nor a token is free; that test is used rather than the
# type fields, whose semantics vary (an empty slot was observed carrying the
# same type value as an occupied one).
_SLOT_RE = re.compile(r"mspUsersMap\.(\d+)\.(\d+)\s*=\s*(.*)")
_FIELD_RE = re.compile(r"(\d+):(\d+):(?:\"([^\"]*)\"|(\S+))")

# Slot 0 belongs to the wall monitor / primary internal unit.  Never claim it.
_RESERVED_SLOT = 0

# Field semantics read off the device's users.html controls:
#   field 4  — 1 enabled / 2 disabled  ("#" checkbox)
#   field 5  — device type: 0 none, 1 Internal Unit, 2 Apps, 3 Phone
#   field 6  — description (name), 11 contact email, 12 contact phone
#   field 18 — the activation code, once generated
_FIELD_ENABLED = 4
_FIELD_KIND = 5
_FIELD_NAME = 6
_FIELD_ACTIVATION_CODE = 18

_ENABLED = 1
_USER_KIND_APPS = 2  # app-class identity (what a phone or HA uses)


class UserSlot(NamedTuple):
    """One entry in the device's user table."""

    map_index: int
    slot: int
    name: str
    token: str
    email: str
    activation_code: str = ""

    @property
    def is_free(self) -> bool:
        """True when nothing occupies this slot."""
        return not self.name and not self.token

    @property
    def path(self) -> str:
        """Slot identifier as the web UI addresses it (e.g. "0.3").

        Read off the device's own users.html, which emits
        `update.html?mspUsersMap_.0.2_5=` and `create-actcode.html?user=0.2`.
        """
        return f"{self.map_index}.{self.slot}"


def parse_user_slots(users_cfg: str) -> list[UserSlot]:
    """Parse users.cfg into slot records."""
    slots: list[UserSlot] = []
    for line in users_cfg.splitlines():
        match = _SLOT_RE.match(line.strip())
        if not match:
            continue
        map_index, slot = int(match.group(1)), int(match.group(2))
        fields: dict[int, str] = {}
        for field_match in _FIELD_RE.finditer(match.group(3)):
            index = int(field_match.group(1))
            fields[index] = field_match.group(3) if field_match.group(3) is not None else field_match.group(4) or ""
        slots.append(
            UserSlot(
                map_index=map_index,
                slot=slot,
                name=fields.get(6, ""),
                token=fields.get(9, ""),
                email=fields.get(11, ""),
                activation_code=fields.get(18, ""),
            )
        )
    return slots


def find_free_slot(slots: list[UserSlot]) -> UserSlot:
    """Return the first free slot, never the reserved wall-monitor slot."""
    for entry in slots:
        if entry.slot == _RESERVED_SLOT:
            continue
        if entry.is_free:
            return entry
    raise TokenExtractionError(
        f"No free user slot on the device ({len(slots)} slots, all occupied). "
        "Remove an unused user in the device web UI and retry."
    )


async def provision_user(
    host: str,
    password: str | None = None,
    http_port: int = 8080,
    port: int = 64100,
    hass: HomeAssistant | None = None,
    name: str = "Home Assistant",
) -> str:
    """Create a dedicated user on the device and return its ViP token."""
    if password is None:
        password = "comelit"
    base_url = f"http://{host}:{http_port}"
    if hass is not None:
        session = async_get_clientsession(hass)
        return await _provision(session, base_url, host, port, password, name)
    async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
        return await _provision(session, base_url, host, port, password, name)


async def _provision(
    session: aiohttp.ClientSession,
    base_url: str,
    host: str,
    port: int,
    password: str,
    name: str,
) -> str:
    """Run the provisioning steps."""
    await login(session, base_url, password)

    archive = await download_latest_backup(session, base_url)
    slots = parse_user_slots(read_users_cfg(archive))
    if not slots:
        raise TokenExtractionError("Could not read the device user table")
    target = find_free_slot(slots)
    _LOGGER.info("Provisioning user %r in free slot %s", name, target.path)

    await _create_user(session, base_url, target, name)
    code = await _generate_activation_code(session, base_url, target)
    _LOGGER.debug("Activation code obtained for slot %s", target.path)

    token = await _redeem_activation_code(host, port, code, name)
    _LOGGER.info("Provisioned dedicated user %r in slot %s", name, target.path)
    return token


async def _update_field(
    session: aiohttp.ClientSession,
    base_url: str,
    target: UserSlot,
    field: int,
    value: str,
) -> None:
    """Set one field on a user slot."""
    # Build the query manually: aiohttp encodes spaces as "+", and the device
    # stores that literally (a name came back as "HA+Provision+Test").
    query = f"mspUsersMap_.{target.path}_{field}={quote(value, safe='')}"
    async with session.post(
        f"{base_url}/update.html?{query}",
        headers={"Referer": f"{base_url}/users.html"},
        timeout=_REQUEST_TIMEOUT,
    ) as resp:
        if resp.status != 200:
            raise TokenExtractionError(f"Setting user field {field} failed with status {resp.status}")


async def _create_user(
    session: aiohttp.ClientSession,
    base_url: str,
    target: UserSlot,
    name: str,
) -> None:
    """Populate a free slot with an enabled app-class user."""
    await _update_field(session, base_url, target, _FIELD_KIND, str(_USER_KIND_APPS))
    await _update_field(session, base_url, target, _FIELD_NAME, name)
    await _update_field(session, base_url, target, _FIELD_ENABLED, str(_ENABLED))


async def _generate_activation_code(
    session: aiohttp.ClientSession,
    base_url: str,
    target: UserSlot,
) -> str:
    """Ask the device to generate an activation code, then read it back.

    The pending code is rendered on the users page next to the slot's
    activation status — it is *not* written to the user table (field 18 holds
    a longer value on already-activated users and stays empty here), and this
    firmware has no `user-file.mug` endpoint: every form of that request
    answers "Invalid request", with HTTP 200 and an HTML body, so a status
    check would not notice.
    """
    async with session.post(
        f"{base_url}/create-actcode.html",
        params={"user": target.path},
        headers={"Referer": f"{base_url}/users.html"},
        timeout=_REQUEST_TIMEOUT,
    ) as resp:
        body = await resp.text()
        if resp.status != 200 or "Invalid request" in body:
            raise TokenExtractionError(f"Activation code generation failed (HTTP {resp.status}) for slot {target.path}")

    async with session.get(f"{base_url}/users.html", timeout=_REQUEST_TIMEOUT) as resp:
        if resp.status != 200:
            raise TokenExtractionError(f"Reading the users page failed with status {resp.status}")
        html = await resp.text()

    code = _extract_activation_code(html, target)
    if not code:
        raise TokenExtractionError(f"Device generated no activation code for slot {target.path}")
    return code


def _extract_activation_code(html: str, target: UserSlot) -> str:
    """Pull the pending activation code for a slot out of the users page.

    Every row carries its own enable checkbox (`mspUsersMap_.<slot>_4=`), so
    that is used to bound the row; the pending code is rendered inside a
    <strong> in the status cell.  Anchoring on the "Generate activation code"
    button instead does not work — once a code is pending, that button is no
    longer emitted for the row.
    """
    row_start = html.find(f"mspUsersMap_.{target.path}_4=")
    if row_start == -1:
        return ""
    next_row = html.find("_4=", row_start + len(f"mspUsersMap_.{target.path}_4="))
    row = html[row_start : next_row if next_row != -1 else len(html)]
    match = re.search(r"<strong>([^<]*)</strong>", row)
    return match.group(1).strip() if match else ""


async def _redeem_activation_code(host: str, port: int, code: str, description: str) -> str:
    """Redeem an activation code on the UAUT channel and return the token."""
    # Local import: client imports protocol, and provisioning is imported by
    # the config flow before the coordinator exists.
    from .channels import ChannelType
    from .client import IconaBridgeClient

    client = IconaBridgeClient(host, port)
    try:
        await asyncio.wait_for(client.connect(), timeout=_ACTIVATION_TIMEOUT)
        channel = await client.open_channel("UAUT", ChannelType.UAUT)
        response = await asyncio.wait_for(
            client.send_json(
                channel,
                {
                    "message": "user-activation",
                    "activation-code": code,
                    "description": description,
                    "message-type": "request",
                    "message-id": 2,
                },
            ),
            timeout=_ACTIVATION_TIMEOUT,
        )
    except TokenExtractionError:
        raise
    except Exception as err:
        raise TokenExtractionError(f"Activation request failed: {type(err).__name__}: {err}") from err
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()

    if response.get("response-code") != 200:
        raise TokenExtractionError(f"Activation rejected by device: {response.get('response-string', response)}")
    token = response.get("user-token")
    if not token:
        raise TokenExtractionError("Device accepted the activation code but returned no token")
    return str(token)
