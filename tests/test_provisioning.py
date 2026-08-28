"""Unit tests for dedicated-user provisioning.

The users.cfg fixtures mirror the layout read from a real 6701W (fw 2.x);
names, tokens, and emails are synthetic.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.comelit_man.exceptions import TokenExtractionError
from custom_components.comelit_man.provisioning import (
    UserSlot,
    _extract_activation_code,
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


_ROW = (
    '<input type="checkbox" onchange="post_page(\'update.html?mspUsersMap_.{slot}_4=\'"'
    ">&nbsp;{n}</td><td>Apps</td><td><image src='led-off.png' />&nbsp;Not Activated "
    "<p style=\"display: inline; font-family:'Helvetica', Monaco, monospace; color: #00662c;\">"
    "<strong>{code}</strong></p></td>"
)


def _users_page(codes: dict[int, str]) -> str:
    """Markup shaped like the device's users.html, one row per slot."""
    return "<table>" + "".join(_ROW.format(slot=f"0.{i}", n=i, code=codes.get(i, "")) for i in range(16)) + "</table>"


class TestExtractActivationCode:
    def test_reads_pending_code_for_the_right_slot(self):
        html = _users_page({4: "sv55jw"})
        assert _extract_activation_code(html, UserSlot(0, 4, "", "", "")) == "sv55jw"

    def test_other_slots_report_nothing(self):
        html = _users_page({4: "sv55jw"})
        assert _extract_activation_code(html, UserSlot(0, 2, "", "", "")) == ""

    def test_distinguishes_adjacent_slots(self):
        html = _users_page({4: "aaaaaa", 5: "bbbbbb"})
        assert _extract_activation_code(html, UserSlot(0, 5, "", "", "")) == "bbbbbb"

    def test_missing_slot_returns_empty(self):
        assert _extract_activation_code("<table></table>", UserSlot(0, 4, "", "", "")) == ""

    def test_activated_slot_has_empty_code(self):
        html = _users_page({})
        assert _extract_activation_code(html, UserSlot(0, 1, "", "", "")) == ""

    def test_double_digit_slot_not_confused_with_single(self):
        """Slot 0.1 must not match the row for 0.10."""
        html = _users_page({10: "tententen"})
        assert _extract_activation_code(html, UserSlot(0, 10, "", "", "")) == "tententen"


class TestProvisionUser:
    """End-to-end flow with the device mocked."""

    def _session(self, *, code: str = "sv55jw", actcode_status: int = 200, actcode_body: str = ""):
        session = MagicMock()

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
            if "users.html" in url:
                return _resp(200, _users_page({2: code} if code else {}))
            return _resp(200, "")

        session.post = MagicMock(side_effect=post)
        session.get = MagicMock(side_effect=get)
        return session

    def _enter(self, stack, session, cfg=None, *, redeem_token="f" * 32):
        """Enter the standard patches; returns the redeem mock."""
        cfg = cfg if cfg is not None else _users_cfg({1: _occupied("phone", "a" * 32, "a@b.c")})
        stack.enter_context(patch("custom_components.comelit_man.provisioning.login", new_callable=AsyncMock))
        stack.enter_context(
            patch(
                "custom_components.comelit_man.provisioning.download_latest_backup",
                new_callable=AsyncMock,
                return_value=b"archive",
            )
        )
        stack.enter_context(patch("custom_components.comelit_man.provisioning.read_users_cfg", return_value=cfg))
        stack.enter_context(patch("aiohttp.ClientSession", return_value=_ctx(session)))
        return stack.enter_context(
            patch(
                "custom_components.comelit_man.provisioning._redeem_activation_code",
                new_callable=AsyncMock,
                return_value=redeem_token,
            )
        )

    @pytest.mark.asyncio
    async def test_happy_path_returns_token(self):
        session = self._session()
        with ExitStack() as stack:
            redeem = self._enter(stack, session)
            token = await provision_user("1.2.3.4", "pw", name="Home Assistant")
        assert token == "f" * 32
        # the scraped code is what gets redeemed
        assert redeem.await_args[0][2] == "sv55jw"

    @pytest.mark.asyncio
    async def test_targets_first_free_slot(self):
        session = self._session()
        with ExitStack() as stack:
            self._enter(stack, session)
            await provision_user("1.2.3.4", "pw")
        wrote = [c.args[0] for c in session.post.call_args_list if c.args and "update.html" in c.args[0]]
        assert wrote, "no field writes recorded"
        for url in wrote:
            assert "mspUsersMap_.0.2_" in url, url

    @pytest.mark.asyncio
    async def test_spaces_encoded_as_percent20(self):
        """aiohttp's default "+" encoding is stored literally by the device."""
        session = self._session()
        with ExitStack() as stack:
            self._enter(stack, session)
            await provision_user("1.2.3.4", "pw", name="Home Assistant")
        names = [c.args[0] for c in session.post.call_args_list if c.args and "_6=" in c.args[0]]
        assert names, "name was never written"
        assert "Home%20Assistant" in names[0]
        assert "+" not in names[0].split("_6=")[1]

    @pytest.mark.asyncio
    async def test_enables_the_slot(self):
        session = self._session()
        with ExitStack() as stack:
            self._enter(stack, session)
            await provision_user("1.2.3.4", "pw")
        wrote = [c.args[0] for c in session.post.call_args_list if c.args and "update.html" in c.args[0]]
        assert any("_4=1" in u for u in wrote), "slot was never enabled"

    @pytest.mark.asyncio
    async def test_full_table_raises_before_touching_device(self):
        session = self._session()
        occupied = {i: _occupied(f"u{i}", f"{i:032d}", "x@y.z") for i in range(16)}
        with ExitStack() as stack:
            self._enter(stack, session, cfg=_users_cfg(occupied))
            with pytest.raises(TokenExtractionError, match="No free user slot"):
                await provision_user("1.2.3.4", "pw")
        session.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_activation_code_raises(self):
        session = self._session(code="")
        with ExitStack() as stack:
            self._enter(stack, session)
            with pytest.raises(TokenExtractionError, match="no activation code"):
                await provision_user("1.2.3.4", "pw")

    @pytest.mark.asyncio
    async def test_invalid_request_body_detected(self):
        """This firmware answers bad requests with HTTP 200 + an HTML error."""
        session = self._session(actcode_body="<td>Invalid request</td>")
        with ExitStack() as stack:
            self._enter(stack, session)
            with pytest.raises(TokenExtractionError, match="Activation code generation failed"):
                await provision_user("1.2.3.4", "pw")

    @pytest.mark.asyncio
    async def test_actcode_failure_raises(self):
        session = self._session(actcode_status=500)
        with ExitStack() as stack:
            self._enter(stack, session)
            with pytest.raises(TokenExtractionError, match="Activation code generation failed"):
                await provision_user("1.2.3.4", "pw")

    @pytest.mark.asyncio
    async def test_empty_user_table_raises(self):
        session = self._session()
        with ExitStack() as stack:
            self._enter(stack, session, cfg="")
            with pytest.raises(TokenExtractionError, match="user table"):
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


# ---------------------------------------------------------------------------
# Error paths and the hass-session branch
# ---------------------------------------------------------------------------


class TestProvisionErrorPaths:
    @pytest.mark.asyncio
    async def test_uses_hass_shared_session_when_available(self):
        """With hass, the shared client session is used, not a new one."""
        shared = MagicMock()
        with (
            patch(
                "custom_components.comelit_man.provisioning.async_get_clientsession",
                return_value=shared,
            ) as get_session,
            patch(
                "custom_components.comelit_man.provisioning._provision",
                new_callable=AsyncMock,
                return_value="f" * 32,
            ) as prov,
            patch("aiohttp.ClientSession") as new_session,
        ):
            token = await provision_user("1.2.3.4", "pw", hass=MagicMock())
        assert token == "f" * 32
        get_session.assert_called_once()
        new_session.assert_not_called()
        assert prov.await_args[0][0] is shared

    @pytest.mark.asyncio
    async def test_default_password_applied(self):
        """Omitting the password falls back to the device default."""
        with (
            patch(
                "custom_components.comelit_man.provisioning._provision",
                new_callable=AsyncMock,
                return_value="f" * 32,
            ) as prov,
            patch("aiohttp.ClientSession", return_value=_ctx(MagicMock())),
        ):
            await provision_user("1.2.3.4")
        assert prov.await_args[0][4] == "comelit"

    @pytest.mark.asyncio
    async def test_field_update_failure_raises(self):
        from custom_components.comelit_man.provisioning import UserSlot, _update_field

        resp = MagicMock()
        resp.status = 500
        resp.text = AsyncMock(return_value="")
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post = MagicMock(return_value=resp)

        with pytest.raises(TokenExtractionError, match="Setting user field 6 failed"):
            await _update_field(session, "http://d", UserSlot(0, 2, "", "", ""), 6, "x")

    @pytest.mark.asyncio
    async def test_users_page_read_failure_raises(self):
        from custom_components.comelit_man.provisioning import UserSlot, _generate_activation_code

        ok = MagicMock()
        ok.status = 200
        ok.text = AsyncMock(return_value="")
        ok.__aenter__ = AsyncMock(return_value=ok)
        ok.__aexit__ = AsyncMock(return_value=False)
        bad = MagicMock()
        bad.status = 503
        bad.text = AsyncMock(return_value="")
        bad.__aenter__ = AsyncMock(return_value=bad)
        bad.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post = MagicMock(return_value=ok)
        session.get = MagicMock(return_value=bad)

        with pytest.raises(TokenExtractionError, match="Reading the users page failed"):
            await _generate_activation_code(session, "http://d", UserSlot(0, 2, "", "", ""))


class TestRedeemActivationCode:
    """The UAUT redemption leg, with the ICONA client mocked."""

    def _client(self, response=None, *, connect_error=None):
        client = MagicMock()
        client.connect = AsyncMock(side_effect=connect_error)
        client.disconnect = AsyncMock()
        client.open_channel = AsyncMock(return_value=MagicMock())
        client.send_json = AsyncMock(return_value=response or {})
        return client

    @pytest.mark.asyncio
    async def test_returns_token_on_success(self):
        from custom_components.comelit_man.provisioning import _redeem_activation_code

        client = self._client({"response-code": 200, "user-token": "a" * 32})
        with patch("custom_components.comelit_man.client.IconaBridgeClient", return_value=client):
            token = await _redeem_activation_code("1.2.3.4", 64100, "abc123", "HA")
        assert token == "a" * 32
        client.disconnect.assert_awaited_once()
        sent = client.send_json.await_args[0][1]
        assert sent["message"] == "user-activation"
        assert sent["activation-code"] == "abc123"

    @pytest.mark.asyncio
    async def test_rejection_raises_with_device_reason(self):
        from custom_components.comelit_man.provisioning import _redeem_activation_code

        client = self._client({"response-code": 403, "response-string": "Invalid code"})
        with (
            patch("custom_components.comelit_man.client.IconaBridgeClient", return_value=client),
            pytest.raises(TokenExtractionError, match="Invalid code"),
        ):
            await _redeem_activation_code("1.2.3.4", 64100, "bad", "HA")

    @pytest.mark.asyncio
    async def test_accepted_without_token_raises(self):
        from custom_components.comelit_man.provisioning import _redeem_activation_code

        client = self._client({"response-code": 200})
        with (
            patch("custom_components.comelit_man.client.IconaBridgeClient", return_value=client),
            pytest.raises(TokenExtractionError, match="returned no token"),
        ):
            await _redeem_activation_code("1.2.3.4", 64100, "abc", "HA")

    @pytest.mark.asyncio
    async def test_connection_failure_wrapped(self):
        from custom_components.comelit_man.provisioning import _redeem_activation_code

        client = self._client(connect_error=OSError("refused"))
        with (
            patch("custom_components.comelit_man.client.IconaBridgeClient", return_value=client),
            pytest.raises(TokenExtractionError, match="Activation request failed"),
        ):
            await _redeem_activation_code("1.2.3.4", 64100, "abc", "HA")

    @pytest.mark.asyncio
    async def test_disconnect_failure_is_suppressed(self):
        from custom_components.comelit_man.provisioning import _redeem_activation_code

        client = self._client({"response-code": 200, "user-token": "b" * 32})
        client.disconnect = AsyncMock(side_effect=OSError("already closed"))
        with patch("custom_components.comelit_man.client.IconaBridgeClient", return_value=client):
            assert await _redeem_activation_code("1.2.3.4", 64100, "abc", "HA") == "b" * 32


class TestRedeemPassesThroughOwnErrors:
    @pytest.mark.asyncio
    async def test_token_extraction_error_not_rewrapped(self):
        """A TokenExtractionError from the client keeps its own message."""
        from custom_components.comelit_man.provisioning import _redeem_activation_code

        client = MagicMock()
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()
        client.open_channel = AsyncMock(side_effect=TokenExtractionError("channel refused"))
        with (
            patch("custom_components.comelit_man.client.IconaBridgeClient", return_value=client),
            pytest.raises(TokenExtractionError, match="channel refused"),
        ):
            await _redeem_activation_code("1.2.3.4", 64100, "abc", "HA")
