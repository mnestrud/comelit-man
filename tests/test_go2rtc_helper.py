"""Unit tests for go2rtc_helper — managed vs legacy go2rtc detection."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.comelit_man.go2rtc_helper import (
    DATA_GO2RTC,
    LEGACY_API_URL,
    get_go2rtc,
)


class TestGetGo2rtc:
    def test_prefers_managed_go2rtc_when_available(self):
        hass = MagicMock()
        managed_session = MagicMock()
        managed_config = MagicMock()
        managed_config.session = managed_session
        managed_config.url = "http://127.0.0.1:18555/"
        hass.data = {DATA_GO2RTC: managed_config}

        fallback_session = MagicMock()
        session, base_url = get_go2rtc(hass, fallback_session)

        assert session is managed_session
        assert base_url == "http://127.0.0.1:18555"

    def test_falls_back_to_legacy_when_no_go2rtc_in_hass_data(self):
        hass = MagicMock()
        hass.data = {}

        fallback_session = MagicMock()
        session, base_url = get_go2rtc(hass, fallback_session)

        assert session is fallback_session
        assert base_url == LEGACY_API_URL

    def test_falls_back_when_managed_session_is_none(self):
        hass = MagicMock()
        managed_config = MagicMock()
        managed_config.session = None
        managed_config.url = "http://127.0.0.1:18555"
        hass.data = {DATA_GO2RTC: managed_config}

        fallback_session = MagicMock()
        session, base_url = get_go2rtc(hass, fallback_session)

        assert session is fallback_session
        assert base_url == LEGACY_API_URL
