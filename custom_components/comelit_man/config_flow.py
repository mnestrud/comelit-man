"""Config flow for Comelit Local integration."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_TOKEN

if TYPE_CHECKING:
    from homeassistant.components.dhcp import DhcpServiceInfo

from .auth import authenticate
from .client import IconaBridgeClient
from .const import CONF_ENABLE_NOTIFICATIONS, CONF_HTTP_PORT, DEFAULT_HTTP_PORT, DEFAULT_PORT, DOMAIN
from .exceptions import (
    AuthenticationError,
    ConnectionComelitError as ComelitConnectionError,
)
from .token import extract_token

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional("name", default=""): str,
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_HTTP_PORT, default=DEFAULT_HTTP_PORT): int,
        vol.Optional(CONF_TOKEN, default=""): str,
        vol.Optional(CONF_PASSWORD, default="comelit"): str,
    }
)


class ComelitLocalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Comelit Local."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler."""
        return ComelitLocalOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input.get("name", "").strip()
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            http_port = user_input.get(CONF_HTTP_PORT, DEFAULT_HTTP_PORT)
            token = user_input.get(CONF_TOKEN, "").strip()
            password = user_input.get(CONF_PASSWORD, "comelit")
            title = name if name else f"Comelit {host}"

            # Auto-extract token if not provided
            if not token:
                try:
                    token = await extract_token(host, password, http_port, self.hass)
                except Exception as err:
                    _LOGGER.exception("Token extraction failed: %s", err)  # nosemgrep: python-logger-credential-disclosure
                    errors["base"] = "token_extraction_failed"

            if not errors:
                client = IconaBridgeClient(host, port)
                try:
                    await asyncio.wait_for(client.connect(), timeout=10)
                    await asyncio.wait_for(authenticate(client, token), timeout=10)
                except AuthenticationError:
                    _LOGGER.warning("Authentication failed for %s", host)
                    errors["base"] = "invalid_auth"
                except (TimeoutError, ComelitConnectionError, OSError) as err:
                    _LOGGER.warning("Connection failed for %s: %s", host, err)
                    errors["base"] = "cannot_connect"
                finally:
                    await client.disconnect()

            if not errors:
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_TOKEN: token,
                        CONF_HTTP_PORT: http_port,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> config_entries.ConfigFlowResult:
        """Handle DHCP discovery of a Comelit device."""
        host = discovery_info.ip
        mac = discovery_info.macaddress
        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        self.context["title_placeholders"] = {"host": host}
        self._discovered_host = host
        return await self.async_step_dhcp_confirm()

    async def async_step_dhcp_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm DHCP-discovered device and collect credentials."""
        errors: dict[str, str] = {}
        host = self._discovered_host
        port = DEFAULT_PORT
        http_port = DEFAULT_HTTP_PORT

        if user_input is not None:
            token = user_input.get(CONF_TOKEN, "").strip()
            password = user_input.get(CONF_PASSWORD, "comelit")

            if not token:
                try:
                    token = await extract_token(host, password, http_port, self.hass)
                except Exception as err:
                    _LOGGER.exception("Token extraction failed: %s", err)  # nosemgrep: python-logger-credential-disclosure
                    errors["base"] = "token_extraction_failed"

            if not errors:
                client = IconaBridgeClient(host, port)
                try:
                    await asyncio.wait_for(client.connect(), timeout=10)
                    await asyncio.wait_for(authenticate(client, token), timeout=10)
                except AuthenticationError:
                    _LOGGER.warning("Authentication failed for discovered device %s", host)
                    errors["base"] = "invalid_auth"
                except (TimeoutError, ComelitConnectionError, OSError) as err:
                    _LOGGER.warning("Connection failed for discovered device %s: %s", host, err)
                    errors["base"] = "cannot_connect"
                finally:
                    await client.disconnect()

            if not errors:
                return self.async_create_entry(
                    title=f"Comelit {host}",
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_TOKEN: token,
                        CONF_HTTP_PORT: http_port,
                    },
                )

        return self.async_show_form(
            step_id="dhcp_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_TOKEN, default=""): str,
                    vol.Optional(CONF_PASSWORD, default="comelit"): str,
                }
            ),
            description_placeholders={"host": host},
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Initiate reauthentication when the stored token becomes invalid."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle reauthentication — re-enter token or password."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        host = reauth_entry.data[CONF_HOST]
        port = reauth_entry.data.get(CONF_PORT, DEFAULT_PORT)
        http_port = reauth_entry.data.get(CONF_HTTP_PORT, DEFAULT_HTTP_PORT)

        if user_input is not None:
            token = user_input.get(CONF_TOKEN, "").strip()
            password = user_input.get(CONF_PASSWORD, "comelit")

            if not token:
                try:
                    token = await extract_token(host, password, http_port, self.hass)
                except Exception as err:
                    _LOGGER.exception("Token extraction failed during reauth: %s", err)  # nosemgrep: python-logger-credential-disclosure
                    errors["base"] = "token_extraction_failed"

            if not errors:
                client = IconaBridgeClient(host, port)
                try:
                    await asyncio.wait_for(client.connect(), timeout=10)
                    await asyncio.wait_for(authenticate(client, token), timeout=10)
                except AuthenticationError:
                    _LOGGER.warning("Reauth failed for %s", host)
                    errors["base"] = "invalid_auth"
                except (TimeoutError, ComelitConnectionError, OSError) as err:
                    _LOGGER.warning("Connection failed during reauth for %s: %s", host, err)
                    errors["base"] = "cannot_connect"
                finally:
                    await client.disconnect()

            if not errors:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_TOKEN: token},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_TOKEN, default=""): str,
                    vol.Optional(CONF_PASSWORD, default="comelit"): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle reconfiguration — change host/port/token without delete+re-add."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()
        current = reconfigure_entry.data

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input.get(CONF_PORT, DEFAULT_PORT)
            http_port = user_input.get(CONF_HTTP_PORT, DEFAULT_HTTP_PORT)
            token = user_input.get(CONF_TOKEN, "").strip()
            password = user_input.get(CONF_PASSWORD, "comelit")

            if not token:
                try:
                    token = await extract_token(host, password, http_port, self.hass)
                except Exception as err:
                    _LOGGER.exception("Token extraction failed during reconfigure: %s", err)  # nosemgrep: python-logger-credential-disclosure
                    errors["base"] = "token_extraction_failed"

            if not errors:
                client = IconaBridgeClient(host, port)
                try:
                    await asyncio.wait_for(client.connect(), timeout=10)
                    await asyncio.wait_for(authenticate(client, token), timeout=10)
                except AuthenticationError:
                    _LOGGER.warning("Authentication failed during reconfigure for %s", host)
                    errors["base"] = "invalid_auth"
                except (TimeoutError, ComelitConnectionError, OSError) as err:
                    _LOGGER.warning("Connection failed during reconfigure for %s: %s", host, err)
                    errors["base"] = "cannot_connect"
                finally:
                    await client.disconnect()

            if not errors:
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_mismatch(reason="another_device")
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    data_updates={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_TOKEN: token,
                        CONF_HTTP_PORT: http_port,
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=current.get(CONF_HOST, "")): str,
                    vol.Optional(CONF_PORT, default=current.get(CONF_PORT, DEFAULT_PORT)): int,
                    vol.Optional(CONF_HTTP_PORT, default=current.get(CONF_HTTP_PORT, DEFAULT_HTTP_PORT)): int,
                    vol.Optional(CONF_TOKEN, default=""): str,
                    vol.Optional(CONF_PASSWORD, default="comelit"): str,
                }
            ),
            errors=errors,
        )


class ComelitLocalOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Comelit Local (e.g. enable/disable notifications)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show the options form."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._config_entry.options.get(CONF_ENABLE_NOTIFICATIONS, True)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_ENABLE_NOTIFICATIONS, default=current): bool}
            ),
        )
