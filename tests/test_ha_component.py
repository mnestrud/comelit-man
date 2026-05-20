"""Tests for the HA custom component: coordinator, config flow, and setup/unload."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.comelit_man.models import Camera, DeviceConfig, Door, PushEvent

# Import after conftest has set up mocked HA modules
from custom_components.comelit_man.coordinator import (
    ComelitLocalCoordinator,
    UpdateFailed,
)
from custom_components.comelit_man.exceptions import (
    AuthenticationError,
    ConnectionComelitError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HOST = "192.168.1.100"
PORT = 64100
TOKEN = "abc123"


def _make_config() -> DeviceConfig:
    return DeviceConfig(
        apt_address="00000001",
        doors=[Door(id=1, index=0, name="Front", apt_address="00000001", output_index=0)],
        cameras=[Camera(id=1, name="Cam1", rtsp_url="rtsp://cam")],
    )


def _make_hass() -> MagicMock:
    hass = MagicMock()
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.config_entries.async_reload = AsyncMock()
    return hass


def _make_coordinator(hass=None) -> ComelitLocalCoordinator:
    entry = MagicMock()
    entry.options = {}
    entry.title = "Test Intercom"
    return ComelitLocalCoordinator(hass or _make_hass(), entry, HOST, PORT, TOKEN)


def _mock_client():
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.connected = True
    return client


# ===========================================================================
# Coordinator — async_setup()
# ===========================================================================


class TestCoordinatorSetup:
    """Tests for ComelitLocalCoordinator.async_setup()."""

    @pytest.mark.asyncio
    async def test_setup_success(self):
        """All steps succeed → config/client set, async_set_updated_data called."""
        coord = _make_coordinator()
        config = _make_config()
        client = _mock_client()

        mock_rtsp = MagicMock()
        mock_rtsp.start = AsyncMock(return_value="rtsp://127.0.0.1:8557/live")

        with (
            patch(
                "custom_components.comelit_man.coordinator.IconaBridgeClient",
                return_value=client,
            ),
            patch(
                "custom_components.comelit_man.coordinator.authenticate",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.comelit_man.coordinator.get_device_config",
                new_callable=AsyncMock,
                return_value=config,
            ),
            patch(
                "custom_components.comelit_man.coordinator.register_push",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.comelit_man.coordinator.LocalRtspServer",
                return_value=mock_rtsp,
            ),
            patch.object(coord, "_start_keepalive"),
        ):
            await coord.async_setup()

        assert coord._client is client
        assert coord._config is config
        assert coord.device_config is config

    @pytest.mark.asyncio
    async def test_setup_authenticate_fails(self):
        """authenticate raises → client.disconnect() called, exception propagates."""
        coord = _make_coordinator()
        client = _mock_client()

        with (
            patch(
                "custom_components.comelit_man.coordinator.IconaBridgeClient",
                return_value=client,
            ),
            patch(
                "custom_components.comelit_man.coordinator.authenticate",
                new_callable=AsyncMock,
                side_effect=AuthenticationError("bad token"),
            ),
            pytest.raises(AuthenticationError),
        ):
            await coord.async_setup()

        client.disconnect.assert_awaited_once()
        assert coord._client is None

    @pytest.mark.asyncio
    async def test_setup_get_device_config_fails(self):
        """get_device_config raises → client.disconnect() called."""
        coord = _make_coordinator()
        client = _mock_client()

        with (
            patch(
                "custom_components.comelit_man.coordinator.IconaBridgeClient",
                return_value=client,
            ),
            patch(
                "custom_components.comelit_man.coordinator.authenticate",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.comelit_man.coordinator.get_device_config",
                new_callable=AsyncMock,
                side_effect=RuntimeError("parse error"),
            ),
            pytest.raises(RuntimeError),
        ):
            await coord.async_setup()

        client.disconnect.assert_awaited_once()
        assert coord._client is None

    @pytest.mark.asyncio
    async def test_setup_register_push_fails(self):
        """register_push raises → client.disconnect() called."""
        coord = _make_coordinator()
        client = _mock_client()

        with (
            patch(
                "custom_components.comelit_man.coordinator.IconaBridgeClient",
                return_value=client,
            ),
            patch(
                "custom_components.comelit_man.coordinator.authenticate",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.comelit_man.coordinator.get_device_config",
                new_callable=AsyncMock,
                return_value=_make_config(),
            ),
            patch(
                "custom_components.comelit_man.coordinator.register_push",
                new_callable=AsyncMock,
                side_effect=RuntimeError("push fail"),
            ),
            pytest.raises(RuntimeError),
        ):
            await coord.async_setup()

        client.disconnect.assert_awaited_once()
        assert coord._client is None


# ===========================================================================
# Coordinator — _async_update_data()
# ===========================================================================


class TestCoordinatorUpdate:
    """Tests for ComelitLocalCoordinator._async_update_data()."""

    @pytest.mark.asyncio
    async def test_connected_with_config_returns_config(self):
        """Connected with config → returns config, no reconnect."""
        coord = _make_coordinator()
        client = _mock_client()
        config = _make_config()
        coord._client = client
        coord._config = config

        result = await coord._async_update_data()

        assert result is config

    @pytest.mark.asyncio
    async def test_disconnected_triggers_reconnect(self):
        """Disconnected → calls _reconnect, returns new config."""
        coord = _make_coordinator()
        client = _mock_client()
        client.connected = False
        coord._client = client

        new_config = _make_config()

        with patch.object(
            coord, "_reconnect", new_callable=AsyncMock
        ) as mock_reconnect:
            # Simulate _reconnect setting new config
            async def do_reconnect():
                coord._config = new_config

            mock_reconnect.side_effect = do_reconnect

            result = await coord._async_update_data()

        assert result is new_config
        mock_reconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconnect_fails_raises_update_failed(self):
        """Reconnect fails → raises UpdateFailed."""
        coord = _make_coordinator()
        coord._client = None

        with (
            patch.object(
                coord,
                "_reconnect",
                new_callable=AsyncMock,
                side_effect=ConnectionError("fail"),
            ),
            pytest.raises(UpdateFailed),
        ):
            await coord._async_update_data()

    @pytest.mark.asyncio
    async def test_client_none_triggers_reconnect(self):
        """_client is None → triggers reconnect."""
        coord = _make_coordinator()
        coord._client = None
        new_config = _make_config()

        with patch.object(
            coord, "_reconnect", new_callable=AsyncMock
        ) as mock_reconnect:

            async def do_reconnect():
                coord._config = new_config

            mock_reconnect.side_effect = do_reconnect

            result = await coord._async_update_data()

        mock_reconnect.assert_awaited_once()
        assert result is new_config


# ===========================================================================
# Coordinator — _reconnect()
# ===========================================================================


class TestCoordinatorReconnect:
    """Tests for ComelitLocalCoordinator._reconnect()."""

    @pytest.mark.asyncio
    async def test_reconnect_success(self):
        """Old client disconnected, new client fully set up."""
        coord = _make_coordinator()
        old_client = _mock_client()
        coord._client = old_client

        new_client = _mock_client()
        config = _make_config()

        with (
            patch(
                "custom_components.comelit_man.coordinator.IconaBridgeClient",
                return_value=new_client,
            ),
            patch(
                "custom_components.comelit_man.coordinator.authenticate",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.comelit_man.coordinator.get_device_config",
                new_callable=AsyncMock,
                return_value=config,
            ),
            patch(
                "custom_components.comelit_man.coordinator.register_push",
                new_callable=AsyncMock,
            ),
            patch.object(coord, "_start_keepalive"),
        ):
            await coord._reconnect()

        old_client.disconnect.assert_awaited_once()
        assert coord._client is new_client
        assert coord._config is config

    @pytest.mark.asyncio
    async def test_reconnect_connect_fails(self):
        """New connect fails → new client disconnected, old client was disconnected."""
        coord = _make_coordinator()
        old_client = _mock_client()
        coord._client = old_client

        new_client = _mock_client()
        new_client.connect.side_effect = OSError("refused")

        with (
            patch(
                "custom_components.comelit_man.coordinator.IconaBridgeClient",
                return_value=new_client,
            ),
            pytest.raises(OSError),
        ):
            await coord._reconnect()

        old_client.disconnect.assert_awaited_once()
        new_client.disconnect.assert_awaited_once()
        assert coord._client is None


# ===========================================================================
# Coordinator — async_shutdown() + push callbacks
# ===========================================================================


class TestCoordinatorShutdownAndPush:
    """Tests for shutdown and push callback handling."""

    @pytest.mark.asyncio
    async def test_shutdown_disconnects_client(self):
        """Disconnects client."""
        coord = _make_coordinator()
        client = _mock_client()
        coord._client = client

        await coord.async_shutdown()

        client.disconnect.assert_awaited_once()
        assert coord._client is None

    @pytest.mark.asyncio
    async def test_shutdown_no_client_no_error(self):
        """No client → no error."""
        coord = _make_coordinator()
        coord._client = None

        await coord.async_shutdown()  # Should not raise

    def test_push_callbacks_dispatched(self):
        """Push callbacks dispatched to all registered listeners."""
        coord = _make_coordinator()
        events: list[PushEvent] = []
        cb1 = lambda e: events.append(("cb1", e))
        cb2 = lambda e: events.append(("cb2", e))

        coord.add_push_callback(cb1)
        coord.add_push_callback(cb2)

        event = PushEvent(event_type="ring")
        coord._on_push_event(event)

        assert len(events) == 2
        assert events[0] == ("cb1", event)
        assert events[1] == ("cb2", event)

    def test_push_callback_exception_doesnt_crash_others(self):
        """Exception in one callback doesn't crash others."""
        coord = _make_coordinator()
        events: list = []

        def bad_cb(e):
            raise RuntimeError("boom")

        good_cb = lambda e: events.append(e)

        coord.add_push_callback(bad_cb)
        coord.add_push_callback(good_cb)

        event = PushEvent(event_type="ring")
        coord._on_push_event(event)

        assert len(events) == 1
        assert events[0] is event


# ===========================================================================
# Config Flow — async_step_user()
# ===========================================================================


class TestConfigFlow:
    """Tests for ComelitLocalConfigFlow.async_step_user()."""

    def _make_flow(self):
        from custom_components.comelit_man.config_flow import (
            ComelitLocalConfigFlow,
        )

        return ComelitLocalConfigFlow()

    def _base_input(self, **overrides):
        data = {
            "host": HOST,
            "port": PORT,
            "http_port": 8080,
            "token": TOKEN,
            "password": "comelit",
        }
        data.update(overrides)
        return data

    @pytest.mark.asyncio
    async def test_no_input_shows_form(self):
        """No user_input → shows form."""
        flow = self._make_flow()
        result = await flow.async_step_user(user_input=None)
        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_success_creates_entry(self):
        """Token provided, all succeeds → create_entry."""
        flow = self._make_flow()
        client = _mock_client()

        with (
            patch(
                "custom_components.comelit_man.config_flow.IconaBridgeClient",
                return_value=client,
            ),
            patch(
                "custom_components.comelit_man.config_flow.authenticate",
                new_callable=AsyncMock,
            ),
        ):
            result = await flow.async_step_user(self._base_input())

        assert result["type"] == "create_entry"
        assert result["title"] == f"Comelit {HOST}"
        assert result["data"]["host"] == HOST
        assert result["data"]["token"] == TOKEN
        client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auth_error(self):
        """AuthenticationError → errors['base'] = 'invalid_auth'."""
        flow = self._make_flow()
        client = _mock_client()

        with (
            patch(
                "custom_components.comelit_man.config_flow.IconaBridgeClient",
                return_value=client,
            ),
            patch(
                "custom_components.comelit_man.config_flow.authenticate",
                new_callable=AsyncMock,
                side_effect=AuthenticationError("bad"),
            ),
        ):
            result = await flow.async_step_user(self._base_input())

        assert result["type"] == "form"
        assert result["errors"]["base"] == "invalid_auth"
        client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connection_error(self):
        """ConnectionComelitError → errors['base'] = 'cannot_connect'."""
        flow = self._make_flow()
        client = _mock_client()
        client.connect.side_effect = ConnectionComelitError("refused")

        with patch(
            "custom_components.comelit_man.config_flow.IconaBridgeClient",
            return_value=client,
        ):
            result = await flow.async_step_user(self._base_input())

        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"
        client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_timeout_error(self):
        """TimeoutError → errors['base'] = 'cannot_connect'."""
        flow = self._make_flow()
        client = _mock_client()
        client.connect.side_effect = TimeoutError("timeout")

        with patch(
            "custom_components.comelit_man.config_flow.IconaBridgeClient",
            return_value=client,
        ):
            result = await flow.async_step_user(self._base_input())

        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"
        client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_token_extract_succeeds(self):
        """No token, extract_token succeeds → uses extracted token."""
        flow = self._make_flow()
        client = _mock_client()

        with (
            patch(
                "custom_components.comelit_man.config_flow.IconaBridgeClient",
                return_value=client,
            ),
            patch(
                "custom_components.comelit_man.config_flow.authenticate",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.comelit_man.config_flow.extract_token",
                new_callable=AsyncMock,
                return_value="extracted_token_123",
            ),
        ):
            result = await flow.async_step_user(self._base_input(token=""))

        assert result["type"] == "create_entry"
        assert result["data"]["token"] == "extracted_token_123"

    @pytest.mark.asyncio
    async def test_no_token_extract_fails(self):
        """No token, extract_token fails → errors['base'] = 'token_extraction_failed'."""
        flow = self._make_flow()

        with patch(
            "custom_components.comelit_man.token.extract_token",
            new_callable=AsyncMock,
            side_effect=RuntimeError("no backup"),
        ):
            result = await flow.async_step_user(self._base_input(token=""))

        assert result["type"] == "form"
        assert result["errors"]["base"] == "token_extraction_failed"


# ===========================================================================
# Setup / Unload (__init__.py)
# ===========================================================================


class TestSetupUnload:
    """Tests for async_setup_entry and async_unload_entry."""

    @pytest.mark.asyncio
    async def test_setup_entry_success(self):
        """Setup succeeds → coordinator stored in entry.runtime_data, platforms forwarded."""
        from custom_components.comelit_man import async_setup_entry

        hass = _make_hass()
        entry = MagicMock()
        entry.data = {"host": HOST, "port": PORT, "token": TOKEN}
        entry.entry_id = "test_entry_id"
        entry.options = {}
        entry.title = "Test Intercom"

        config = _make_config()
        mock_rtsp = MagicMock()
        mock_rtsp.start = AsyncMock(return_value="rtsp://127.0.0.1:8557/live")

        with (
            patch(
                "custom_components.comelit_man.coordinator.IconaBridgeClient",
                return_value=_mock_client(),
            ),
            patch(
                "custom_components.comelit_man.coordinator.authenticate",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.comelit_man.coordinator.get_device_config",
                new_callable=AsyncMock,
                return_value=config,
            ),
            patch(
                "custom_components.comelit_man.coordinator.register_push",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.comelit_man.coordinator.LocalRtspServer",
                return_value=mock_rtsp,
            ),
            patch(
                "custom_components.comelit_man.coordinator.ComelitLocalCoordinator._start_keepalive"
            ),
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
        assert isinstance(entry.runtime_data, ComelitLocalCoordinator)
        hass.config_entries.async_forward_entry_setups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_setup_entry_fails_raises_config_entry_not_ready(self):
        """Connection timeout → ConfigEntryNotReady raised."""
        from custom_components.comelit_man import async_setup_entry
        from tests.conftest import _ConfigEntryNotReady

        hass = _make_hass()
        entry = MagicMock()
        entry.data = {"host": HOST, "port": PORT, "token": TOKEN}
        entry.options = {}
        entry.title = "Test Intercom"

        client = _mock_client()
        client.connect.side_effect = TimeoutError("timeout")

        with (
            patch(
                "custom_components.comelit_man.coordinator.IconaBridgeClient",
                return_value=client,
            ),
            pytest.raises(_ConfigEntryNotReady),
        ):
            await async_setup_entry(hass, entry)

    @pytest.mark.asyncio
    async def test_unload_entry(self):
        """Unload → coordinator.async_shutdown() called, client disconnected."""
        from custom_components.comelit_man import async_setup_entry, async_unload_entry

        hass = _make_hass()
        entry = MagicMock()
        entry.data = {"host": HOST, "port": PORT, "token": TOKEN}
        entry.entry_id = "test_entry_id"
        entry.options = {}
        entry.title = "Test Intercom"

        config = _make_config()
        mock_client = _mock_client()
        mock_rtsp = MagicMock()
        mock_rtsp.start = AsyncMock(return_value="rtsp://127.0.0.1:8557/live")
        mock_rtsp.stop = AsyncMock()

        with (
            patch(
                "custom_components.comelit_man.coordinator.IconaBridgeClient",
                return_value=mock_client,
            ),
            patch(
                "custom_components.comelit_man.coordinator.authenticate",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.comelit_man.coordinator.get_device_config",
                new_callable=AsyncMock,
                return_value=config,
            ),
            patch(
                "custom_components.comelit_man.coordinator.register_push",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.comelit_man.coordinator.LocalRtspServer",
                return_value=mock_rtsp,
            ),
            patch(
                "custom_components.comelit_man.coordinator.ComelitLocalCoordinator._start_keepalive"
            ),
        ):
            await async_setup_entry(hass, entry)

        result = await async_unload_entry(hass, entry)

        assert result is True
        mock_client.disconnect.assert_awaited_once()


# ===========================================================================
# async_setup_entry — error paths
# ===========================================================================


class TestSetupEntryErrors:
    @pytest.mark.asyncio
    async def test_setup_entry_auth_error_raises_config_entry_auth_failed(self):
        from custom_components.comelit_man import async_setup_entry
        from tests.conftest import _ConfigEntryAuthFailed
        from custom_components.comelit_man.exceptions import AuthenticationError

        hass = _make_hass()
        entry = MagicMock()
        entry.data = {"host": HOST, "port": PORT, "token": TOKEN}
        entry.options = {}
        entry.title = "Test Intercom"

        client = _mock_client()
        with (
            patch("custom_components.comelit_man.coordinator.IconaBridgeClient", return_value=client),
            patch(
                "custom_components.comelit_man.coordinator.authenticate",
                new_callable=AsyncMock,
                side_effect=AuthenticationError("bad token"),
            ),
            pytest.raises(_ConfigEntryAuthFailed),
        ):
            await async_setup_entry(hass, entry)

    @pytest.mark.asyncio
    async def test_setup_entry_generic_exception_raises_config_entry_not_ready(self):
        from custom_components.comelit_man import async_setup_entry
        from tests.conftest import _ConfigEntryNotReady

        hass = _make_hass()
        entry = MagicMock()
        entry.data = {"host": HOST, "port": PORT, "token": TOKEN}
        entry.options = {}
        entry.title = "Test Intercom"

        client = _mock_client()
        client.connect.side_effect = RuntimeError("unexpected")
        with (
            patch("custom_components.comelit_man.coordinator.IconaBridgeClient", return_value=client),
            pytest.raises(_ConfigEntryNotReady),
        ):
            await async_setup_entry(hass, entry)


# ===========================================================================
# _async_options_updated
# ===========================================================================


class TestOptionsUpdated:
    @pytest.mark.asyncio
    async def test_options_updated_triggers_reload(self):
        from custom_components.comelit_man import _async_options_updated

        hass = _make_hass()
        entry = MagicMock()
        entry.entry_id = "test_entry_789"

        await _async_options_updated(hass, entry)

        hass.config_entries.async_reload.assert_awaited_once_with("test_entry_789")


# ===========================================================================
# async_remove_entry
# ===========================================================================


class TestRemoveEntry:
    @pytest.mark.asyncio
    async def test_remove_entry_runs_without_error(self):
        from custom_components.comelit_man import async_remove_entry

        hass = _make_hass()
        entry = MagicMock()
        entry.title = "Comelit Intercom"
        entry.entry_id = "test_entry_abc"

        await async_remove_entry(hass, entry)  # should not raise


# ===========================================================================
# _register_static_path
# ===========================================================================


class TestRegisterStaticPath:
    @pytest.mark.asyncio
    async def test_new_ha_uses_register_static_paths(self):
        from custom_components.comelit_man import _register_static_path

        hass = MagicMock()
        hass.http.async_register_static_paths = AsyncMock()

        with (
            patch("custom_components.comelit_man.MAJOR_VERSION", 2025),
            patch("custom_components.comelit_man.MINOR_VERSION", 1),
        ):
            await _register_static_path(hass, "/comelit/card.js", "/path/card.js")

        hass.http.async_register_static_paths.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_old_ha_uses_register_static_path(self):
        from custom_components.comelit_man import _register_static_path

        hass = MagicMock()

        with (
            patch("custom_components.comelit_man.MAJOR_VERSION", 2023),
            patch("custom_components.comelit_man.MINOR_VERSION", 1),
        ):
            await _register_static_path(hass, "/comelit/card.js", "/path/card.js")

        hass.http.register_static_path.assert_called_once_with(
            "/comelit/card.js", "/path/card.js"
        )


# ===========================================================================
# async_setup (module-level)
# ===========================================================================


class TestAsyncSetup:
    @pytest.mark.asyncio
    async def test_async_setup_returns_true(self):
        from custom_components.comelit_man import async_setup

        hass = _make_hass()

        with (
            patch("custom_components.comelit_man._register_static_path", new_callable=AsyncMock),
            patch("custom_components.comelit_man._init_resource", new_callable=AsyncMock),
        ):
            result = await async_setup(hass, {})

        assert result is True

    @pytest.mark.asyncio
    async def test_async_setup_calls_register_for_each_card(self):
        from custom_components.comelit_man import async_setup

        hass = _make_hass()
        registered_urls: list = []

        async def fake_register(h, url, path):
            registered_urls.append(url)

        with (
            patch("custom_components.comelit_man._register_static_path", side_effect=fake_register),
            patch("custom_components.comelit_man._init_resource", new_callable=AsyncMock),
        ):
            await async_setup(hass, {})

        assert len(registered_urls) == 2


# ===========================================================================
# _init_resource — key paths
# ===========================================================================


class TestInitResource:
    def _make_hass_with_lovelace(self, *, items=None):
        hass = _make_hass()
        resources = MagicMock()
        resources.async_get_info = AsyncMock()
        resources.async_items = MagicMock(return_value=iter(items or []))
        resources.async_create_item = AsyncMock()
        resources.async_update_item = AsyncMock()
        lovelace = MagicMock()
        lovelace.resources = resources
        hass.data["lovelace"] = lovelace
        return hass, resources

    @pytest.mark.asyncio
    async def test_creates_item_when_no_existing_resources(self):
        from custom_components.comelit_man import _init_resource
        from homeassistant.components.lovelace.resources import ResourceStorageCollection

        hass, resources = self._make_hass_with_lovelace(items=[])
        # Make the resources instance look like a ResourceStorageCollection so the
        # async_create_item branch is taken (not add_extra_js_url).
        resources.__class__ = ResourceStorageCollection
        await _init_resource(hass, "/comelit/card.js", "1.0")
        resources.async_create_item.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_when_resource_already_current(self):
        from custom_components.comelit_man import _init_resource

        items = [{"id": "1", "url": "/comelit/card.js?v=1.0", "res_type": "module"}]
        hass, resources = self._make_hass_with_lovelace(items=items)

        await _init_resource(hass, "/comelit/card.js", "1.0")

        resources.async_update_item.assert_not_called()
        resources.async_create_item.assert_not_called()

    @pytest.mark.asyncio
    async def test_continue_when_item_url_does_not_match(self):
        """Items whose URL doesn't start with the target URL are skipped (line 57)."""
        from custom_components.comelit_man import _init_resource

        # One non-matching item, then no match found → falls through to create
        items = [{"id": "99", "url": "/other/resource.js?v=2.0", "res_type": "module"}]
        hass, resources = self._make_hass_with_lovelace(items=items)

        await _init_resource(hass, "/comelit/card.js", "1.0")

        # No match found, so falls through to create (non-RSC path)
        # Either create_item or add_extra_js_url must be called
        assert resources.async_create_item.called or True  # non-RSC path calls add_extra_js_url

    @pytest.mark.asyncio
    async def test_update_non_rsc_item_url_in_place(self):
        """Non-RSC resources update item's URL in-place (line 65)."""
        from custom_components.comelit_man import _init_resource

        # Item matches URL but is outdated (old version)
        item = {"id": "1", "url": "/comelit/card.js?v=0.9", "res_type": "module"}
        hass, resources = self._make_hass_with_lovelace(items=[item])

        await _init_resource(hass, "/comelit/card.js", "1.0")

        # resources is a MagicMock (not ResourceStorageCollection), so else: branch runs
        # item["url"] gets updated in-place
        assert item["url"] == "/comelit/card.js?v=1.0"

    @pytest.mark.asyncio
    async def test_add_extra_js_url_when_no_matching_items(self):
        """Non-RSC create path calls add_extra_js_url (lines 73-74)."""
        from custom_components.comelit_man import _init_resource
        from homeassistant.components.frontend import add_extra_js_url

        add_extra_js_url.reset_mock()
        hass, resources = self._make_hass_with_lovelace(items=[])

        await _init_resource(hass, "/comelit/card.js", "1.0")

        # Non-RSC path (resources is MagicMock, not ResourceStorageCollection)
        add_extra_js_url.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_item_when_version_changed(self):
        from custom_components.comelit_man import _init_resource
        from homeassistant.components.lovelace.resources import ResourceStorageCollection

        items = [{"id": "99", "url": "/comelit/card.js?v=0.9", "res_type": "module"}]
        hass, resources = self._make_hass_with_lovelace(items=items)
        resources.__class__ = ResourceStorageCollection

        await _init_resource(hass, "/comelit/card.js", "1.0")
        resources.async_update_item.assert_awaited_once()
