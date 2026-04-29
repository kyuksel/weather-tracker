"""Tests for the APScheduler integration."""

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.poller import PollResult
from app.scheduler import build_scheduler

_LAT = 39.7456
_LON = -97.0892


@pytest.fixture
def test_settings() -> Settings:
    return Settings.model_construct(
        tracked_latitude=_LAT,
        tracked_longitude=_LON,
        weather_gov_user_agent="test-agent/1.0",
        poll_interval_minutes=60,
        forecast_hours_window=3,
        database_url="sqlite:///:memory:",
        log_level="INFO",
        weather_gov_timeout_seconds=30,
        weather_gov_max_retries=3,
        api_port=8000,
    )


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def sf(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


@pytest.mark.asyncio
async def test_scheduler_runs_first_poll_immediately(test_settings, sf) -> None:
    """AsyncIOScheduler fires the first poll immediately at startup.

    The scheduler is built with next_run_time=datetime.utcnow() and a 60-minute
    interval. We verify the first tick executes without waiting a full interval
    by patching poll_once with a threading.Event signal rather than relying on
    cross-thread SQLite writes.
    """
    poll_started = threading.Event()

    def fake_poll_once(*args, **kwargs) -> PollResult:
        poll_started.set()
        return PollResult(success=True, observations_written=3, error_class=None, duration_ms=0)

    with patch("app.scheduler.poll_once", fake_poll_once):
        scheduler = build_scheduler(test_settings, sf, MagicMock())
        scheduler.start()
        try:
            # Wait non-blocking (in a thread) for the first tick, up to 5 s.
            # poll_once is called via asyncio.to_thread so the event is set
            # from a worker thread; threading.Event.wait is the right primitive.
            fired = await asyncio.to_thread(poll_started.wait, 5.0)
        finally:
            scheduler.shutdown(wait=False)

    assert fired, "poll_once was not called within 5 seconds of scheduler start"
