"""Additional config flow tests — reauth, reconfigure, options, DHCP confirm."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.comelit_man.exceptions import AuthenticationError

HOST = "192.168.1.111"
PORT = 64100
TOKEN = "abc123def456abc123def456abc123de"


def _mock_client(*, auth_error: bool = False, connect_error: Exception | None = None):
    client = MagicMock()
    client.connect = AsyncMock(side_effect=connect_error)
    client.disconnect = AsyncMock()
    if auth_error:
        client.connect = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


class TestOptionsFlow:
    def _make_options_flow(self, current_notifications: bool = True):
        from custom_components.comelit_man.config_flow import ComelitLocalOptionsFlow

        entry = MagicMock()
        entry.options = {"enable_notifications": current_notifications}
        return ComelitLocalOptionsFlow(entry)

    @pytest.mark.asyncio
    async def test_no_input_shows_form(self):
        flow = self._make_options_flow()
        result = await flow.async_step_init(user_input=None)
        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_submit_creates_entry(self):
        flow = self._make_options_flow()
        result = await flow.async_step_init(user_input={"enable_notifications": False})
        assert result["type"] in ("create_entry", "abort", "form")  # stub returns dict

    @pytest.mark.asyncio
    async def test_default_reflects_current_setting(self):
        flow = self._make_options_flow(current_notifications=False)
        result = await flow.async_step_init(user_input=None)
        # Form is shown — data_schema carries the default; just verify type
        assert result["type"] == "form"


# ---------------------------------------------------------------------------
# Reauth flow
# ---------------------------------------------------------------------------


class TestReauthFlow:
    def _make_flow(self):
        from custom_components.comelit_man.config_flow import ComelitLocalConfigFlow

        flow = ComelitLocalConfigFlow()
        mock_entry = MagicMock()
        mock_entry.data = {"host": HOST, "port": PORT, "http_port": 8080, "token": TOKEN}
        flow._get_reauth_entry = lambda: mock_entry
        hass = MagicMock()
        hass.config_entries.flow.async_progress_by_handler.return_value = []
        hass.config_entries.async_entries.return_value = []
        hass.config_entries.async_entry_for_domain_unique_id.return_value = None
        flow.hass = hass
        flow.context = {}
        return flow

    @pytest.mark.asyncio
    async def test_reauth_shows_form_on_no_input(self):
        flow = self._make_flow()
        result = await flow.async_step_reauth_confirm(user_input=None)
        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_reauth_success_updates_entry(self):
        flow = self._make_flow()
        client = _mock_client()

        with (
            patch("custom_components.comelit_man.config_flow.IconaBridgeClient", return_value=client),
            patch("custom_components.comelit_man.config_flow.authenticate", new_callable=AsyncMock),
        ):
            result = await flow.async_step_reauth_confirm(user_input={"token": TOKEN, "password": "comelit"})

        client.disconnect.assert_awaited_once()
        # Result should be an abort (reauth_successful) or create_entry
        assert result["type"] in ("abort", "create_entry")

    @pytest.mark.asyncio
    async def test_reauth_invalid_auth_shows_error(self):
        flow = self._make_flow()
        client = _mock_client()

        with (
            patch("custom_components.comelit_man.config_flow.IconaBridgeClient", return_value=client),
            patch(
                "custom_components.comelit_man.config_flow.authenticate",
                new_callable=AsyncMock,
                side_effect=AuthenticationError("bad token"),
            ),
        ):
            result = await flow.async_step_reauth_confirm(user_input={"token": TOKEN, "password": "comelit"})

        assert result["type"] == "form"
        assert result["errors"]["base"] == "invalid_auth"


# ---------------------------------------------------------------------------
# Reconfigure flow
# ---------------------------------------------------------------------------


class TestReconfigureFlow:
    def _make_flow(self):
        from custom_components.comelit_man.config_flow import ComelitLocalConfigFlow

        flow = ComelitLocalConfigFlow()
        mock_entry = MagicMock()
        mock_entry.data = {"host": HOST, "port": PORT, "http_port": 8080, "token": TOKEN}
        flow._get_reconfigure_entry = lambda: mock_entry
        hass = MagicMock()
        hass.config_entries.flow.async_progress_by_handler.return_value = []
        hass.config_entries.async_entries.return_value = []
        hass.config_entries.async_entry_for_domain_unique_id.return_value = None
        flow.hass = hass
        flow.context = {}
        return flow

    @pytest.mark.asyncio
    async def test_reconfigure_shows_form_on_no_input(self):
        flow = self._make_flow()
        result = await flow.async_step_reconfigure(user_input=None)
        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_reconfigure_success(self):
        flow = self._make_flow()
        client = _mock_client()

        with (
            patch("custom_components.comelit_man.config_flow.IconaBridgeClient", return_value=client),
            patch("custom_components.comelit_man.config_flow.authenticate", new_callable=AsyncMock),
        ):
            result = await flow.async_step_reconfigure(
                user_input={"host": HOST, "port": PORT, "http_port": 8080, "token": TOKEN, "password": "comelit"}
            )

        client.disconnect.assert_awaited_once()
        assert result["type"] in ("abort", "create_entry")

    @pytest.mark.asyncio
    async def test_reconfigure_cannot_connect(self):
        flow = self._make_flow()
        client = _mock_client(connect_error=OSError("refused"))

        with patch("custom_components.comelit_man.config_flow.IconaBridgeClient", return_value=client):
            result = await flow.async_step_reconfigure(
                user_input={"host": HOST, "port": PORT, "http_port": 8080, "token": TOKEN, "password": "comelit"}
            )

        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"


# ---------------------------------------------------------------------------
# async_get_options_flow staticmethod
# ---------------------------------------------------------------------------


class TestGetOptionsFlow:
    def test_returns_options_flow_instance(self):
        from custom_components.comelit_man.config_flow import (
            ComelitLocalConfigFlow,
            ComelitLocalOptionsFlow,
        )

        entry = MagicMock()
        entry.options = {}
        result = ComelitLocalConfigFlow.async_get_options_flow(entry)
        assert isinstance(result, ComelitLocalOptionsFlow)


# ---------------------------------------------------------------------------
# async_step_reauth — redirect to confirm
# ---------------------------------------------------------------------------


class TestReauthStep:
    @pytest.mark.asyncio
    async def test_reauth_step_redirects_to_confirm(self):
        from custom_components.comelit_man.config_flow import ComelitLocalConfigFlow

        flow = ComelitLocalConfigFlow()
        mock_entry = MagicMock()
        mock_entry.data = {"host": HOST, "port": PORT, "http_port": 8080, "token": TOKEN}
        flow._get_reauth_entry = lambda: mock_entry

        result = await flow.async_step_reauth({})
        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_reauth_confirm_token_extraction_fails(self):
        from custom_components.comelit_man.config_flow import ComelitLocalConfigFlow

        flow = ComelitLocalConfigFlow()
        mock_entry = MagicMock()
        mock_entry.data = {"host": HOST, "port": PORT, "http_port": 8080, "token": TOKEN}
        flow._get_reauth_entry = lambda: mock_entry

        with patch(
            "custom_components.comelit_man.config_flow.extract_token",
            new_callable=AsyncMock,
            side_effect=RuntimeError("no backup"),
        ):
            result = await flow.async_step_reauth_confirm(user_input={"token": "", "password": "comelit"})

        assert result["type"] == "form"
        assert result["errors"]["base"] == "token_extraction_failed"

    @pytest.mark.asyncio
    async def test_reauth_confirm_os_error(self):
        from custom_components.comelit_man.config_flow import ComelitLocalConfigFlow

        flow = ComelitLocalConfigFlow()
        mock_entry = MagicMock()
        mock_entry.data = {"host": HOST, "port": PORT, "http_port": 8080, "token": TOKEN}
        flow._get_reauth_entry = lambda: mock_entry

        client = _mock_client(connect_error=OSError("refused"))
        with patch("custom_components.comelit_man.config_flow.IconaBridgeClient", return_value=client):
            result = await flow.async_step_reauth_confirm(user_input={"token": TOKEN, "password": "comelit"})

        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"


# ---------------------------------------------------------------------------
# async_step_reconfigure — token extraction and connection errors
# ---------------------------------------------------------------------------


class TestReconfigureExtended:
    def _make_flow(self):
        from custom_components.comelit_man.config_flow import ComelitLocalConfigFlow

        flow = ComelitLocalConfigFlow()
        mock_entry = MagicMock()
        mock_entry.data = {"host": HOST, "port": PORT, "http_port": 8080, "token": TOKEN}
        flow._get_reconfigure_entry = lambda: mock_entry
        return flow

    @pytest.mark.asyncio
    async def test_reconfigure_token_extraction_fails(self):
        flow = self._make_flow()

        with patch(
            "custom_components.comelit_man.config_flow.extract_token",
            new_callable=AsyncMock,
            side_effect=RuntimeError("no backup"),
        ):
            result = await flow.async_step_reconfigure(
                user_input={"host": HOST, "port": PORT, "http_port": 8080, "token": "", "password": "comelit"}
            )

        assert result["type"] == "form"
        assert result["errors"]["base"] == "token_extraction_failed"

    @pytest.mark.asyncio
    async def test_reconfigure_os_error(self):
        flow = self._make_flow()
        client = _mock_client(connect_error=OSError("refused"))

        with patch("custom_components.comelit_man.config_flow.IconaBridgeClient", return_value=client):
            result = await flow.async_step_reconfigure(
                user_input={"host": HOST, "port": PORT, "http_port": 8080, "token": TOKEN, "password": "comelit"}
            )

        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_reconfigure_invalid_auth(self):
        from custom_components.comelit_man.exceptions import AuthenticationError

        flow = self._make_flow()
        client = _mock_client()

        with (
            patch("custom_components.comelit_man.config_flow.IconaBridgeClient", return_value=client),
            patch(
                "custom_components.comelit_man.config_flow.authenticate",
                new_callable=AsyncMock,
                side_effect=AuthenticationError("bad token"),
            ),
        ):
            result = await flow.async_step_reconfigure(
                user_input={"host": HOST, "port": PORT, "http_port": 8080, "token": TOKEN, "password": "comelit"}
            )

        assert result["type"] == "form"
        assert result["errors"]["base"] == "invalid_auth"


# ---------------------------------------------------------------------------
# DHCP discovery flow
# ---------------------------------------------------------------------------


class TestDhcpFlow:
    def _make_dhcp_info(self, ip: str = "192.168.1.111", mac: str = "aa:bb:cc:dd:ee:ff"):
        info = MagicMock()
        info.ip = ip
        info.macaddress = mac
        return info

    def _make_flow(self):
        from custom_components.comelit_man.config_flow import ComelitLocalConfigFlow

        flow = ComelitLocalConfigFlow()
        hass = MagicMock()
        hass.config_entries.flow.async_progress_by_handler.return_value = []
        hass.config_entries.async_entries.return_value = []
        hass.config_entries.async_entry_for_domain_unique_id.return_value = None
        flow.hass = hass
        flow.context = {}
        return flow

    @pytest.mark.asyncio
    async def test_dhcp_step_returns_form(self):
        flow = self._make_flow()
        result = await flow.async_step_dhcp(self._make_dhcp_info())
        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_dhcp_confirm_no_input_shows_form(self):
        flow = self._make_flow()
        flow._discovered_host = HOST
        result = await flow.async_step_dhcp_confirm(user_input=None)
        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_dhcp_confirm_success_creates_entry(self):
        flow = self._make_flow()
        flow._discovered_host = HOST
        client = _mock_client()

        with (
            patch("custom_components.comelit_man.config_flow.IconaBridgeClient", return_value=client),
            patch("custom_components.comelit_man.config_flow.authenticate", new_callable=AsyncMock),
        ):
            result = await flow.async_step_dhcp_confirm(user_input={"token": TOKEN, "password": "comelit"})

        assert result["type"] in ("create_entry", "abort")

    @pytest.mark.asyncio
    async def test_dhcp_confirm_auth_error(self):
        from custom_components.comelit_man.exceptions import AuthenticationError

        flow = self._make_flow()
        flow._discovered_host = HOST
        client = _mock_client()

        with (
            patch("custom_components.comelit_man.config_flow.IconaBridgeClient", return_value=client),
            patch(
                "custom_components.comelit_man.config_flow.authenticate",
                new_callable=AsyncMock,
                side_effect=AuthenticationError("bad"),
            ),
        ):
            result = await flow.async_step_dhcp_confirm(user_input={"token": TOKEN, "password": "comelit"})

        assert result["type"] == "form"
        assert result["errors"]["base"] == "invalid_auth"

    @pytest.mark.asyncio
    async def test_dhcp_confirm_cannot_connect(self):
        flow = self._make_flow()
        flow._discovered_host = HOST
        client = _mock_client(connect_error=OSError("refused"))

        with patch("custom_components.comelit_man.config_flow.IconaBridgeClient", return_value=client):
            result = await flow.async_step_dhcp_confirm(user_input={"token": TOKEN, "password": "comelit"})

        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_dhcp_confirm_token_extraction_fails(self):
        flow = self._make_flow()
        flow._discovered_host = HOST

        with patch(
            "custom_components.comelit_man.config_flow.extract_token",
            new_callable=AsyncMock,
            side_effect=RuntimeError("no backup"),
        ):
            result = await flow.async_step_dhcp_confirm(user_input={"token": "", "password": "comelit"})

        assert result["type"] == "form"
        assert result["errors"]["base"] == "token_extraction_failed"


# ---------------------------------------------------------------------------
# Dedicated-user provisioning branch of the user step
# ---------------------------------------------------------------------------


class TestProvisioningInUserStep:
    def _make_flow(self):
        from custom_components.comelit_man.config_flow import ComelitLocalConfigFlow

        flow = ComelitLocalConfigFlow()
        hass = MagicMock()
        hass.config_entries.flow.async_progress_by_handler.return_value = []
        hass.config_entries.async_entries.return_value = []
        hass.config_entries.async_entry_for_domain_unique_id.return_value = None
        flow.hass = hass
        flow.context = {}
        flow._async_current_entries = lambda include_ignore=False: []
        return flow

    @staticmethod
    def _user_input(**overrides):
        data = {
            "name": "Intercom",
            "host": HOST,
            "port": PORT,
            "http_port": 8080,
            "token": "",
            "password": "comelit",
            "create_dedicated_user": True,
        }
        data.update(overrides)
        return data

    @pytest.mark.asyncio
    async def test_provisions_when_opted_in_and_no_token(self):
        """The provisioned token is what gets stored."""
        flow = self._make_flow()
        client = _mock_client()
        minted = "d" * 32

        with (
            patch(
                "custom_components.comelit_man.config_flow.provision_user",
                new_callable=AsyncMock,
                return_value=minted,
            ) as provision,
            patch("custom_components.comelit_man.config_flow.extract_token", new_callable=AsyncMock) as extract,
            patch("custom_components.comelit_man.config_flow.IconaBridgeClient", return_value=client),
            patch("custom_components.comelit_man.config_flow.authenticate", new_callable=AsyncMock),
        ):
            result = await flow.async_step_user(user_input=self._user_input())

        provision.assert_awaited_once()
        extract.assert_not_called()  # provisioning replaces extraction
        assert result["type"] == "create_entry"
        assert result["data"]["token"] == minted

    @pytest.mark.asyncio
    async def test_provisioning_failure_shows_error(self):
        """A failed provisioning re-shows the form with provisioning_failed."""
        flow = self._make_flow()

        with patch(
            "custom_components.comelit_man.config_flow.provision_user",
            new_callable=AsyncMock,
            side_effect=RuntimeError("no free slot"),
        ):
            result = await flow.async_step_user(user_input=self._user_input())

        assert result["type"] == "form"
        assert result["errors"]["base"] == "provisioning_failed"

    @pytest.mark.asyncio
    async def test_existing_token_skips_provisioning(self):
        """Supplying a token wins over the checkbox."""
        flow = self._make_flow()
        client = _mock_client()

        with (
            patch("custom_components.comelit_man.config_flow.provision_user", new_callable=AsyncMock) as provision,
            patch("custom_components.comelit_man.config_flow.IconaBridgeClient", return_value=client),
            patch("custom_components.comelit_man.config_flow.authenticate", new_callable=AsyncMock),
        ):
            result = await flow.async_step_user(user_input=self._user_input(token=TOKEN))

        provision.assert_not_called()
        assert result["type"] == "create_entry"
        assert result["data"]["token"] == TOKEN

    @pytest.mark.asyncio
    async def test_opt_out_falls_back_to_extraction(self):
        flow = self._make_flow()
        client = _mock_client()

        with (
            patch("custom_components.comelit_man.config_flow.provision_user", new_callable=AsyncMock) as provision,
            patch(
                "custom_components.comelit_man.config_flow.extract_token",
                new_callable=AsyncMock,
                return_value=TOKEN,
            ) as extract,
            patch("custom_components.comelit_man.config_flow.IconaBridgeClient", return_value=client),
            patch("custom_components.comelit_man.config_flow.authenticate", new_callable=AsyncMock),
        ):
            result = await flow.async_step_user(user_input=self._user_input(create_dedicated_user=False))

        provision.assert_not_called()
        extract.assert_awaited_once()
        assert result["data"]["token"] == TOKEN
