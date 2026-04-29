"""Tests for the weather.gov HTTP client."""

from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.models import TemperatureUnit
from app.weather_client import (
    WeatherGovBadRequestError,
    WeatherGovClient,
    WeatherGovInvalidResponseError,
    WeatherGovUnavailableError,
)

# ---------------------------------------------------------------------------
# Realistic fixture data (based on the public weather.gov GeoJSON schema)
# ---------------------------------------------------------------------------

_FORECAST_HOURLY_URL = "https://api.weather.gov/gridpoints/TOP/31,80/forecast/hourly"

_POINTS_RESPONSE: dict[str, Any] = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [-97.0892, 39.7456]},
    "properties": {
        "gridId": "TOP",
        "gridX": 31,
        "gridY": 80,
        "forecastHourly": _FORECAST_HOURLY_URL,
        "timeZone": "America/Chicago",
    },
}

_BASE_UTC = datetime(2026, 4, 29, 0, 0, 0, tzinfo=UTC)


def _period(offset_hours: int, temperature: float = 20.0, unit: str = "C") -> dict[str, Any]:
    start = (_BASE_UTC + timedelta(hours=offset_hours)).isoformat()
    return {
        "number": offset_hours + 1,
        "startTime": start,
        "endTime": (_BASE_UTC + timedelta(hours=offset_hours + 1)).isoformat(),
        "temperature": temperature,
        "temperatureUnit": unit,
        "shortForecast": "Sunny",
        "isDaytime": True,
    }


def _hourly_response(periods: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {
            "updated": "2026-04-29T12:00:00+00:00",
            "units": "us",
            "forecastGenerator": "HourlyForecastGenerator",
            "periods": periods,
        },
    }


_DEFAULT_PERIODS = [_period(i, temperature=15.0 + i * 0.1) for i in range(72)]
_DEFAULT_HOURLY = _hourly_response(_DEFAULT_PERIODS)


# ---------------------------------------------------------------------------
# Client factory helpers
# ---------------------------------------------------------------------------


def _make_client(
    responses: list[tuple[int, dict[str, Any] | None]],
    max_retries: int = 3,
) -> WeatherGovClient:
    """Build a WeatherGovClient backed by a fixed sequence of mock responses."""
    queue: deque[tuple[int, dict[str, Any] | None]] = deque(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        status, body = queue.popleft()
        if body is None:
            return httpx.Response(status)
        return httpx.Response(status, json=body)

    mock_http = httpx.Client(transport=httpx.MockTransport(handler))
    return WeatherGovClient(user_agent="test-agent/1.0", max_retries=max_retries, client=mock_http)


def _tracking_client(
    responses: list[tuple[int, dict[str, Any] | None]],
    max_retries: int = 3,
) -> tuple[WeatherGovClient, list[str]]:
    """Like _make_client but also returns a list that records every requested URL."""
    queue: deque[tuple[int, dict[str, Any] | None]] = deque(responses)
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        status, body = queue.popleft()
        if body is None:
            return httpx.Response(status)
        return httpx.Response(status, json=body)

    mock_http = httpx.Client(transport=httpx.MockTransport(handler))
    client = WeatherGovClient(
        user_agent="test-agent/1.0", max_retries=max_retries, client=mock_http
    )
    return client, urls


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch time.sleep to eliminate real waits during retry tests."""
    monkeypatch.setattr("time.sleep", lambda _: None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_happy_path() -> None:
    """Client returns entries in ascending start_time order with correct fields."""
    client = _make_client([(200, _POINTS_RESPONSE), (200, _DEFAULT_HOURLY)])
    entries = client.get_hourly_forecast(39.7456, -97.0892, hours=72)

    assert len(entries) == 72
    assert entries[0].start_time < entries[-1].start_time
    # start_time must be naive UTC
    assert entries[0].start_time.tzinfo is None
    assert entries[0].start_time == datetime(2026, 4, 29, 0, 0, 0)
    assert entries[0].temperature == 15.0
    assert entries[0].temperature_unit == TemperatureUnit.CELSIUS


def test_gridpoint_cached_after_first_call() -> None:
    """Two calls for the same (lat, lon) only hit the points endpoint once."""
    client, urls = _tracking_client(
        [
            (200, _POINTS_RESPONSE),  # first call — points
            (200, _DEFAULT_HOURLY),  # first call — hourly
            (200, _DEFAULT_HOURLY),  # second call — hourly (no second points)
        ]
    )

    client.get_hourly_forecast(39.7456, -97.0892, hours=3)
    client.get_hourly_forecast(39.7456, -97.0892, hours=3)

    points_calls = [u for u in urls if "/points/" in u]
    assert len(points_calls) == 1


def test_gridpoint_cache_invalidated_on_404() -> None:
    """404 from the hourly endpoint clears the cache; the second call re-fetches gridpoint."""
    client, urls = _tracking_client(
        [
            (200, _POINTS_RESPONSE),  # call 1 — points
            (200, _DEFAULT_HOURLY),  # call 1 — hourly OK
            (404, None),  # call 2 — hourly 404 (expired URL)
            (200, _POINTS_RESPONSE),  # call 2 — re-fetch points
            (200, _DEFAULT_HOURLY),  # call 2 — hourly OK (retry)
        ]
    )

    entries1 = client.get_hourly_forecast(39.7456, -97.0892, hours=3)
    assert len(entries1) == 3

    entries2 = client.get_hourly_forecast(39.7456, -97.0892, hours=3)
    assert len(entries2) == 3

    points_calls = [u for u in urls if "/points/" in u]
    assert len(points_calls) == 2
    hourly_calls = [u for u in urls if "forecast/hourly" in u]
    assert len(hourly_calls) == 3


def test_retries_on_5xx(no_sleep: None) -> None:
    """Client retries on 5xx and succeeds when a later attempt returns 200."""
    client = _make_client(
        [
            (200, _POINTS_RESPONSE),
            (503, None),  # attempt 1
            (503, None),  # attempt 2
            (200, _DEFAULT_HOURLY),  # attempt 3 — success
        ],
        max_retries=3,
    )
    entries = client.get_hourly_forecast(39.7456, -97.0892, hours=3)
    assert len(entries) == 3


def test_gives_up_after_max_retries(no_sleep: None) -> None:
    """WeatherGovUnavailableError is raised after max_retries + 1 total attempts."""
    max_retries = 2
    client = _make_client(
        [(200, _POINTS_RESPONSE)] + [(503, None)] * (max_retries + 1),
        max_retries=max_retries,
    )
    with pytest.raises(WeatherGovUnavailableError):
        client.get_hourly_forecast(39.7456, -97.0892, hours=3)


def test_no_retry_on_400() -> None:
    """A 400 response raises WeatherGovBadRequestError immediately with no retry."""
    client, urls = _tracking_client([(200, _POINTS_RESPONSE), (400, None)])
    with pytest.raises(WeatherGovBadRequestError):
        client.get_hourly_forecast(39.7456, -97.0892, hours=3)

    hourly_calls = [u for u in urls if "forecast/hourly" in u]
    assert len(hourly_calls) == 1


def test_invalid_response_missing_periods() -> None:
    """Hourly response without properties.periods raises WeatherGovInvalidResponseError."""
    bad_hourly: dict[str, Any] = {"properties": {"updated": "2026-04-29T12:00:00+00:00"}}
    client = _make_client([(200, _POINTS_RESPONSE), (200, bad_hourly)])
    with pytest.raises(WeatherGovInvalidResponseError):
        client.get_hourly_forecast(39.7456, -97.0892, hours=3)


def test_invalid_response_unknown_unit() -> None:
    """A period with an unrecognised temperatureUnit raises WeatherGovInvalidResponseError."""
    periods = [_period(0, unit="K")]
    client = _make_client([(200, _POINTS_RESPONSE), (200, _hourly_response(periods))])
    with pytest.raises(WeatherGovInvalidResponseError, match="'K'"):
        client.get_hourly_forecast(39.7456, -97.0892, hours=3)


def test_slice_to_requested_hours() -> None:
    """When the API returns more entries than requested, only the first hours are returned."""
    periods_156 = [_period(i, temperature=float(i)) for i in range(156)]
    client = _make_client([(200, _POINTS_RESPONSE), (200, _hourly_response(periods_156))])
    entries = client.get_hourly_forecast(39.7456, -97.0892, hours=72)

    assert len(entries) == 72
    # Verify the returned entries are the earliest 72
    assert entries[0].start_time == datetime(2026, 4, 29, 0, 0, 0)
    assert entries[-1].start_time == datetime(2026, 4, 29, 0, 0, 0) + timedelta(hours=71)


def test_timezone_conversion() -> None:
    """startTime with non-UTC offsets is correctly converted to naive UTC."""
    # 2026-04-29T10:00:00-04:00  ==  2026-04-29T14:00:00 UTC
    periods = [
        {
            "number": 1,
            "startTime": "2026-04-29T10:00:00-04:00",
            "endTime": "2026-04-29T11:00:00-04:00",
            "temperature": 22.5,
            "temperatureUnit": "C",
            "shortForecast": "Partly Cloudy",
            "isDaytime": True,
        }
    ]
    client = _make_client([(200, _POINTS_RESPONSE), (200, _hourly_response(periods))])
    entries = client.get_hourly_forecast(39.7456, -97.0892, hours=5)

    assert len(entries) == 1
    assert entries[0].start_time == datetime(2026, 4, 29, 14, 0, 0)
    assert entries[0].start_time.tzinfo is None
    assert entries[0].temperature == 22.5


def test_user_agent_required() -> None:
    """Instantiating WeatherGovClient with an empty user_agent raises ValueError."""
    with pytest.raises(ValueError, match="user_agent"):
        WeatherGovClient(user_agent="")
