"""Unit tests for config_reader — device config parsing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.comelit_man.config_reader import _parse_config, get_device_config
from custom_components.comelit_man.exceptions import ProtocolError
from custom_components.comelit_man.models import DeviceConfig


# ---------------------------------------------------------------------------
# _parse_config — parsing the raw JSON response
# ---------------------------------------------------------------------------


def _vip_payload(
    apt_address: str = "SB000006",
    apt_subaddress: int = 1,
    entrance_book: list | None = None,
    opendoor_book: list | None = None,
    actuator_book: list | None = None,
    camera_book: list | None = None,
) -> dict:
    user_params: dict = {}
    if entrance_book is not None:
        user_params["entrance-address-book"] = entrance_book
    if opendoor_book is not None:
        user_params["opendoor-address-book"] = opendoor_book
    if actuator_book is not None:
        user_params["actuator-address-book"] = actuator_book
    if camera_book is not None:
        user_params["rtsp-camera-address-book"] = camera_book
    return {
        "response-code": 200,
        "vip": {
            "apt-address": apt_address,
            "apt-subaddress": apt_subaddress,
            "user-parameters": user_params,
        },
    }


class TestParseConfig:
    def test_parses_apt_address(self):
        data = _vip_payload(apt_address="SB000006")
        config = _parse_config(data)
        assert config.apt_address == "SB000006"

    def test_parses_apt_subaddress(self):
        data = _vip_payload(apt_subaddress=2)
        config = _parse_config(data)
        assert config.apt_subaddress == 2

    def test_parses_caller_address_from_entrance_book(self):
        data = _vip_payload(entrance_book=[{"apt-address": "SB100001"}])
        config = _parse_config(data)
        assert config.caller_address == "SB100001"

    def test_caller_address_empty_when_no_entrance_book(self):
        data = _vip_payload()
        config = _parse_config(data)
        assert config.caller_address == ""

    def test_parses_regular_doors(self):
        doors = [
            {"id": 1, "name": "Front", "apt-address": "SB100001", "output-index": 0},
            {"id": 2, "name": "Back", "apt-address": "SB100002", "output-index": 1},
        ]
        data = _vip_payload(opendoor_book=doors)
        config = _parse_config(data)
        assert len(config.doors) == 2
        assert config.doors[0].name == "Front"
        assert config.doors[0].is_actuator is False
        assert config.doors[1].name == "Back"
        assert config.doors[0].index == 0
        assert config.doors[1].index == 1

    def test_parses_actuator_doors(self):
        actuators = [
            {"id": 3, "name": "Gate", "apt-address": "SB100003", "output-index": 0, "module-index": 1}
        ]
        data = _vip_payload(actuator_book=actuators)
        config = _parse_config(data)
        assert len(config.doors) == 1
        assert config.doors[0].name == "Gate"
        assert config.doors[0].is_actuator is True
        assert config.doors[0].module_index == 1

    def test_regular_and_actuator_doors_sequential_index(self):
        doors = [{"id": 1, "name": "Front", "apt-address": "SB100001", "output-index": 0}]
        actuators = [{"id": 2, "name": "Gate", "apt-address": "SB100002", "output-index": 0}]
        data = _vip_payload(opendoor_book=doors, actuator_book=actuators)
        config = _parse_config(data)
        assert len(config.doors) == 2
        # Regular door gets index 0, actuator gets index 1
        assert config.doors[0].index == 0
        assert config.doors[1].index == 1

    def test_parses_cameras(self):
        cameras = [
            {"id": 1, "name": "Cam1", "rtsp-url": "rtsp://192.168.1.50/live", "rtsp-user": "admin", "rtsp-password": "pass"}
        ]
        data = _vip_payload(camera_book=cameras)
        config = _parse_config(data)
        assert len(config.cameras) == 1
        assert config.cameras[0].name == "Cam1"
        assert config.cameras[0].rtsp_url == "rtsp://192.168.1.50/live"
        assert config.cameras[0].rtsp_user == "admin"
        assert config.cameras[0].rtsp_password == "pass"

    def test_empty_config_when_no_vip(self):
        config = _parse_config({"response-code": 200})
        assert config.apt_address == ""
        assert config.doors == []
        assert config.cameras == []

    def test_raw_data_stored(self):
        data = _vip_payload()
        config = _parse_config(data)
        assert config.raw is data


# ---------------------------------------------------------------------------
# get_device_config — integration with client mock
# ---------------------------------------------------------------------------


class TestGetDeviceConfig:
    @pytest.mark.asyncio
    async def test_success_returns_device_config(self):
        channel = MagicMock()
        client = MagicMock()
        client.open_channel = AsyncMock(return_value=channel)
        client.send_json = AsyncMock(return_value=_vip_payload())

        config = await get_device_config(client)

        assert isinstance(config, DeviceConfig)
        assert config.apt_address == "SB000006"

    @pytest.mark.asyncio
    async def test_raises_protocol_error_on_non_200(self):
        channel = MagicMock()
        client = MagicMock()
        client.open_channel = AsyncMock(return_value=channel)
        client.send_json = AsyncMock(
            return_value={"response-code": 500, "error": "internal"}
        )

        with pytest.raises(ProtocolError, match="500"):
            await get_device_config(client)

    @pytest.mark.asyncio
    async def test_opens_ucfg_channel(self):
        from custom_components.comelit_man.channels import ChannelType

        channel = MagicMock()
        client = MagicMock()
        client.open_channel = AsyncMock(return_value=channel)
        client.send_json = AsyncMock(return_value=_vip_payload())

        await get_device_config(client)

        client.open_channel.assert_called_once_with("UCFG", ChannelType.UCFG)
