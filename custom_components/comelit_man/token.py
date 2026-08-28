"""Token extraction from the device's HTTP backup archive.

Based on https://github.com/nicolas-fricke/ha-component-comelit-intercom
"""

from __future__ import annotations

import asyncio
import gzip
import io
import logging
import re
import tarfile
from pathlib import PurePosixPath

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .exceptions import TokenExtractionError

_LOGGER = logging.getLogger(__name__)

TOKEN_PATTERN = re.compile(r'9:4:"([a-f0-9]{32})"', re.IGNORECASE)

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=10)


async def extract_token(
    host: str,
    password: str | None = None,
    http_port: int = 8080,
    hass: HomeAssistant | None = None,
) -> str | None:
    """Extract the 32-char hex authentication token from the device backup.

    The Comelit web interface uses IP-based sessions — once we authenticate
    from an IP address, all subsequent requests from that IP are authorized.
    """
    if password is None:
        password = "comelit"
    base_url = f"http://{host}:{http_port}"

    if hass is not None:
        session = async_get_clientsession(hass)
        return await _do_extract(session, base_url, password)

    async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
        return await _do_extract(session, base_url, password)


async def _do_extract(session: aiohttp.ClientSession, base_url: str, password: str) -> str | None:
    """Run the extraction steps against the given session."""
    await login(session, base_url, password)
    archive_data = await download_latest_backup(session, base_url)
    return _parse_token_from_archive(archive_data)


async def login(session: aiohttp.ClientSession, base_url: str, password: str) -> None:
    """Authenticate against the device web UI.

    Sessions are IP-based: once authenticated from an address, subsequent
    requests from it are authorized.  Shared with user provisioning.
    """
    _LOGGER.debug("Logging in to %s", base_url)
    login_headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": f"{base_url}/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    async with session.post(
        f"{base_url}/do-login.html",
        data={"l-pwd": password},
        headers=login_headers,
        timeout=_REQUEST_TIMEOUT,
    ) as resp:
        if resp.status != 200:
            raise TokenExtractionError(f"Login failed with status {resp.status}")
        login_content = await resp.text()
        if "Access granted" not in login_content:
            raise TokenExtractionError("Login failed — check password")

    _LOGGER.debug("Login successful")


async def download_latest_backup(session: aiohttp.ClientSession, base_url: str) -> bytes:
    """Create a fresh configuration backup and download it."""
    _LOGGER.debug("Creating backup")
    backup_headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{base_url}/config-backup.html",
    }
    async with session.post(
        f"{base_url}/create-backup.html",
        headers=backup_headers,
        timeout=_REQUEST_TIMEOUT,
    ) as resp:
        create_response = await resp.text()
        if "Backup successfully created" not in create_response:
            _LOGGER.error("Backup creation failed: %s", create_response)
            raise TokenExtractionError("Backup creation failed")

    # Wait for the device to finish creating the backup file
    await asyncio.sleep(2)

    _LOGGER.debug("Listing backups")
    async with session.get(f"{base_url}/config-backup.html", timeout=_REQUEST_TIMEOUT) as resp:
        if resp.status != 200:
            raise TokenExtractionError(f"Backup page returned status {resp.status}")
        html = await resp.text()

    backup_files = re.findall(r"([0-9]+\.tar\.gz)", html)
    if not backup_files:
        raise TokenExtractionError(f"No backup files found on device. Page content (first 500 chars): {html[:500]}")

    backup_files.sort()
    latest_backup = backup_files[-1]
    _LOGGER.debug("Using latest backup: %s", latest_backup)

    async with session.get(f"{base_url}/{latest_backup}", timeout=_REQUEST_TIMEOUT) as resp:
        if resp.status != 200:
            raise TokenExtractionError(f"Backup download failed with status {resp.status}")
        archive_data = await resp.read()

    _LOGGER.debug("Downloaded %d bytes", len(archive_data))
    return bytes(archive_data)


def read_users_cfg(archive_data: bytes) -> str:
    """Return the text of etc/comelit/users.cfg from a backup archive.

    Matches the basename exactly — facerecognitionusers.cfg is a different
    file that also ends in "users.cfg" and appears earlier in the archive.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:gz") as tar:
            for member in tar.getmembers():
                if PurePosixPath(member.name).name != "users.cfg":
                    continue
                f = tar.extractfile(member)
                if f is None:
                    continue
                raw = f.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                text = raw.decode("utf-8", errors="replace")
                if "mspUsersMap" in text:
                    return text
    except tarfile.TarError as e:
        raise TokenExtractionError(f"Failed to read backup archive: {e}") from e
    raise TokenExtractionError("users.cfg with user slots not found in backup archive")


def _parse_token_from_archive(archive_data: bytes) -> str | None:
    """Parse the authentication token from a backup tar.gz archive."""
    members_seen: list[str] = []
    candidates_read: list[str] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:gz") as tar:
            for member in tar.getmembers():
                members_seen.append(member.name)
                # Match the file name exactly.  An `endswith` test also matches
                # etc/comelit/facerecognitionusers.cfg, which holds unrelated
                # face-recognition users and no tokens — and on this firmware it
                # is listed *before* the real users.cfg, so a first-match-wins
                # scan reads the wrong file and fails (verified on 6701W fw 2.x).
                if PurePosixPath(member.name).name != "users.cfg":
                    continue
                f = tar.extractfile(member)
                if f is None:
                    continue
                raw = f.read()

                # Some firmware versions gzip users.cfg without a .gz extension
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)

                content = raw.decode("utf-8", errors="replace")
                candidates_read.append(f"{member.name} ({len(content)} bytes)")
                _LOGGER.debug("Read %s: %d bytes", member.name, len(content))

                # Skip null tokens (all zeros); keep scanning other candidates
                # rather than failing on the first file without a token.
                for token in TOKEN_PATTERN.findall(content):
                    if token != "00000000000000000000000000000000":
                        _LOGGER.debug(
                            "Extracted token: %s...%s", token[:4], token[-4:]
                        )  # nosemgrep: python-logger-credential-disclosure
                        return str(token)

    except tarfile.TarError as e:
        raise TokenExtractionError(f"Failed to read backup archive: {e}") from e

    if candidates_read:
        raise TokenExtractionError(f"No token found in backup archive. Files read: {', '.join(candidates_read)}")
    raise TokenExtractionError(f"users.cfg not found in backup archive. Members seen: {members_seen}")
