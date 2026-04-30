"""Repository functions for ForecastObservation queries."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ForecastObservation, TemperatureUnit


@dataclass
class ExtremesResult:
    """Aggregated temperature extremes for a given location and target hour."""

    min_temperature: float | None
    max_temperature: float | None
    unit: TemperatureUnit | None
    observation_count: int


def get_forecast_extremes(
    session: Session,
    location_id: int,
    target_hour_utc: datetime,
) -> ExtremesResult:
    """Return min/max temperature extremes for a location and target hour.

    Issues a single aggregate query. When no observations match, returns an
    ExtremesResult with all-None fields and observation_count=0.

    Args:
        session: Active SQLAlchemy session.
        location_id: ID of the Location row.
        target_hour_utc: Naive UTC datetime identifying the forecast hour.

    Returns:
        ExtremesResult with aggregated min, max, unit, and row count.
    """
    row = session.execute(
        select(
            func.min(ForecastObservation.temperature),
            func.max(ForecastObservation.temperature),
            func.count(ForecastObservation.id),
            func.max(ForecastObservation.temperature_unit),
        ).where(
            ForecastObservation.location_id == location_id,
            ForecastObservation.forecast_for == target_hour_utc,
        )
    ).one()

    count: int = row[2]
    if count == 0:
        return ExtremesResult(
            min_temperature=None,
            max_temperature=None,
            unit=None,
            observation_count=0,
        )

    return ExtremesResult(
        min_temperature=row[0],
        max_temperature=row[1],
        unit=TemperatureUnit(row[3]),
        observation_count=count,
    )
