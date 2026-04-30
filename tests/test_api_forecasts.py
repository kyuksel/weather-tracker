"""Tests for the GET /forecasts/extremes endpoint."""

from collections.abc import Generator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.main import app
from app.models import ForecastObservation, Location, TemperatureUnit

_LAT = 39.7456
_LON = -97.0892
_DATE = "2026-04-29"
_HOUR = 14
_BASE_URL = f"/forecasts/extremes?lat={_LAT}&lon={_LON}&date={_DATE}&hour={_HOUR}"
_TARGET_DT = datetime(2026, 4, 29, 14, 0, 0)


@pytest.fixture
def db_engine():
    """In-memory SQLite engine with StaticPool so all sessions share one connection.

    StaticPool is required here because in-memory SQLite databases are per-connection;
    without it, the endpoint (running in a thread pool) would get a fresh, empty
    connection when the dependency override checks out from the pool.
    """
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
    """Sessionmaker bound to the shared test engine."""
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


@pytest.fixture
def db_session(sf) -> Generator[Session]:
    """Session for test data setup."""
    session = sf()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(sf) -> Generator[TestClient]:
    """TestClient with get_session overridden to create sessions from the test engine."""

    def _override() -> Generator[Session]:
        session = sf()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _make_location(session: Session, lat: float = _LAT, lon: float = _LON) -> Location:
    loc = Location(latitude=lat, longitude=lon)
    session.add(loc)
    session.commit()
    session.refresh(loc)
    return loc


def _make_obs(
    session: Session,
    location: Location,
    forecast_for: datetime,
    temperature: float,
    unit: TemperatureUnit = TemperatureUnit.CELSIUS,
) -> ForecastObservation:
    obs = ForecastObservation(
        location_id=location.id,
        retrieved_at=datetime(2026, 4, 29, 13, 0, 0),
        forecast_for=forecast_for,
        temperature=temperature,
        temperature_unit=unit,
    )
    session.add(obs)
    session.commit()
    return obs


def test_returns_404_for_unknown_location(client: TestClient) -> None:
    """Empty database returns 404; detail mentions location not tracked."""
    response = client.get(_BASE_URL)

    assert response.status_code == 404
    assert "not tracked" in response.json()["detail"].lower()


def test_returns_zero_count_for_known_location_no_matching_observations(
    client: TestClient, db_session: Session
) -> None:
    """Known location with no matching observations returns 200 and count=0 with null fields."""
    _make_location(db_session)

    response = client.get(_BASE_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["observation_count"] == 0
    assert body["min_temperature"] is None
    assert body["max_temperature"] is None
    assert body["unit"] is None


def test_returns_correct_min_max_for_single_observation(
    client: TestClient, db_session: Session
) -> None:
    """Single observation: min == max == that temperature, count == 1, unit matches."""
    loc = _make_location(db_session)
    _make_obs(db_session, loc, _TARGET_DT, temperature=15.0)

    response = client.get(_BASE_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["min_temperature"] == 15.0
    assert body["max_temperature"] == 15.0
    assert body["observation_count"] == 1
    assert body["unit"] == "C"


def test_returns_correct_min_max_across_multiple_observations(
    client: TestClient, db_session: Session
) -> None:
    """Three observations for same location/hour: correct min, max, and count."""
    loc = _make_location(db_session)
    for temp in [10.0, 15.5, 12.3]:
        _make_obs(db_session, loc, _TARGET_DT, temperature=temp)

    response = client.get(_BASE_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["min_temperature"] == 10.0
    assert body["max_temperature"] == 15.5
    assert body["observation_count"] == 3


def test_filters_by_target_hour_correctly(client: TestClient, db_session: Session) -> None:
    """Only observations at exactly the queried forecast_for hour are included."""
    loc = _make_location(db_session)
    _make_obs(db_session, loc, datetime(2026, 4, 29, 13, 0, 0), temperature=5.0)
    _make_obs(db_session, loc, datetime(2026, 4, 29, 14, 0, 0), temperature=20.0)
    _make_obs(db_session, loc, datetime(2026, 4, 29, 15, 0, 0), temperature=8.0)

    response = client.get(_BASE_URL)  # hour=14

    assert response.status_code == 200
    body = response.json()
    assert body["observation_count"] == 1
    assert body["min_temperature"] == 20.0
    assert body["max_temperature"] == 20.0


def test_filters_by_location_correctly(client: TestClient, db_session: Session) -> None:
    """Only observations for the queried location contribute to min/max."""
    loc1 = _make_location(db_session, lat=_LAT, lon=_LON)
    loc2 = _make_location(db_session, lat=40.0, lon=-98.0)

    _make_obs(db_session, loc1, _TARGET_DT, temperature=12.0)
    _make_obs(db_session, loc2, _TARGET_DT, temperature=99.0)

    response = client.get(_BASE_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["observation_count"] == 1
    assert body["min_temperature"] == 12.0
    assert body["max_temperature"] == 12.0


def test_rejects_invalid_lat(client: TestClient) -> None:
    """lat=91 is out of range; returns 422."""
    response = client.get(f"/forecasts/extremes?lat=91&lon={_LON}&date={_DATE}&hour={_HOUR}")
    assert response.status_code == 422


def test_rejects_invalid_lon(client: TestClient) -> None:
    """lon=-181 is out of range; returns 422."""
    response = client.get(f"/forecasts/extremes?lat={_LAT}&lon=-181&date={_DATE}&hour={_HOUR}")
    assert response.status_code == 422


def test_rejects_invalid_hour(client: TestClient) -> None:
    """hour=24 is out of range; returns 422."""
    response = client.get(f"/forecasts/extremes?lat={_LAT}&lon={_LON}&date={_DATE}&hour=24")
    assert response.status_code == 422


def test_rejects_invalid_hour_negative(client: TestClient) -> None:
    """hour=-1 is out of range; returns 422."""
    response = client.get(f"/forecasts/extremes?lat={_LAT}&lon={_LON}&date={_DATE}&hour=-1")
    assert response.status_code == 422


def test_rejects_malformed_date(client: TestClient) -> None:
    """Unparseable date string returns 422."""
    response = client.get(f"/forecasts/extremes?lat={_LAT}&lon={_LON}&date=not-a-date&hour={_HOUR}")
    assert response.status_code == 422


def test_response_target_hour_is_iso_utc(client: TestClient, db_session: Session) -> None:
    """target_hour_utc serializes as ISO 8601 with Z suffix."""
    _make_location(db_session)

    response = client.get(_BASE_URL)

    assert response.status_code == 200
    target = response.json()["target_hour_utc"]
    # field_serializer formats with Z suffix: "2026-04-29T14:00:00Z"
    assert target.endswith("Z"), f"Expected Z suffix, got: {target!r}"
    assert target == "2026-04-29T14:00:00Z"
