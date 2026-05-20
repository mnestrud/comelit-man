"""Unit tests for models and exceptions — dataclass fields and exception hierarchy."""

from __future__ import annotations

import pytest

from custom_components.comelit_man.models import Camera, DeviceConfig, Door, PushEvent
from custom_components.comelit_man.exceptions import (
    AuthenticationError,
    ComelitError,
    ConnectionComelitError,
    DoorOpenError,
    ProtocolError,
    TokenExtractionError,
    VideoCallError,
)


# ---------------------------------------------------------------------------
# Models — dataclass field defaults and construction
# ---------------------------------------------------------------------------


class TestDoor:
    def test_required_fields(self):
        d = Door(id=1, index=0, name="Front", apt_address="SB100001", output_index=0)
        assert d.id == 1
        assert d.index == 0
        assert d.name == "Front"
        assert d.apt_address == "SB100001"
        assert d.output_index == 0

    def test_default_is_actuator_false(self):
        d = Door(id=1, index=0, name="X", apt_address="A", output_index=0)
        assert d.is_actuator is False

    def test_default_secure_mode_false(self):
        d = Door(id=1, index=0, name="X", apt_address="A", output_index=0)
        assert d.secure_mode is False

    def test_default_module_index_zero(self):
        d = Door(id=1, index=0, name="X", apt_address="A", output_index=0)
        assert d.module_index == 0

    def test_actuator_flag_set(self):
        d = Door(id=1, index=0, name="Gate", apt_address="A", output_index=0, is_actuator=True)
        assert d.is_actuator is True


class TestCamera:
    def test_required_fields(self):
        c = Camera(id=1, name="Cam", rtsp_url="rtsp://x")
        assert c.id == 1
        assert c.name == "Cam"
        assert c.rtsp_url == "rtsp://x"

    def test_default_credentials_empty(self):
        c = Camera(id=1, name="Cam", rtsp_url="rtsp://x")
        assert c.rtsp_user == ""
        assert c.rtsp_password == ""

    def test_credentials_set(self):
        c = Camera(id=1, name="Cam", rtsp_url="rtsp://x", rtsp_user="admin", rtsp_password="pass")
        assert c.rtsp_user == "admin"
        assert c.rtsp_password == "pass"


class TestDeviceConfig:
    def test_defaults(self):
        cfg = DeviceConfig()
        assert cfg.apt_address == ""
        assert cfg.apt_subaddress == 0
        assert cfg.caller_address == ""
        assert cfg.doors == []
        assert cfg.cameras == []
        assert cfg.raw is None

    def test_doors_list_independent_between_instances(self):
        cfg1 = DeviceConfig()
        cfg2 = DeviceConfig()
        cfg1.doors.append(Door(id=1, index=0, name="X", apt_address="A", output_index=0))
        assert cfg2.doors == []

    def test_cameras_list_independent_between_instances(self):
        cfg1 = DeviceConfig()
        cfg2 = DeviceConfig()
        cfg1.cameras.append(Camera(id=1, name="Cam", rtsp_url="rtsp://x"))
        assert cfg2.cameras == []


class TestPushEvent:
    def test_required_field(self):
        ev = PushEvent(event_type="doorbell_ring")
        assert ev.event_type == "doorbell_ring"

    def test_defaults(self):
        ev = PushEvent(event_type="ring")
        assert ev.apt_address == ""
        assert ev.timestamp == 0.0
        assert ev.raw is None


# ---------------------------------------------------------------------------
# Exceptions — hierarchy and instantiation
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    def test_comelit_error_is_exception(self):
        assert issubclass(ComelitError, Exception)

    def test_all_errors_subclass_comelit_error(self):
        for cls in (
            ConnectionComelitError,
            AuthenticationError,
            ProtocolError,
            TokenExtractionError,
            DoorOpenError,
            VideoCallError,
        ):
            assert issubclass(cls, ComelitError), f"{cls} should subclass ComelitError"

    def test_comelit_error_subclasses_homeassistant_error(self):
        # ComelitError inherits from HomeAssistantError (real or stub).
        from custom_components.comelit_man.exceptions import ComelitError
        from tests.conftest import _HomeAssistantError
        assert issubclass(ComelitError, _HomeAssistantError)

    def test_door_open_error_with_translation_kwargs(self):
        err = DoorOpenError(
            translation_domain="comelit_man",
            translation_key="door_open_failed",
            translation_placeholders={"door": "Front"},
        )
        assert err.translation_domain == "comelit_man"
        assert err.translation_key == "door_open_failed"
        assert err.translation_placeholders == {"door": "Front"}

    def test_video_call_error_with_translation_key(self):
        err = VideoCallError(
            translation_domain="comelit_man",
            translation_key="video_call_failed",
        )
        assert err.translation_key == "video_call_failed"

    def test_authentication_error_with_message(self):
        err = AuthenticationError("bad token: 403 Forbidden")
        assert "403" in str(err)

    def test_raise_and_catch_comelit_error(self):
        with pytest.raises(ComelitError):
            raise DoorOpenError("test")

    def test_raise_and_catch_by_subclass(self):
        with pytest.raises(DoorOpenError):
            raise DoorOpenError("test")
