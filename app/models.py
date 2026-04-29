"""ORM models for the weather-tracker domain.

All DateTime columns store naive UTC datetimes. SQLite has no timezone-aware
column type; the application enforces the UTC-only contract at the boundary.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class TemperatureUnit(StrEnum):
    """Temperature unit stored alongside each observation."""

    CELSIUS = "C"
    FAHRENHEIT = "F"


class Location(Base):
    """A geographic location identified by latitude and longitude.

    The data model supports multiple locations; the application currently
    tracks one location configured via env vars.
    """

    __tablename__ = "location"
    __table_args__ = (UniqueConstraint("latitude", "longitude", name="uq_location_lat_lon"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    observations: Mapped[list["ForecastObservation"]] = relationship(
        "ForecastObservation", back_populates="location"
    )


class ForecastObservation(Base):
    """A single hourly forecast observation recorded at a specific poll time.

    Multiple rows per (location_id, forecast_for) are intentional: each poll
    tick writes one row per forecast hour, capturing how the forecast evolves
    over time. The query endpoint aggregates these with MIN/MAX.
    """

    __tablename__ = "forecast_observation"
    __table_args__ = (Index("ix_observation_location_forecast_for", "location_id", "forecast_for"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    location_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("location.id"), nullable=False, index=True
    )
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    forecast_for: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    temperature: Mapped[float] = mapped_column(Float, nullable=False)
    temperature_unit: Mapped[TemperatureUnit] = mapped_column(
        SAEnum(TemperatureUnit, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )

    location: Mapped["Location"] = relationship("Location", back_populates="observations")
