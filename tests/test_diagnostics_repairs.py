"""Unit tests for diagnostics and repairs platforms."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.comelit_man.diagnostics import async_get_config_entry_diagnostics
from custom_components.comelit_man.repairs import async_create_fix_flow


# ---------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------


def _make_entry(*, connected: bool = True, has_config: bool = True, session_active: bool = False):
    coordinator = MagicMock()
    coordinator._client = MagicMock()
    coordinator._client.connected = connected
    coordinator._connection_lost = False
    coordinator._vip_listener = MagicMock() if connected else None
    coordinator.video_session = MagicMock() if session_active else None
    if session_active:
        coordinator.video_session.active = True
    coordinator.rtsp_url = "rtsp://127.0.0.1:8557/live"
    coordinator.rtsp_server = MagicMock()

    if has_config:
        coordinator.device_config = MagicMock()
        coordinator.device_config.apt_address = "SB000006"
        coordinator.device_config.apt_subaddress = 1
        coordinator.device_config.caller_address = "SB100001"
        coordinator.device_config.doors = [MagicMock(), MagicMock()]
        coordinator.device_config.cameras = [MagicMock()]
    else:
        coordinator.device_config = None

    entry = MagicMock()
    entry.runtime_data = coordinator
    entry.data = {"host": "192.168.1.111", "port": 64100, "token": "secret_token_here"}
    return entry


class TestDiagnostics:
    @pytest.mark.asyncio
    async def test_returns_dict(self):
        entry = _make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_redacts_token(self):
        entry = _make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["config"].get("token") == "**REDACTED**"

    @pytest.mark.asyncio
    async def test_includes_device_config(self):
        entry = _make_entry(has_config=True)
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["device_config"]["apt_address"] == "SB000006"
        assert result["device_config"]["door_count"] == 2
        assert result["device_config"]["camera_count"] == 1

    @pytest.mark.asyncio
    async def test_device_config_none_when_not_loaded(self):
        entry = _make_entry(has_config=False)
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["device_config"] is None

    @pytest.mark.asyncio
    async def test_connection_state_connected(self):
        entry = _make_entry(connected=True)
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["connection"]["connected"] is True
        assert result["connection"]["vip_listener_active"] is True

    @pytest.mark.asyncio
    async def test_video_session_active(self):
        entry = _make_entry(session_active=True)
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["video"]["session_active"] is True

    @pytest.mark.asyncio
    async def test_video_session_inactive(self):
        entry = _make_entry(session_active=False)
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["video"]["session_active"] is False


# ---------------------------------------------------------------------------
# repairs
# ---------------------------------------------------------------------------


class TestRepairs:
    @pytest.mark.asyncio
    async def test_auth_failed_returns_confirm_flow(self):
        flow = await async_create_fix_flow(MagicMock(), "auth_failed", None)
        # Should return a ConfirmRepairFlow (real or stub)
        assert flow is not None

    @pytest.mark.asyncio
    async def test_unknown_issue_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown issue"):
            await async_create_fix_flow(MagicMock(), "nonexistent_issue", None)
