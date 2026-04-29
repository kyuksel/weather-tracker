"""Shared pytest fixtures for the weather-tracker test suite."""

import os
from collections.abc import Generator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Set required env vars before importing app modules that call get_settings() at
# module level (app.db creates the engine on import). The mock_settings fixture
# overrides these at test time, but the import-time call needs valid values.
os.environ.setdefault("TRACKED_LATITUDE", "39.7456")
os.environ.setdefault("TRACKED_LONGITUDE", "-97.0892")
os.environ.setdefault("WEATHER_GOV_USER_AGENT", "weather-tracker/0.1")

from app.config import Settings  # noqa: E402
from app.db import Base  # noqa: E402


def _make_test_settings() -> Settings:
    """Build a Settings instance with test values, bypassing env-var validation."""
    return Settings.model_construct(
        tracked_latitude=39.7456,
        tracked_longitude=-97.0892,
        weather_gov_user_agent="weather-tracker/0.1",
        poll_interval_minutes=60,
        forecast_hours_window=72,
        database_url="sqlite:///:memory:",
        log_level="INFO",
        weather_gov_timeout_seconds=30,
        weather_gov_max_retries=3,
        api_port=8000,
    )


@pytest.fixture(autouse=True)
def mock_settings() -> Generator[Settings]:
    """Patch get_settings and migrations in every test so tests need no env vars or /data."""
    settings = _make_test_settings()
    with (
        patch("app.main.get_settings", return_value=settings),
        patch("app.main._run_migrations"),
    ):
        yield settings


@pytest.fixture
def db_session() -> Generator[Session]:
    """Yield an in-memory SQLite session with schema applied via metadata.

    Uses Base.metadata.create_all rather than Alembic migrations; the goal
    here is schema round-trip testing, not migration testing.
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
