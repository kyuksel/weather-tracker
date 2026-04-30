"""Pydantic response schemas for the forecasts API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_serializer

from app.models import TemperatureUnit


class LocationOut(BaseModel):
    """Geographic location in an API response."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"latitude": 39.7456, "longitude": -97.0892}}
    )

    latitude: float
    longitude: float


class ForecastExtremesResponse(BaseModel):
    """Response body for GET /forecasts/extremes.

    target_hour_utc serializes as ISO 8601 with 'Z' suffix (e.g. "2026-04-29T14:00:00Z").
    When observation_count is 0, min_temperature, max_temperature, and unit are null.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "location": {"latitude": 39.7456, "longitude": -97.0892},
                "target_hour_utc": "2026-04-29T14:00:00Z",
                "min_temperature": 12.4,
                "max_temperature": 18.7,
                "unit": "C",
                "observation_count": 3,
            }
        }
    )

    location: LocationOut
    target_hour_utc: datetime
    min_temperature: float | None
    max_temperature: float | None
    unit: TemperatureUnit | None
    observation_count: int

    @field_serializer("target_hour_utc")
    def serialize_utc(self, dt: datetime) -> str:
        """Serialize datetime to ISO 8601 with Z suffix."""
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
