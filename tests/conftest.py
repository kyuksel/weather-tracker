"""Shared pytest fixtures for the weather-tracker test suite."""

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def configure_test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject required env vars and reset the settings cache around every test."""
    monkeypatch.setenv("TRACKED_LATITUDE", "39.7456")
    monkeypatch.setenv("TRACKED_LONGITUDE", "-97.0892")
    monkeypatch.setenv("WEATHER_GOV_USER_AGENT", "weather-tracker/0.1")
    get_settings.cache_clear()
    yield  # type: ignore[misc]
    get_settings.cache_clear()
