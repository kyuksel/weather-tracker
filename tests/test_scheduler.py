"""Tests for the APScheduler integration."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
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


@pytest.mark.asyncio
async def test_scheduler_runs_first_poll_immediately(test_settings) -> None:
    """AsyncIOScheduler fires the first poll immediately at startup.

    The scheduler is built with next_run_time=datetime.utcnow() and a 60-minute
    interval. We verify the first tick executes without waiting a full interval.

    fake_poll_once runs in a worker thread (via asyncio.to_thread inside
    _poll_job). The correct primitive for signalling back to the event loop from
    a thread is loop.call_soon_threadsafe; asyncio.wait_for then completes as
    soon as the event is set without occupying any executor threads.
    """
    poll_fired = asyncio.Event()
    loop = asyncio.get_running_loop()

    def fake_poll_once(*args, **kwargs) -> PollResult:
        loop.call_soon_threadsafe(poll_fired.set)
        return PollResult(success=True, observations_written=3, error_class=None, duration_ms=0)

    with patch("app.scheduler.poll_once", fake_poll_once):
        scheduler = build_scheduler(test_settings, MagicMock(), MagicMock())
        scheduler.start()
        try:
            await asyncio.wait_for(poll_fired.wait(), timeout=5.0)
        finally:
            scheduler.shutdown(wait=False)

    assert poll_fired.is_set()
