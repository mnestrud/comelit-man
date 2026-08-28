"""Unit tests for token extraction — no device needed."""

from __future__ import annotations

import gzip
import io
import tarfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.comelit_man.exceptions import TokenExtractionError
from custom_components.comelit_man.token import (
    _parse_token_from_archive,
    extract_token,
)


def _make_tar_gz(files: dict[str, bytes]) -> bytes:
    """Build an in-memory tar.gz with the given filename→content mapping."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


VALID_USERS_CFG = b'9:4:"abcdef1234567890abcdef1234567890"'


class TestParseTokenFromArchive:
    def test_parse_token_success(self):
        archive = _make_tar_gz({"config/users.cfg": VALID_USERS_CFG})
        token = _parse_token_from_archive(archive)
        assert token == "abcdef1234567890abcdef1234567890"

    def test_parse_token_missing_users_cfg(self):
        archive = _make_tar_gz({"config/other.cfg": b"irrelevant content"})
        with pytest.raises(TokenExtractionError, match="users.cfg not found"):
            _parse_token_from_archive(archive)

    def test_parse_token_missing_users_cfg_lists_members(self):
        archive = _make_tar_gz({"config/other.cfg": b"data", "config/network.cfg": b"data"})
        with pytest.raises(TokenExtractionError, match="other.cfg"):
            _parse_token_from_archive(archive)

    def test_parse_token_no_match_in_users_cfg(self):
        archive = _make_tar_gz({"config/users.cfg": b"no token here"})
        with pytest.raises(TokenExtractionError, match="No token found"):
            _parse_token_from_archive(archive)

    def test_parse_token_skips_null_token(self):
        null_token = b'9:4:"00000000000000000000000000000000"'
        valid_token = b'9:4:"abcdef1234567890abcdef1234567890"'
        archive = _make_tar_gz({"config/users.cfg": null_token + b"\n" + valid_token})
        token = _parse_token_from_archive(archive)
        assert token == "abcdef1234567890abcdef1234567890"

    def test_parse_token_all_null_tokens(self):
        null_token = b'9:4:"00000000000000000000000000000000"'
        archive = _make_tar_gz({"config/users.cfg": null_token})
        with pytest.raises(TokenExtractionError, match="No token found"):
            _parse_token_from_archive(archive)

    def test_parse_token_gzipped_users_cfg(self):
        compressed = gzip.compress(VALID_USERS_CFG)
        archive = _make_tar_gz({"config/users.cfg": compressed})
        token = _parse_token_from_archive(archive)
        assert token == "abcdef1234567890abcdef1234567890"

    def test_parse_token_bad_archive(self):
        with pytest.raises(TokenExtractionError, match="Failed to read backup archive"):
            _parse_token_from_archive(b"not a valid tar.gz")

    def test_parse_token_skips_none_extractfile(self):
        """Tar member where extractfile returns None (directory) is skipped — line 137.

        tarfile.extractfile() returns None for directory entries.  We give the
        directory a name that ends with 'users.cfg' so the extraction branch is
        entered and the `if f is None: continue` guard fires before the real
        users.cfg file is processed.
        """
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            # Directory whose name ends with users.cfg — extractfile returns None
            dir_info = tarfile.TarInfo(name="dir.users.cfg")
            dir_info.type = tarfile.DIRTYPE
            tar.addfile(dir_info)
            # Real file follows
            content = VALID_USERS_CFG
            file_info = tarfile.TarInfo(name="config/users.cfg")
            file_info.size = len(content)
            tar.addfile(file_info, io.BytesIO(content))
        token = _parse_token_from_archive(buf.getvalue())
        assert token == "abcdef1234567890abcdef1234567890"


# ---------------------------------------------------------------------------
# extract_token — HTTP flow with mocked aiohttp
# ---------------------------------------------------------------------------


def _make_mock_response(status: int = 200, text: str = "", content: bytes = b""):
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    resp.read = AsyncMock(return_value=content)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_session(*responses):
    """Build a mock aiohttp session that returns responses in order."""
    session = MagicMock()

    response_iter = iter(responses)

    def _get_next(*args, **kwargs):
        return next(response_iter)

    session.post = MagicMock(side_effect=_get_next)
    session.get = MagicMock(side_effect=_get_next)
    return session


def _patch_session(session):
    """Patch async_get_clientsession to return the given mock session."""
    return patch(
        "custom_components.comelit_man.token.async_get_clientsession",
        return_value=session,
    )


class TestExtractToken:
    @pytest.mark.asyncio
    async def test_extract_token_success(self):
        """Full happy-path: login → create backup → list → download → parse."""
        archive = _make_tar_gz({"config/users.cfg": VALID_USERS_CFG})

        login_resp = _make_mock_response(200, "Access granted")
        backup_resp = _make_mock_response(200, "Backup successfully created")
        list_resp = _make_mock_response(200, '<a href="12345.tar.gz">12345.tar.gz</a>')
        dl_resp = _make_mock_response(200, content=archive)

        session = _make_session(login_resp, backup_resp, list_resp, dl_resp)

        with _patch_session(session), patch("asyncio.sleep", AsyncMock()):
            token = await extract_token("192.168.1.1", "comelit", 8080, MagicMock())

        assert token == "abcdef1234567890abcdef1234567890"

    @pytest.mark.asyncio
    async def test_extract_token_login_fails_bad_status(self):
        """Raises TokenExtractionError when login returns non-200."""
        login_resp = _make_mock_response(403, "Forbidden")
        session = _make_session(login_resp)

        with _patch_session(session), pytest.raises(TokenExtractionError, match="Login failed with status 403"):
            await extract_token("192.168.1.1", hass=MagicMock())

    @pytest.mark.asyncio
    async def test_extract_token_login_fails_wrong_content(self):
        """Raises TokenExtractionError when login response lacks 'Access granted'."""
        login_resp = _make_mock_response(200, "Invalid password")
        session = _make_session(login_resp)

        with _patch_session(session), pytest.raises(TokenExtractionError, match="Login failed"):
            await extract_token("192.168.1.1", hass=MagicMock())

    @pytest.mark.asyncio
    async def test_extract_token_backup_creation_fails(self):
        """Raises TokenExtractionError when backup creation response is unexpected."""
        login_resp = _make_mock_response(200, "Access granted")
        backup_resp = _make_mock_response(200, "something unexpected")
        session = _make_session(login_resp, backup_resp)

        with _patch_session(session), patch("asyncio.sleep", AsyncMock()):
            with pytest.raises(TokenExtractionError, match="Backup creation failed"):
                await extract_token("192.168.1.1", hass=MagicMock())

    @pytest.mark.asyncio
    async def test_extract_token_no_backup_files(self):
        """Raises TokenExtractionError when no .tar.gz files listed."""
        login_resp = _make_mock_response(200, "Access granted")
        backup_resp = _make_mock_response(200, "Backup successfully created")
        list_resp = _make_mock_response(200, "<html>No backups here</html>")
        session = _make_session(login_resp, backup_resp, list_resp)

        with _patch_session(session), patch("asyncio.sleep", AsyncMock()):
            with pytest.raises(TokenExtractionError, match="No backup files found"):
                await extract_token("192.168.1.1", hass=MagicMock())

    @pytest.mark.asyncio
    async def test_extract_token_download_fails(self):
        """Raises TokenExtractionError when archive download returns non-200."""
        login_resp = _make_mock_response(200, "Access granted")
        backup_resp = _make_mock_response(200, "Backup successfully created")
        list_resp = _make_mock_response(200, '<a href="99.tar.gz">99.tar.gz</a>')
        dl_resp = _make_mock_response(404)
        session = _make_session(login_resp, backup_resp, list_resp, dl_resp)

        with _patch_session(session), patch("asyncio.sleep", AsyncMock()):
            with pytest.raises(TokenExtractionError, match="Backup download failed"):
                await extract_token("192.168.1.1", hass=MagicMock())

    @pytest.mark.asyncio
    async def test_extract_token_backup_page_fails(self):
        """Raises TokenExtractionError when backup listing returns non-200."""
        login_resp = _make_mock_response(200, "Access granted")
        backup_resp = _make_mock_response(200, "Backup successfully created")
        list_resp = _make_mock_response(500, "Server Error")
        session = _make_session(login_resp, backup_resp, list_resp)

        with _patch_session(session), patch("asyncio.sleep", AsyncMock()):
            with pytest.raises(TokenExtractionError, match="Backup page returned status 500"):
                await extract_token("192.168.1.1", hass=MagicMock())


# ---------------------------------------------------------------------------
# Regression: facerecognitionusers.cfg must not shadow users.cfg
# ---------------------------------------------------------------------------


def _archive(members: list[tuple[str, bytes]]) -> bytes:
    """Build a tar.gz with members in the given order."""
    import io as _io
    import tarfile as _tarfile

    buf = _io.BytesIO()
    with _tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, payload in members:
            info = _tarfile.TarInfo(name=name)
            info.size = len(payload)
            tar.addfile(info, _io.BytesIO(payload))
    return buf.getvalue()


# Shapes mirror a real 6701W backup; values are synthetic.
_FACEREC = b'recUserList.0 = 2:4:"someone" 3:2:1 4:2:1 \n'
_USERS = (
    b'mspUsersMap.0.0 = 4:2:2 5:2:0 6:4:"" 9:4:"" 11:4:"" \n'
    b'mspUsersMap.0.1 = 4:2:1 5:2:2 6:4:"Phone" 9:4:"aedf67d4130203c77b8e62ecb093ec39" 11:4:"a@b.c" \n'
)


class TestUsersCfgSelection:
    def test_facerecognitionusers_does_not_shadow_users_cfg(self):
        """It sorts before users.cfg in a real archive and holds no token."""
        from custom_components.comelit_man.token import _parse_token_from_archive

        data = _archive(
            [
                ("etc/comelit/facerecognitionusers.cfg", _FACEREC),
                ("etc/comelit/users.cfg", _USERS),
            ]
        )
        assert _parse_token_from_archive(data) == "aedf67d4130203c77b8e62ecb093ec39"

    def test_token_found_regardless_of_member_order(self):
        from custom_components.comelit_man.token import _parse_token_from_archive

        data = _archive(
            [
                ("etc/comelit/users.cfg", _USERS),
                ("etc/comelit/facerecognitionusers.cfg", _FACEREC),
            ]
        )
        assert _parse_token_from_archive(data) == "aedf67d4130203c77b8e62ecb093ec39"

    def test_error_names_files_actually_read(self):
        from custom_components.comelit_man.exceptions import TokenExtractionError
        from custom_components.comelit_man.token import _parse_token_from_archive

        empty = b'mspUsersMap.0.0 = 4:2:2 5:2:0 6:4:"" 9:4:"" \n'
        data = _archive([("etc/comelit/facerecognitionusers.cfg", _FACEREC), ("etc/comelit/users.cfg", empty)])
        with pytest.raises(TokenExtractionError, match="users.cfg"):
            _parse_token_from_archive(data)

    def test_missing_users_cfg_reports_members(self):
        from custom_components.comelit_man.exceptions import TokenExtractionError
        from custom_components.comelit_man.token import _parse_token_from_archive

        data = _archive([("etc/comelit/facerecognitionusers.cfg", _FACEREC)])
        with pytest.raises(TokenExtractionError, match="not found"):
            _parse_token_from_archive(data)
