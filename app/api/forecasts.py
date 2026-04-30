"""Forecasts API router."""

from datetime import UTC, date, datetime, time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.repositories.locations import find_location
from app.repositories.observations import get_forecast_extremes
from app.schemas import ForecastExtremesResponse, LocationOut

router = APIRouter(prefix="/forecasts", tags=["forecasts"])


@router.get("/extremes", response_model=ForecastExtremesResponse)
def get_extremes(
    lat: Annotated[float, Query(ge=-90, le=90, description="Latitude")],
    lon: Annotated[float, Query(ge=-180, le=180, description="Longitude")],
    date: Annotated[date, Query(description="UTC date in YYYY-MM-DD format")],
    hour: Annotated[int, Query(ge=0, le=23, description="UTC hour of day (0-23)")],
    session: Annotated[Session, Depends(get_session)],
) -> ForecastExtremesResponse:
    """Return the min/max forecast temperatures for a location, date, and UTC hour.

    Aggregates all ForecastObservation rows recorded for the target
    (location, forecast_for) pair across every polling tick. Returns 404 if
    the location has never been polled; returns 200 with observation_count=0
    if the location is known but no observations match the requested hour.
    """
    target_hour_utc = datetime.combine(date, time(hour=hour))

    location = find_location(session, lat, lon)
    if location is None:
        raise HTTPException(
            status_code=404,
            detail="Location not tracked. Only the configured location is polled.",
        )

    extremes = get_forecast_extremes(session, location.id, target_hour_utc)

    return ForecastExtremesResponse(
        location=LocationOut(latitude=location.latitude, longitude=location.longitude),
        target_hour_utc=target_hour_utc.replace(tzinfo=UTC),
        min_temperature=extremes.min_temperature,
        max_temperature=extremes.max_temperature,
        unit=extremes.unit,
        observation_count=extremes.observation_count,
    )
