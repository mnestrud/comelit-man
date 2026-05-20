"""Unit tests for camera_utils — RTSP URL helpers."""

from __future__ import annotations

import pytest

from custom_components.comelit_man.camera_utils import get_cameras, get_rtsp_url
from custom_components.comelit_man.models import Camera, DeviceConfig


def _make_camera(
    *,
    rtsp_url: str = "rtsp://192.168.1.50:554/live",
    rtsp_user: str = "",
    rtsp_password: str = "",
) -> Camera:
    return Camera(id=1, name="Test Cam", rtsp_url=rtsp_url, rtsp_user=rtsp_user, rtsp_password=rtsp_password)


def _make_config(cameras: list[Camera] | None = None) -> DeviceConfig:
    return DeviceConfig(
        apt_address="SB000006",
        cameras=cameras or [],
    )


# ---------------------------------------------------------------------------
# get_cameras
# ---------------------------------------------------------------------------


class TestGetCameras:
    def test_returns_empty_list_when_no_cameras(self):
        config = _make_config()
        assert get_cameras(config) == []

    def test_returns_all_cameras(self):
        cams = [
            Camera(id=1, name="Front", rtsp_url="rtsp://a"),
            Camera(id=2, name="Back", rtsp_url="rtsp://b"),
        ]
        config = _make_config(cameras=cams)
        assert get_cameras(config) == cams

    def test_returns_same_list_object(self):
        cams = [Camera(id=1, name="Cam", rtsp_url="rtsp://x")]
        config = _make_config(cameras=cams)
        assert get_cameras(config) is config.cameras


# ---------------------------------------------------------------------------
# get_rtsp_url — no device_host
# ---------------------------------------------------------------------------


class TestGetRtspUrlNoHostReplacement:
    def test_returns_url_unchanged_without_device_host(self):
        cam = _make_camera(rtsp_url="rtsp://192.168.1.50:554/stream")
        assert get_rtsp_url(cam) == "rtsp://192.168.1.50:554/stream"

    def test_returns_empty_string_for_empty_url(self):
        cam = _make_camera(rtsp_url="")
        assert get_rtsp_url(cam) == ""

    def test_returns_empty_string_when_none_url(self):
        cam = Camera(id=1, name="Cam", rtsp_url="")
        assert get_rtsp_url(cam, device_host=None) == ""


# ---------------------------------------------------------------------------
# get_rtsp_url — with device_host replacing the address
# ---------------------------------------------------------------------------


class TestGetRtspUrlWithHostReplacement:
    def test_replaces_hostname_with_device_host(self):
        cam = _make_camera(rtsp_url="rtsp://old-host:554/live")
        result = get_rtsp_url(cam, device_host="192.168.1.111")
        assert result == "rtsp://192.168.1.111:554/live"

    def test_replaces_hostname_preserving_path(self):
        cam = _make_camera(rtsp_url="rtsp://old-host/cam/live")
        result = get_rtsp_url(cam, device_host="10.0.0.5")
        assert "10.0.0.5" in result
        assert "/cam/live" in result

    def test_adds_credentials_when_user_provided(self):
        cam = _make_camera(rtsp_url="rtsp://old-host:554/stream", rtsp_user="admin")
        result = get_rtsp_url(cam, device_host="192.168.1.10")
        assert "admin@192.168.1.10" in result

    def test_adds_user_and_password_when_both_provided(self):
        cam = _make_camera(
            rtsp_url="rtsp://old-host:554/stream",
            rtsp_user="admin",
            rtsp_password="secret",
        )
        result = get_rtsp_url(cam, device_host="192.168.1.10")
        assert "admin:secret@192.168.1.10" in result

    def test_no_credentials_added_when_user_absent(self):
        cam = _make_camera(rtsp_url="rtsp://old-host:554/stream")
        result = get_rtsp_url(cam, device_host="192.168.1.10")
        assert "@" not in result
        assert "192.168.1.10" in result

    def test_url_without_port_replaces_host_only(self):
        cam = _make_camera(rtsp_url="rtsp://old-host/live")
        result = get_rtsp_url(cam, device_host="new-host")
        assert "new-host" in result
        # No spurious port should be added
        assert result.startswith("rtsp://new-host/")
