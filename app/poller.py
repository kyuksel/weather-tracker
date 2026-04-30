"""Poller: fetches forecasts from weather.gov and writes observations to the database."""

import time
from datetime import datetime

import structlog
from pydantic import BaseModel
from sqlalchemy.orm import sessionmaker

from app.models import ForecastObservation
from app.repositories.locations import get_or_create_location
from app.weather_client import WeatherClientError, WeatherGovClient

log = structlog.get_logger()


class PollResult(BaseModel):
    """Result of a single polling attempt."""

    success: bool
    observations_written: int
    error_class: str | None
    duration_ms: int


def poll_once(
    session_factory: sessionmaker,
    weather_client: WeatherGovClient,
    latitude: float,
    longitude: float,
    hours: int,
) -> PollResult:
    """Fetch hourly forecasts and persist one row per entry to the database.

    Always returns a PollResult; never raises. The scheduler depends on this
    guarantee — an unhandled exception would trigger APScheduler's
    misbehaving-job protection and could disable the job.

    Args:
        session_factory: SQLAlchemy sessionmaker used to open a DB session.
        weather_client: Configured weather.gov HTTP client.
        latitude: Latitude of the location to poll.
        longitude: Longitude of the location to poll.
        hours: Number of hourly forecast entries to fetch and store.

    Returns:
        PollResult describing success/failure, row count, and elapsed time.
    """
    log.info("poll_start", lat=latitude, lon=longitude, hours=hours)
    t0 = time.monotonic()
    retrieved_at = datetime.utcnow()

    try:
        entries = weather_client.get_hourly_forecast(latitude, longitude, hours)
    except WeatherClientError as exc:
        duration_ms = round((time.monotonic() - t0) * 1000)
        log.error(
            "poll_failed",
            exc_class=type(exc).__name__,
            message=str(exc),
            lat=latitude,
            lon=longitude,
        )
        return PollResult(
            success=False,
            observations_written=0,
            error_class=type(exc).__name__,
            duration_ms=duration_ms,
        )
    except Exception as exc:
        duration_ms = round((time.monotonic() - t0) * 1000)
        log.error(
            "poll_unexpected_error",
            exc_class=type(exc).__name__,
            message=str(exc),
            lat=latitude,
            lon=longitude,
        )
        return PollResult(
            success=False,
            observations_written=0,
            error_class=type(exc).__name__,
            duration_ms=duration_ms,
        )

    with session_factory() as session:
        location = get_or_create_location(session, latitude, longitude)
        rows = [
            ForecastObservation(
                location_id=location.id,
                retrieved_at=retrieved_at,
                forecast_for=entry.start_time,
                temperature=entry.temperature,
                temperature_unit=entry.temperature_unit,
            )
            for entry in entries
        ]
        session.add_all(rows)
        session.commit()

    duration_ms = round((time.monotonic() - t0) * 1000)
    log.info(
        "poll_complete",
        lat=latitude,
        lon=longitude,
        observations_written=len(rows),
        duration_ms=duration_ms,
    )
    return PollResult(
        success=True,
        observations_written=len(rows),
        error_class=None,
        duration_ms=duration_ms,
    )
