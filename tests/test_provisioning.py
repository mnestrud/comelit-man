"""Unit tests for dedicated-user provisioning.

The users.cfg fixtures mirror the layout read from a real 6701W (fw 2.x);
names, tokens, and emails are synthetic.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.comelit_man.exceptions import TokenExtractionError
from custom_components.comelit_man.provisioning import (
    UserSlot,
    _find_activation_response,
    find_free_slot,
    parse_user_slots,
    provision_user,
)

_EMPTY = (
    '4:2:2 5:2:0 6:4:"" 7:2:2 8:2:0 9:4:"" 10:4:"" 11:4:"" 12:4:"" 13:2:0 '
    '14:4:"" 15:2:0 16:2:0 17:4:"" 18:4:"" 19:2:2 20:4:"" 21:2:0 22:4:""'
)


def _occupied(name: str, token: str, email: str) -> str:
    return (
        f'4:2:1 5:2:2 6:4:"{name}" 7:2:2 8:2:0 9:4:"{token}" 10:4:"" '
        f'11:4:"{email}" 12:4:"" 13:2:0 14:4:"" 15:2:0 16:2:0 17:4:"" '
        f'18:4:"abcdefghij" 19:2:2 20:4:"" 21:2:0 22:4:""'
    )


def _users_cfg(occupied_slots: dict[int, str] | None = None, count: int = 16) -> str:
    occupied_slots = occupied_slots or {}
    lines = []
    for slot in range(count):
        body = occupied_slots.get(slot, _EMPTY)
        lines.append(f"mspUsersMap.0.{slot} = {body} ")
    return "\n".join(lines)


def _cfg_pair(code: str = "zx9y8w7v6u") -> list[str]:
    """users.cfg before and after code generation; free target is slot 0.2."""
    taken = {1: _occupied("phone", "a" * 32, "a@b.c")}
    before = _users_cfg(taken)
    after = _users_cfg({**taken, 2: _EMPTY.replace('18:4:""', f'18:4:"{code}"')})
    return [before, after]


class TestParseUserSlots:
    def test_parses_all_slots(self):
        assert len(parse_user_slots(_users_cfg())) == 16

    def test_reads_name_token_email(self):
        cfg = _users_cfg({1: _occupied("google Pixel 10", "a" * 32, "someone@example.com")})
        slot = parse_user_slots(cfg)[1]
        assert slot.name == "google Pixel 10"
        assert slot.token == "a" * 32
        assert slot.email == "someone@example.com"

    def test_empty_slot_is_free(self):
        assert parse_user_slots(_users_cfg())[5].is_free is True

    def test_occupied_slot_is_not_free(self):
        cfg = _users_cfg({3: _occupied("phone", "b" * 32, "x@y.z")})
        assert parse_user_slots(cfg)[3].is_free is False

    def test_slot_path_format(self):
        assert parse_user_slots(_users_cfg())[7].path == "0.7"

    def test_ignores_unrelated_lines(self):
        cfg = 'recUserList.0 = 2:4:"ryan" 3:2:1\n' + _users_cfg()
        assert len(parse_user_slots(cfg)) == 16

    def test_empty_config_yields_nothing(self):
        assert parse_user_slots("") == []

    def test_name_only_slot_counts_as_occupied(self):
        """A named slot without a token is still in use."""
        cfg = _users_cfg({4: _EMPTY.replace('6:4:""', '6:4:"Reserved"')})
        assert parse_user_slots(cfg)[4].is_free is False


class TestFindFreeSlot:
    def test_picks_first_free_slot_after_reserved(self):
        cfg = _users_cfg({1: _occupied("phone", "a" * 32, "a@b.c")})
        assert find_free_slot(parse_user_slots(cfg)).path == "0.2"

    def test_never_returns_slot_zero(self):
        """Slot 0 is the wall monitor even when it looks empty."""
        slots = parse_user_slots(_users_cfg())
        assert slots[0].is_free is True
        assert find_free_slot(slots).slot != 0

    def test_skips_occupied_slots(self):
        cfg = _users_cfg({1: _occupied("a", "a" * 32, "a@b.c"), 2: _occupied("b", "b" * 32, "b@c.d")})
        assert find_free_slot(parse_user_slots(cfg)).path == "0.3"

    def test_raises_when_table_full(self):
        occupied = {i: _occupied(f"u{i}", f"{i:032d}", "x@y.z") for i in range(16)}
        with pytest.raises(TokenExtractionError, match="No free user slot"):
            find_free_slot(parse_user_slots(_users_cfg(occupied)))

    def test_raises_when_only_slot_zero_free(self):
        occupied = {i: _occupied(f"u{i}", f"{i:032d}", "x@y.z") for i in range(1, 16)}
        with pytest.raises(TokenExtractionError, match="No free user slot"):
            find_free_slot(parse_user_slots(_users_cfg(occupied)))


class TestActivationResponseParsing:
    def test_extracts_activation_response(self):
        payload = b"\x00\x06" + json.dumps({"message": "user-activation", "response-code": 200}).encode()
        assert _find_activation_response(payload)["response-code"] == 200

    def test_ignores_other_messages(self):
        payload = json.dumps({"message": "access", "response-code": 200}).encode()
        assert _find_activation_response(payload) is None

    def test_handles_non_json(self):
        assert _find_activation_response(b"\x00\x06\x01\x02\x03") is None

    def test_handles_malformed_json(self):
        assert _find_activation_response(b'{"message": "user-activation"') is None


class TestProvisionUser:
    """End-to-end flow with the device mocked."""

    def _session(self, *, actcode_status: int = 200, actcode_body: str = "ok"):
        session = MagicMock()
        responses: dict[str, MagicMock] = {}

        def _resp(status=200, text=""):
            r = MagicMock()
            r.status = status
            r.text = AsyncMock(return_value=text)
            r.read = AsyncMock(return_value=b"")
            r.__aenter__ = AsyncMock(return_value=r)
            r.__aexit__ = AsyncMock(return_value=False)
            return r

        def post(url, **kwargs):
            if "create-actcode" in url:
                return _resp(actcode_status, actcode_body)
            return _resp(200, "Access granted")

        def get(url, **kwargs):
            return _resp(200, "")

        session.post = MagicMock(side_effect=post)
        session.get = MagicMock(side_effect=get)
        responses.clear()
        return session

    @pytest.mark.asyncio
    async def test_happy_path_returns_token(self):
        session = self._session()
        with (
            patch("custom_components.comelit_man.provisioning.login", new_callable=AsyncMock),
            patch(
                "custom_components.comelit_man.provisioning.download_latest_backup",
                new_callable=AsyncMock,
                return_value=b"archive",
            ),
            patch("custom_components.comelit_man.provisioning.read_users_cfg", side_effect=_cfg_pair()),
            patch(
                "custom_components.comelit_man.provisioning._redeem_activation_code",
                new_callable=AsyncMock,
                return_value="f" * 32,
            ) as redeem,
            patch("aiohttp.ClientSession", return_value=_ctx(session)),
        ):
            token = await provision_user("1.2.3.4", "pw", name="Home Assistant")
        assert token == "f" * 32
        redeem.assert_awaited_once()
        assert redeem.await_args[0][2] == "zx9y8w7v6u"

    @pytest.mark.asyncio
    async def test_targets_first_free_slot(self):
        session = self._session()
        with (
            patch("custom_components.comelit_man.provisioning.login", new_callable=AsyncMock),
            patch(
                "custom_components.comelit_man.provisioning.download_latest_backup",
                new_callable=AsyncMock,
                return_value=b"archive",
            ),
            patch("custom_components.comelit_man.provisioning.read_users_cfg", side_effect=_cfg_pair()),
            patch(
                "custom_components.comelit_man.provisioning._redeem_activation_code",
                new_callable=AsyncMock,
                return_value="f" * 32,
            ),
            patch("aiohttp.ClientSession", return_value=_ctx(session)),
        ):
            await provision_user("1.2.3.4", "pw")
        # Field updates and the actcode request must all address slot 0_2
        targeted = [c for c in session.post.call_args_list if c.kwargs.get("params")]
        assert targeted, "no parameterised POSTs recorded"
        for call in targeted:
            key = next(iter(call.kwargs["params"]))
            assert "0.2" in key or call.kwargs["params"].get("user") == "0.2"

    @pytest.mark.asyncio
    async def test_full_table_raises_before_touching_device(self):
        session = self._session()
        occupied = {i: _occupied(f"u{i}", f"{i:032d}", "x@y.z") for i in range(16)}
        with (
            patch("custom_components.comelit_man.provisioning.login", new_callable=AsyncMock),
            patch(
                "custom_components.comelit_man.provisioning.download_latest_backup",
                new_callable=AsyncMock,
                return_value=b"archive",
            ),
            patch("custom_components.comelit_man.provisioning.read_users_cfg", return_value=_users_cfg(occupied)),
            patch("aiohttp.ClientSession", return_value=_ctx(session)),
            pytest.raises(TokenExtractionError, match="No free user slot"),
        ):
            await provision_user("1.2.3.4", "pw")
        session.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_activation_code_raises(self):
        """Device accepted the request but left field 18 empty."""
        session = self._session()
        with (
            patch("custom_components.comelit_man.provisioning.login", new_callable=AsyncMock),
            patch(
                "custom_components.comelit_man.provisioning.download_latest_backup",
                new_callable=AsyncMock,
                return_value=b"archive",
            ),
            patch("custom_components.comelit_man.provisioning.read_users_cfg", return_value=_users_cfg()),
            patch("aiohttp.ClientSession", return_value=_ctx(session)),
            pytest.raises(TokenExtractionError, match="no activation code"),
        ):
            await provision_user("1.2.3.4", "pw")

    @pytest.mark.asyncio
    async def test_activation_code_read_from_field_18(self):
        """The generated code is read back from the user table, not a .mug file."""
        session = self._session()
        with (
            patch("custom_components.comelit_man.provisioning.login", new_callable=AsyncMock),
            patch(
                "custom_components.comelit_man.provisioning.download_latest_backup",
                new_callable=AsyncMock,
                return_value=b"archive",
            ),
            patch("custom_components.comelit_man.provisioning.read_users_cfg", side_effect=_cfg_pair()),
            patch(
                "custom_components.comelit_man.provisioning._redeem_activation_code",
                new_callable=AsyncMock,
                return_value="f" * 32,
            ) as redeem,
            patch("aiohttp.ClientSession", return_value=_ctx(session)),
        ):
            token = await provision_user("1.2.3.4", "pw")
        assert token == "f" * 32
        assert redeem.await_args[0][2] == "zx9y8w7v6u"

    @pytest.mark.asyncio
    async def test_invalid_request_body_detected(self):
        """This firmware answers bad requests with HTTP 200 + an HTML error."""
        session = self._session(actcode_body="<td>Invalid request</td>")
        with (
            patch("custom_components.comelit_man.provisioning.login", new_callable=AsyncMock),
            patch(
                "custom_components.comelit_man.provisioning.download_latest_backup",
                new_callable=AsyncMock,
                return_value=b"archive",
            ),
            patch("custom_components.comelit_man.provisioning.read_users_cfg", return_value=_users_cfg()),
            patch("aiohttp.ClientSession", return_value=_ctx(session)),
            pytest.raises(TokenExtractionError, match="Activation code generation failed"),
        ):
            await provision_user("1.2.3.4", "pw")

    @pytest.mark.asyncio
    async def test_actcode_failure_raises(self):
        session = self._session(actcode_status=500)
        with (
            patch("custom_components.comelit_man.provisioning.login", new_callable=AsyncMock),
            patch(
                "custom_components.comelit_man.provisioning.download_latest_backup",
                new_callable=AsyncMock,
                return_value=b"archive",
            ),
            patch("custom_components.comelit_man.provisioning.read_users_cfg", return_value=_users_cfg()),
            patch("aiohttp.ClientSession", return_value=_ctx(session)),
            pytest.raises(TokenExtractionError, match="Activation code generation failed"),
        ):
            await provision_user("1.2.3.4", "pw")

    @pytest.mark.asyncio
    async def test_empty_user_table_raises(self):
        session = self._session()
        with (
            patch("custom_components.comelit_man.provisioning.login", new_callable=AsyncMock),
            patch(
                "custom_components.comelit_man.provisioning.download_latest_backup",
                new_callable=AsyncMock,
                return_value=b"archive",
            ),
            patch("custom_components.comelit_man.provisioning.read_users_cfg", return_value=""),
            patch("aiohttp.ClientSession", return_value=_ctx(session)),
            pytest.raises(TokenExtractionError, match="user table"),
        ):
            await provision_user("1.2.3.4", "pw")


def _ctx(session):
    """Wrap a mock session as an async context manager."""
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


class TestUserSlot:
    def test_free_when_name_and_token_empty(self):
        assert UserSlot(0, 4, "", "", "").is_free is True

    def test_not_free_with_token_only(self):
        assert UserSlot(0, 4, "", "a" * 32, "").is_free is False
