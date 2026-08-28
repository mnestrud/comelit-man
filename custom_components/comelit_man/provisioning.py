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
import json
import logging
import re
import struct
from typing import Any, NamedTuple

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .exceptions import TokenExtractionError
from .protocol import HEADER_MAGIC, MessageType
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

_USER_KIND_APPS = 2  # field 5: app-class identity (what a phone or HA uses)


class UserSlot(NamedTuple):
    """One entry in the device's user table."""

    map_index: int
    slot: int
    name: str
    token: str
    email: str

    @property
    def is_free(self) -> bool:
        """True when nothing occupies this slot."""
        return not self.name and not self.token

    @property
    def path(self) -> str:
        """Slot identifier as the web UI addresses it (e.g. "0_3")."""
        return f"{self.map_index}_{self.slot}"


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
    params = {f"mspUsersMap_.{target.path}_{field}": value}
    async with session.post(
        f"{base_url}/update.html",
        params=params,
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
    """Populate a free slot with an app-class user."""
    await _update_field(session, base_url, target, 5, str(_USER_KIND_APPS))
    await _update_field(session, base_url, target, 6, name)


async def _generate_activation_code(
    session: aiohttp.ClientSession,
    base_url: str,
    target: UserSlot,
) -> str:
    """Ask the device for an activation code and read it back."""
    async with session.post(
        f"{base_url}/create-actcode.html",
        params={"user": target.path},
        headers={"Referer": f"{base_url}/users.html"},
        timeout=_REQUEST_TIMEOUT,
    ) as resp:
        if resp.status != 200:
            raise TokenExtractionError(f"Activation code generation failed with status {resp.status}")

    async with session.get(
        f"{base_url}/user-file.mug",
        params={"user": target.path},
        timeout=_REQUEST_TIMEOUT,
    ) as resp:
        if resp.status != 200:
            raise TokenExtractionError(f"Reading the pairing file failed with status {resp.status}")
        body = await resp.text()

    try:
        payload = json.loads(body)
    except ValueError as err:
        raise TokenExtractionError(f"Pairing file was not JSON: {body[:200]}") from err

    code = payload.get("activation-code")
    if not code:
        raise TokenExtractionError(f"Pairing file contained no activation code: {sorted(payload)}")
    return str(code)


async def _redeem_activation_code(host: str, port: int, code: str, description: str) -> str:
    """Redeem an activation code on the UAUT channel and return the token."""
    reader, writer = await asyncio.open_connection(host, port)
    try:
        # Open UAUT (channel type 7) with sequence 1 — the device ignores any
        # other sequence on a channel open.
        body = bytearray()
        body += struct.pack("<HH", MessageType.COMMAND, 1)
        body += struct.pack("<I", 7)
        body += b"UAUT"
        body += struct.pack("<H", 1)
        body += b"\x00"
        writer.write(HEADER_MAGIC + struct.pack("<HH", len(body), 0) + b"\x00\x00" + bytes(body))
        await writer.drain()

        open_ack = await asyncio.wait_for(reader.read(4096), timeout=_ACTIVATION_TIMEOUT)
        if len(open_ack) < 10:
            raise TokenExtractionError("Device did not acknowledge the UAUT channel")
        channel_id = struct.unpack_from("<H", open_ack, 8)[0]

        request = {
            "message": "user-activation",
            "activation-code": code,
            "description": description,
            "message-type": "request",
            "message-id": 1,
        }
        payload = json.dumps(request, separators=(",", ":")).encode() + b"\n"
        writer.write(HEADER_MAGIC + struct.pack("<HH", len(payload), channel_id) + b"\x00\x00" + payload)
        await writer.drain()

        # The device may emit unrelated frames first; read a few.
        for _ in range(4):
            data = await asyncio.wait_for(reader.read(8192), timeout=_ACTIVATION_TIMEOUT)
            if not data:
                break
            response = _find_activation_response(data)
            if response is None:
                continue
            if response.get("response-code") != 200:
                raise TokenExtractionError(
                    f"Activation rejected by device: {response.get('response-string', response)}"
                )
            token = response.get("user-token")
            if token:
                return str(token)
        raise TokenExtractionError("Device returned no token for the activation code")
    finally:
        writer.close()
        wait_closed = getattr(writer, "wait_closed", None)
        if wait_closed is not None:
            with contextlib.suppress(Exception):
                await wait_closed()


def _find_activation_response(data: bytes) -> dict[str, Any] | None:
    """Pull a user-activation JSON response out of a raw read."""
    start = data.find(b"{")
    while start != -1:
        end = data.rfind(b"}")
        if end <= start:
            return None
        try:
            payload = json.loads(data[start : end + 1])
        except ValueError:
            start = data.find(b"{", start + 1)
            continue
        if isinstance(payload, dict) and payload.get("message") == "user-activation":
            return payload
        return None
    return None
