"""Tests for the weather poller."""

from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import ForecastObservation, Location
from app.poller import poll_once
from app.weather_client import WeatherGovClient, WeatherGovUnavailableError

_LAT = 39.7456
_LON = -97.0892
_FORECAST_HOURLY_URL = "https://api.weather.gov/gridpoints/TOP/31,80/forecast/hourly"
_BASE_UTC = datetime(2026, 4, 29, 0, 0, 0, tzinfo=UTC)

_POINTS_RESPONSE: dict[str, Any] = {
    "type": "Feature",
    "properties": {"forecastHourly": _FORECAST_HOURLY_URL},
}


def _period(offset_hours: int, temperature: float = 20.0) -> dict[str, Any]:
    start = (_BASE_UTC + timedelta(hours=offset_hours)).isoformat()
    return {
        "number": offset_hours + 1,
        "startTime": start,
        "endTime": (_BASE_UTC + timedelta(hours=offset_hours + 1)).isoformat(),
        "temperature": temperature,
        "temperatureUnit": "C",
        "shortForecast": "Sunny",
        "isDaytime": True,
    }


def _hourly_response(periods: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "Feature", "properties": {"periods": periods}}


def _make_client(responses: list[tuple[int, dict[str, Any] | None]]) -> WeatherGovClient:
    """Build a WeatherGovClient backed by a fixed sequence of mock responses."""
    queue: deque[tuple[int, dict[str, Any] | None]] = deque(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        status, body = queue.popleft()
        if body is None:
            return httpx.Response(status)
        return httpx.Response(status, json=body)

    mock_http = httpx.Client(transport=httpx.MockTransport(handler))
    return WeatherGovClient(user_agent="test-agent/1.0", client=mock_http)


@pytest.fixture
def db_engine():
    """In-memory SQLite engine with StaticPool so all sessions share one connection."""
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
    """Sessionmaker bound to the shared in-memory test engine."""
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


def test_poll_writes_one_row_per_forecast_hour(sf) -> None:
    """poll_once writes one ForecastObservation per forecast entry returned."""
    periods = [_period(i) for i in range(5)]
    client = _make_client([(200, _POINTS_RESPONSE), (200, _hourly_response(periods))])

    result = poll_once(sf, client, _LAT, _LON, hours=5)

    assert result.success is True
    assert result.observations_written == 5

    with sf() as session:
        obs = session.scalars(select(ForecastObservation)).all()
        assert len(obs) == 5
        # All rows share the same retrieved_at timestamp
        retrieved_ats = {o.retrieved_at for o in obs}
        assert len(retrieved_ats) == 1
        # All rows reference the same Location
        location_ids = {o.location_id for o in obs}
        assert len(location_ids) == 1


def test_poll_creates_location_on_first_call(sf) -> None:
    """poll_once creates a Location row when the database is empty."""
    client = _make_client([(200, _POINTS_RESPONSE), (200, _hourly_response([_period(0)]))])

    poll_once(sf, client, _LAT, _LON, hours=1)

    with sf() as session:
        locs = session.scalars(select(Location)).all()
        assert len(locs) == 1
        assert locs[0].latitude == _LAT
        assert locs[0].longitude == _LON


def test_poll_reuses_existing_location(sf) -> None:
    """poll_once reuses an existing Location row without creating duplicates."""
    with sf() as session:
        loc = Location(latitude=_LAT, longitude=_LON)
        session.add(loc)
        session.commit()
        existing_id = loc.id

    client = _make_client([(200, _POINTS_RESPONSE), (200, _hourly_response([_period(0)]))])
    poll_once(sf, client, _LAT, _LON, hours=1)

    with sf() as session:
        locs = session.scalars(select(Location)).all()
        assert len(locs) == 1
        assert locs[0].id == existing_id


def test_poll_returns_failed_result_on_weather_client_error(sf) -> None:
    """poll_once returns a failed PollResult on WeatherClientError; no rows written."""
    client = MagicMock(spec=WeatherGovClient)
    client.get_hourly_forecast.side_effect = WeatherGovUnavailableError("service down")

    result = poll_once(sf, client, _LAT, _LON, hours=5)

    assert result.success is False
    assert result.observations_written == 0
    assert result.error_class == "WeatherGovUnavailableError"

    with sf() as session:
        assert session.scalars(select(ForecastObservation)).first() is None


def test_poll_returns_failed_result_on_unexpected_error(sf) -> None:
    """poll_once returns a failed PollResult on unexpected errors; no exception raised."""
    client = MagicMock(spec=WeatherGovClient)
    client.get_hourly_forecast.side_effect = RuntimeError("unexpected boom")

    result = poll_once(sf, client, _LAT, _LON, hours=5)

    assert result.success is False
    assert result.observations_written == 0
    assert result.error_class == "RuntimeError"

    with sf() as session:
        assert session.scalars(select(ForecastObservation)).first() is None


def test_multiple_polls_accumulate_rows_for_same_target_hour(sf) -> None:
    """Multiple polls write multiple rows per forecast_for; all rows are retained.

    This exercises the critical design point: each tick writes one row per
    forecast hour even when rows for the same (location, forecast_for) already
    exist from earlier ticks.
    """
    target_dt = _BASE_UTC.replace(tzinfo=None)
    temperatures = [10.0, 12.0, 14.0]
    client = _make_client(
        [
            (200, _POINTS_RESPONSE),
            (200, _hourly_response([_period(0, temperature=temperatures[0])])),
            # gridpoint is cached after first call; only hourly responses needed
            (200, _hourly_response([_period(0, temperature=temperatures[1])])),
            (200, _hourly_response([_period(0, temperature=temperatures[2])])),
        ]
    )

    poll_once(sf, client, _LAT, _LON, hours=1)
    poll_once(sf, client, _LAT, _LON, hours=1)
    poll_once(sf, client, _LAT, _LON, hours=1)

    with sf() as session:
        all_obs = session.scalars(select(ForecastObservation)).all()
        assert len(all_obs) == 3

        target_rows = session.scalars(
            select(ForecastObservation).where(ForecastObservation.forecast_for == target_dt)
        ).all()
        assert len(target_rows) == 3
        assert sorted(r.temperature for r in target_rows) == sorted(temperatures)
