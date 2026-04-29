"""weather.gov HTTP client with tenacity retries and in-process gridpoint caching."""

import time
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, ConfigDict
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.models import TemperatureUnit

log = structlog.get_logger()


class WeatherClientError(Exception):
    """Base class for all errors raised by this module."""


class WeatherGovUnavailableError(WeatherClientError):
    """Raised after retries are exhausted on 5xx, network errors, or timeouts."""


class WeatherGovBadRequestError(WeatherClientError):
    """Raised immediately on 4xx responses; not retried.

    Args:
        message: Human-readable description.
        status_code: The HTTP status code returned by weather.gov.
    """

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class WeatherGovInvalidResponseError(WeatherClientError):
    """Raised when a response is valid JSON but missing required fields or unparseable."""


class ForecastEntry(BaseModel):
    """A single hourly forecast entry returned by weather.gov.

    start_time is timezone-naive and always represents UTC.
    """

    model_config = ConfigDict(frozen=True)

    start_time: datetime
    temperature: float
    temperature_unit: TemperatureUnit


def _is_retryable_exception(exc: BaseException) -> bool:
    """Return True for 5xx HTTP errors, network errors, and timeouts."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


class WeatherGovClient:
    """Synchronous client for the weather.gov API.

    Resolves gridpoint URLs with in-process caching, fetches hourly forecasts
    with configurable exponential-backoff retries, and normalises all timestamps
    to naive UTC datetimes.
    """

    def __init__(
        self,
        user_agent: str,
        timeout_seconds: float = 30,
        max_retries: int = 3,
        client: httpx.Client | None = None,
    ) -> None:
        """Initialise the client.

        Args:
            user_agent: Value for the User-Agent header; required by weather.gov.
            timeout_seconds: HTTP timeout per request in seconds.
            max_retries: Maximum retry attempts after the first failure.
            client: Optional pre-built httpx.Client for tests. If None, one is
                created and owned by this instance.

        Raises:
            ValueError: If user_agent is empty.
        """
        if not user_agent:
            raise ValueError("user_agent must not be empty")
        self._max_retries = max_retries
        self._owns_client = client is None
        self._client: httpx.Client = client or httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/geo+json"},
            timeout=timeout_seconds,
        )
        self._gridpoint_cache: dict[tuple[float, float], str] = {}

    # ------------------------------------------------------------------ #
    # Public interface                                                      #
    # ------------------------------------------------------------------ #

    def get_hourly_forecast(self, lat: float, lon: float, hours: int) -> list[ForecastEntry]:
        """Return up to *hours* forecast entries for the given location.

        Entries are sorted by start_time ascending. On a 404 from the hourly
        endpoint (expired forecast URL), the gridpoint cache is invalidated and
        the full flow is retried once.

        Args:
            lat: Latitude of the location.
            lon: Longitude of the location.
            hours: Maximum number of entries to return.

        Returns:
            List of ForecastEntry objects, sorted by start_time.

        Raises:
            WeatherGovUnavailableError: API unreachable after retries.
            WeatherGovBadRequestError: Non-404 4xx response.
            WeatherGovInvalidResponseError: Structurally invalid response.
        """
        bound_log = log.bind(lat=lat, lon=lon)
        t0 = time.monotonic()

        forecast_url = self._get_gridpoint_url(lat, lon)
        try:
            entries = self._fetch_hourly_forecast(forecast_url)
        except WeatherGovBadRequestError as exc:
            if exc.status_code == 404:
                # Forecast URL has expired; clear cache and try once more.
                self._invalidate_gridpoint_cache(lat, lon)
                forecast_url = self._get_gridpoint_url(lat, lon)
                entries = self._fetch_hourly_forecast(forecast_url)
            else:
                raise

        result = sorted(entries, key=lambda e: e.start_time)[:hours]
        duration_ms = round((time.monotonic() - t0) * 1000)
        bound_log.info(
            "weather_gov_fetch_complete",
            hours_returned=len(result),
            duration_ms=duration_ms,
        )
        return result

    def close(self) -> None:
        """Close the underlying HTTP client if owned by this instance."""
        if self._owns_client:
            self._client.close()
            self._owns_client = False

    def __enter__(self) -> "WeatherGovClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Private helpers                                                       #
    # ------------------------------------------------------------------ #

    def _get_gridpoint_url(self, lat: float, lon: float) -> str:
        """Return the forecastHourly URL for (lat, lon), using cache if available.

        Args:
            lat: Latitude.
            lon: Longitude.

        Returns:
            The forecastHourly URL string.

        Raises:
            WeatherGovInvalidResponseError: If properties.forecastHourly is absent.
        """
        key = (lat, lon)
        if key in self._gridpoint_cache:
            return self._gridpoint_cache[key]

        url = f"https://api.weather.gov/points/{lat},{lon}"
        response = self._make_request(url)

        try:
            forecast_url: str = response.json()["properties"]["forecastHourly"]
        except (KeyError, TypeError, ValueError) as exc:
            raise WeatherGovInvalidResponseError(
                "Missing properties.forecastHourly in points response"
            ) from exc

        if not forecast_url:
            raise WeatherGovInvalidResponseError("properties.forecastHourly is empty")

        self._gridpoint_cache[key] = forecast_url
        return forecast_url

    def _fetch_hourly_forecast(self, forecast_url: str) -> list[ForecastEntry]:
        """Fetch and parse hourly periods from the given forecast URL.

        Appends ?units=si to request Celsius. Each startTime is converted from
        its original offset to a naive UTC datetime.

        Args:
            forecast_url: The forecastHourly URL obtained from the gridpoint response.

        Returns:
            Unsorted, unsliced list of ForecastEntry objects.

        Raises:
            WeatherGovInvalidResponseError: If the response is structurally invalid.
        """
        response = self._make_request(f"{forecast_url}?units=si")

        try:
            periods: list[dict[str, Any]] = response.json()["properties"]["periods"]
        except (KeyError, TypeError, ValueError) as exc:
            raise WeatherGovInvalidResponseError(
                "Missing properties.periods in forecast response"
            ) from exc

        entries: list[ForecastEntry] = []
        for period in periods:
            try:
                raw_start: str = period["startTime"]
                temperature = float(period["temperature"])
                unit_str: str = period["temperatureUnit"]
            except (KeyError, TypeError, ValueError) as exc:
                raise WeatherGovInvalidResponseError(f"Malformed period entry: {exc}") from exc

            try:
                unit = TemperatureUnit(unit_str)
            except ValueError as exc:
                raise WeatherGovInvalidResponseError(
                    f"Unknown temperatureUnit {unit_str!r}"
                ) from exc

            try:
                dt = datetime.fromisoformat(raw_start)
                start_time = dt.astimezone(UTC).replace(tzinfo=None)
            except (ValueError, TypeError) as exc:
                raise WeatherGovInvalidResponseError(
                    f"Unparseable startTime {raw_start!r}"
                ) from exc

            entries.append(
                ForecastEntry(
                    start_time=start_time,
                    temperature=temperature,
                    temperature_unit=unit,
                )
            )

        return entries

    def _invalidate_gridpoint_cache(self, lat: float, lon: float) -> None:
        """Remove the cached forecastHourly URL for (lat, lon), if present."""
        self._gridpoint_cache.pop((lat, lon), None)

    def _make_request(self, url: str) -> httpx.Response:
        """Execute an HTTP GET with exponential-backoff retries.

        Retries on 5xx, network errors, and timeouts. Raises immediately on 4xx.
        Converts terminal failure to WeatherGovUnavailableError.

        Args:
            url: URL to GET.

        Returns:
            The successful httpx.Response.

        Raises:
            WeatherGovBadRequestError: On 4xx (status_code attribute set).
            WeatherGovUnavailableError: After retries exhausted on 5xx/network/timeout.
        """

        def before_sleep(retry_state: RetryCallState) -> None:
            exc = retry_state.outcome.exception() if retry_state.outcome else None
            log.warning(
                "weather_gov_retry",
                attempt=retry_state.attempt_number,
                exc_class=type(exc).__name__ if exc else "unknown",
            )

        def do_get() -> httpx.Response:
            resp = self._client.get(url)
            resp.raise_for_status()
            return resp

        try:
            return Retrying(  # type: ignore[return-value]
                retry=retry_if_exception(_is_retryable_exception),
                wait=wait_exponential(multiplier=1, min=1, max=8),
                stop=stop_after_attempt(self._max_retries + 1),
                before_sleep=before_sleep,
                reraise=True,
            )(do_get)
        except httpx.HTTPStatusError as exc:
            if 400 <= exc.response.status_code < 500:
                raise WeatherGovBadRequestError(
                    f"HTTP {exc.response.status_code} from {url}",
                    status_code=exc.response.status_code,
                ) from exc
            log.error("weather_gov_failure", exc_class=type(exc).__name__, message=str(exc))
            raise WeatherGovUnavailableError(str(exc)) from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            log.error("weather_gov_failure", exc_class=type(exc).__name__, message=str(exc))
            raise WeatherGovUnavailableError(str(exc)) from exc
