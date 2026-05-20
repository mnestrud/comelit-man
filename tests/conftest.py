"""Shared test fixtures.

Strategy: inject minimal homeassistant stubs into sys.modules only when the
real homeassistant package is NOT installed.  When pytest-homeassistant-
custom-component (or a plain ``pip install homeassistant``) is present, the
real HA modules are used and no injection happens.

This means:
  - CI (which installs pytest-homeassistant-custom-component) uses real HA types.
  - Quick local runs (without HA installed) use the stubs below.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Real exception / coordinator stubs used by tests regardless of HA presence.
# These must be *real* Python classes (not MagicMock) so raise/except works.
# When HA is installed these become aliases for the real HA classes;
# when not installed they are standalone stubs injected via sys.modules.
# ---------------------------------------------------------------------------


class _HomeAssistantError(Exception):
    """Stand-in for homeassistant.exceptions.HomeAssistantError."""

    def __init__(
        self,
        *args: object,
        translation_domain: str | None = None,
        translation_key: str | None = None,
        translation_placeholders: dict | None = None,
    ) -> None:
        super().__init__(*args)
        self.translation_domain = translation_domain
        self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders or {}


class _UpdateFailed(Exception):
    """Stand-in for homeassistant.helpers.update_coordinator.UpdateFailed."""


class _ConfigEntryNotReady(_HomeAssistantError):
    """Stand-in for homeassistant.exceptions.ConfigEntryNotReady."""


class _ConfigEntryAuthFailed(_HomeAssistantError):
    """Stand-in for homeassistant.exceptions.ConfigEntryAuthFailed."""


# ---------------------------------------------------------------------------
# Check whether homeassistant is already installed.
# If it is, alias the stubs to the real classes and skip sys.modules injection.
# ---------------------------------------------------------------------------

try:
    import homeassistant as _ha_pkg  # noqa: F401

    # Real HA is available — alias stubs to real classes so test imports work
    from homeassistant.exceptions import (  # noqa: F401
        HomeAssistantError as _HomeAssistantError,  # type: ignore[assignment]
        ConfigEntryNotReady as _ConfigEntryNotReady,  # type: ignore[assignment]
        ConfigEntryAuthFailed as _ConfigEntryAuthFailed,  # type: ignore[assignment]
    )
    from homeassistant.helpers.update_coordinator import (  # noqa: F401
        UpdateFailed as _UpdateFailed,  # type: ignore[assignment]
    )

    _HA_INSTALLED = True

except ImportError:
    _HA_INSTALLED = False

# ---------------------------------------------------------------------------
# Stub injection — only when HA is NOT installed.
# ---------------------------------------------------------------------------

if not _HA_INSTALLED:

    class _DataUpdateCoordinator:
        """Minimal stub for DataUpdateCoordinator."""

        def __class_getitem__(cls, item):
            return cls

        def __init__(self, hass, logger, *, name, update_interval, config_entry=None):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.config_entry = config_entry

        def async_set_updated_data(self, data):
            pass

    class _ConfigFlowResult(dict):
        pass

    class _ConfigFlow:
        domain: str = ""
        hass: MagicMock = MagicMock()

        def __init__(self) -> None:
            self.context: dict = {}

        def __init_subclass__(cls, domain: str = "", **kwargs):
            super().__init_subclass__(**kwargs)
            cls.domain = domain

        async def async_set_unique_id(self, uid):
            pass

        def _abort_if_unique_id_configured(self, **kwargs):
            pass

        def _abort_if_unique_id_mismatch(self, reason=None):
            pass

        def _get_reauth_entry(self):
            return MagicMock()

        def _get_reconfigure_entry(self):
            return MagicMock()

        def async_update_reload_and_abort(self, entry, *, data_updates=None):
            return _ConfigFlowResult(type="abort", reason="reauth_successful")

        def async_create_entry(self, *, title, data):
            return _ConfigFlowResult(type="create_entry", title=title, data=data)

        def async_show_form(self, *, step_id, data_schema, errors=None, **kwargs):
            return _ConfigFlowResult(
                type="form", step_id=step_id, data_schema=data_schema, errors=errors or {}
            )

    class _OptionsFlow:
        """Stub for homeassistant.config_entries.OptionsFlow."""

        def async_create_entry(self, *, title="", data):
            return _ConfigFlowResult(type="create_entry", title=title, data=data)

        def async_show_form(self, *, step_id, data_schema, errors=None, **kwargs):
            return _ConfigFlowResult(
                type="form", step_id=step_id, data_schema=data_schema, errors=errors or {}
            )

    class _CoordinatorEntity:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, coordinator, context=None):
            self.coordinator = coordinator

    class _Camera:
        _attr_has_entity_name = False
        _attr_name = None
        _attr_unique_id = None
        _attr_icon = None
        _attr_supported_features = 0

        def __init__(self):
            pass

        def async_write_ha_state(self):
            pass

    class _CameraEntityFeature:
        STREAM = 1
        ON_OFF = 2

    class _ButtonEntity:
        _attr_has_entity_name = False
        _attr_name = None
        _attr_unique_id = None

        async def async_press(self) -> None:
            pass

    class _EventEntity:
        _attr_has_entity_name = False
        _attr_name = None
        _attr_unique_id = None
        _attr_icon = None
        _attr_event_types: list = []
        _attr_translation_key: str | None = None

        def __init__(self):
            self._events: list = []

        def _trigger_event(self, event_type: str, data: dict | None = None) -> None:
            self._events.append({"event_type": event_type, "data": data or {}})

        def async_write_ha_state(self) -> None:
            pass

        def async_on_remove(self, func) -> None:
            pass

    # Build mock module tree
    _ha_exceptions = MagicMock()
    _ha_exceptions.HomeAssistantError = _HomeAssistantError
    _ha_exceptions.ConfigEntryNotReady = _ConfigEntryNotReady
    _ha_exceptions.ConfigEntryAuthFailed = _ConfigEntryAuthFailed

    _ha_update_coordinator = MagicMock()
    _ha_update_coordinator.DataUpdateCoordinator = _DataUpdateCoordinator
    _ha_update_coordinator.UpdateFailed = _UpdateFailed
    _ha_update_coordinator.CoordinatorEntity = _CoordinatorEntity

    _ha_config_entries = MagicMock()
    _ha_config_entries.ConfigFlow = _ConfigFlow
    _ha_config_entries.ConfigFlowResult = _ConfigFlowResult
    _ha_config_entries.OptionsFlow = _OptionsFlow

    _ha_const = MagicMock()
    _ha_const.CONF_HOST = "host"
    _ha_const.CONF_PORT = "port"
    _ha_const.CONF_TOKEN = "token"
    _ha_const.CONF_PASSWORD = "password"
    _ha_const.Platform = MagicMock()
    _ha_const.Platform.BUTTON = "button"
    _ha_const.Platform.CAMERA = "camera"
    _ha_const.Platform.EVENT = "event"

    _ha = MagicMock()
    _ha_helpers = MagicMock()
    _ha.config_entries = _ha_config_entries
    _ha.const = _ha_const
    _ha.core = MagicMock()
    _ha.exceptions = _ha_exceptions
    _ha.helpers = _ha_helpers
    _ha_helpers.update_coordinator = _ha_update_coordinator
    _ha.core.callback = lambda fn: fn

    _ha_camera = MagicMock()
    _ha_camera.Camera = _Camera
    _ha_camera.CameraEntityFeature = _CameraEntityFeature

    _ha_button = MagicMock()
    _ha_button.ButtonEntity = _ButtonEntity

    _ha_event = MagicMock()
    _ha_event.EventEntity = _EventEntity

    _ha_helpers_entity = MagicMock()
    _ha_helpers_entity.DeviceInfo = dict

    _ha_entity_platform = MagicMock()
    _ha_helpers_aiohttp = MagicMock()

    # Stubs for diagnostics and repairs platforms
    class _ConfirmRepairFlow:
        pass

    class _RepairsFlow:
        pass

    _ha_diagnostics = MagicMock()
    _ha_diagnostics.async_redact_data = lambda data, keys: {
        k: "**REDACTED**" if k in keys else v for k, v in data.items()
    }

    _ha_repairs = MagicMock()
    _ha_repairs.ConfirmRepairFlow = _ConfirmRepairFlow
    _ha_repairs.RepairsFlow = _RepairsFlow

    _vol = MagicMock()
    _vol.Schema = lambda x, **kw: x
    _vol.Required = lambda key, **kw: key
    _vol.Optional = lambda key, **kw: key
    _vol.All = lambda *a, **kw: a[0] if a else None
    _vol.coerce = lambda t: t
    _vol.In = lambda choices: choices

    _ha_http = MagicMock()
    _ha_frontend = MagicMock()
    _ha_lovelace = MagicMock()

    class _ResourceStorageCollection:
        """Stub so isinstance() checks in _init_resource work correctly."""

    _ha_lovelace_resources = MagicMock()
    _ha_lovelace_resources.ResourceStorageCollection = _ResourceStorageCollection

    sys.modules.update({
        "homeassistant": _ha,
        "homeassistant.config_entries": _ha_config_entries,
        "homeassistant.const": _ha_const,
        "homeassistant.core": _ha.core,
        "homeassistant.exceptions": _ha_exceptions,
        "homeassistant.helpers": _ha_helpers,
        "homeassistant.helpers.update_coordinator": _ha_update_coordinator,
        "homeassistant.helpers.aiohttp_client": _ha_helpers_aiohttp,
        "homeassistant.helpers.entity": _ha_helpers_entity,
        "homeassistant.helpers.entity_platform": _ha_entity_platform,
        "homeassistant.helpers.issue_registry": MagicMock(),
        "homeassistant.components": MagicMock(),
        "homeassistant.components.button": _ha_button,
        "homeassistant.components.camera": _ha_camera,
        "homeassistant.components.diagnostics": _ha_diagnostics,
        "homeassistant.components.event": _ha_event,
        "homeassistant.components.repairs": _ha_repairs,
        "homeassistant.components.http": _ha_http,
        "homeassistant.components.frontend": _ha_frontend,
        "homeassistant.components.lovelace": _ha_lovelace,
        "homeassistant.components.lovelace.resources": _ha_lovelace_resources,
        "voluptuous": _vol,
    })


import pytest


@pytest.fixture
def sample_apt_address() -> str:
    return "00000001"


@pytest.fixture
def sample_token() -> str:
    return "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
