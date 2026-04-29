"""Shared pytest fixtures for the weather-tracker test suite."""

from collections.abc import Generator
from unittest.mock import patch

import pytest

from app.config import Settings


def _make_test_settings() -> Settings:
    """Build a Settings instance with test values, bypassing env-var validation."""
    return Settings.model_construct(
        tracked_latitude=39.7456,
        tracked_longitude=-97.0892,
        weather_gov_user_agent="weather-tracker/0.1",
        poll_interval_minutes=60,
        forecast_hours_window=72,
        database_url="sqlite:////data/weather.db",
        log_level="INFO",
        weather_gov_timeout_seconds=30,
        weather_gov_max_retries=3,
        api_port=8000,
    )


@pytest.fixture(autouse=True)
def mock_settings() -> Generator[Settings]:
    """Patch get_settings in every app module so tests need no env vars."""
    settings = _make_test_settings()
    with patch("app.main.get_settings", return_value=settings):
        yield settings
