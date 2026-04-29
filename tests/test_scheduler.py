"""Tests for the APScheduler integration."""

import asyncio
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.models import ForecastObservation
from app.scheduler import build_scheduler
from app.weather_client import WeatherGovClient

_LAT = 39.7456
_LON = -97.0892
_FORECAST_HOURLY_URL = "https://api.weather.gov/gridpoints/TOP/31,80/forecast/hourly"
_BASE_UTC = datetime(2026, 4, 29, 0, 0, 0, tzinfo=UTC)


def _period(offset_hours: int) -> dict[str, Any]:
    start = (_BASE_UTC + timedelta(hours=offset_hours)).isoformat()
    return {
        "number": offset_hours + 1,
        "startTime": start,
        "endTime": (_BASE_UTC + timedelta(hours=offset_hours + 1)).isoformat(),
        "temperature": 20.0,
        "temperatureUnit": "C",
        "shortForecast": "Sunny",
        "isDaytime": True,
    }


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
    interval. We inspect DB state after a short wait to confirm the first tick
    executed without waiting a full interval.
    """
    points_response: dict[str, Any] = {
        "type": "Feature",
        "properties": {"forecastHourly": _FORECAST_HOURLY_URL},
    }
    hourly_response: dict[str, Any] = {
        "type": "Feature",
        "properties": {"periods": [_period(i) for i in range(3)]},
    }
    responses: deque[tuple[int, dict[str, Any]]] = deque(
        [(200, points_response), (200, hourly_response)]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        status, body = responses.popleft()
        return httpx.Response(status, json=body)

    mock_http = httpx.Client(transport=httpx.MockTransport(handler))
    client = WeatherGovClient(user_agent="test-agent/1.0", client=mock_http)

    scheduler = build_scheduler(test_settings, sf, client)
    scheduler.start()
    try:
        # Poll runs via asyncio.to_thread; 2s is ample for a mock HTTP call.
        await asyncio.sleep(2.0)
    finally:
        scheduler.shutdown(wait=False)

    with sf() as session:
        obs = session.scalars(select(ForecastObservation)).all()
    assert len(obs) >= 3
