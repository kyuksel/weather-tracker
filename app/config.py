"""Application configuration via pydantic-settings."""

import functools
from typing import Annotated

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration read from environment variables.

    Required vars (no defaults): TRACKED_LATITUDE, TRACKED_LONGITUDE,
    WEATHER_GOV_USER_AGENT.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Required — no defaults
    tracked_latitude: Annotated[float, Field(ge=-90, le=90)]
    tracked_longitude: Annotated[float, Field(ge=-180, le=180)]
    weather_gov_user_agent: str

    # Optional — with defaults
    poll_interval_minutes: int = 60
    forecast_hours_window: int = 72
    database_url: str = "sqlite:////data/weather.db"
    log_level: str = "INFO"
    weather_gov_timeout_seconds: int = 30
    weather_gov_max_retries: int = 3
    api_port: int = 8000


@functools.lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()  # type: ignore[call-arg]
