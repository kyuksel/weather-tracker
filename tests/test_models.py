"""Tests for ORM models and schema correctness."""

from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ForecastObservation, Location, TemperatureUnit

_FORECAST_FOR = datetime(2026, 4, 30, 9, 0, 0)  # naive UTC


def _utc(hour: int) -> datetime:
    return datetime(2026, 4, 29, hour, 0, 0)


def test_multiple_observations_per_forecast_hour_and_unique_location_constraint(
    db_session: Session,
) -> None:
    """Multiple ForecastObservation rows for the same (location_id, forecast_for) are allowed.

    Also verifies that the unique constraint on (latitude, longitude) rejects duplicates.
    """
    location = Location(latitude=39.7456, longitude=-97.0892)
    db_session.add(location)
    db_session.flush()

    observations = [
        ForecastObservation(
            location_id=location.id,
            retrieved_at=_utc(14),
            forecast_for=_FORECAST_FOR,
            temperature=12.5,
            temperature_unit=TemperatureUnit.CELSIUS,
        ),
        ForecastObservation(
            location_id=location.id,
            retrieved_at=_utc(15),
            forecast_for=_FORECAST_FOR,
            temperature=13.1,
            temperature_unit=TemperatureUnit.CELSIUS,
        ),
        ForecastObservation(
            location_id=location.id,
            retrieved_at=_utc(16),
            forecast_for=_FORECAST_FOR,
            temperature=11.8,
            temperature_unit=TemperatureUnit.CELSIUS,
        ),
    ]
    db_session.add_all(observations)
    db_session.commit()

    from sqlalchemy import select

    rows = db_session.scalars(
        select(ForecastObservation)
        .where(ForecastObservation.location_id == location.id)
        .order_by(ForecastObservation.retrieved_at)
    ).all()

    assert len(rows) == 3
    assert rows[0].temperature == pytest.approx(12.5)
    assert rows[1].temperature == pytest.approx(13.1)
    assert rows[2].temperature == pytest.approx(11.8)
    assert all(r.temperature_unit == TemperatureUnit.CELSIUS for r in rows)

    # Unique constraint on (latitude, longitude) must reject a duplicate location.
    db_session.add(Location(latitude=39.7456, longitude=-97.0892))
    with pytest.raises(IntegrityError):
        db_session.flush()
