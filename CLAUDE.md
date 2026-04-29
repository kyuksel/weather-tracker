# CLAUDE.md

Operating spec for AI agents working on this repository. Read this in full
before making any changes. The human reviewer expects every PR to honor these
constraints. When in doubt, ask in the PR description rather than guessing.

## Project purpose

A containerized Python application that polls [weather.gov](http://weather.gov) for hourly
temperature forecasts at a configured location, stores every observation in a
local database, and exposes a query endpoint that returns the highest and
lowest forecasted temperatures recorded for a given location-date-hour.

This is a take-home coding exercise. Scope is fixed by the requirements in the
original prompt. Do not add features beyond what is specified. Items
considered for future work belong in the README's "Future work" section, not
in code.

## Stack (non-negotiable)

- Python 3.13 (Docker base image: `python:3.13-slim`)
- Package manager: uv. Use `uv add` for runtime deps, `uv add --dev` for dev
  deps. Commit `uv.lock`. Dockerfile uses `uv sync --frozen`.
- Web framework: FastAPI
- ORM: SQLAlchemy 2.x (sync, not async)
- Migrations: Alembic
- Database: SQLite (file mounted via Docker volume)
- HTTP client: httpx with tenacity for retries
- Scheduler: APScheduler 3.x, AsyncIOScheduler, in-process, started in FastAPI
  lifespan
- Configuration: pydantic-settings reading env vars
- Logging: structlog with JSON formatter
- Lint and format: ruff (no black, no isort, no flake8)
- Tests: pytest, pytest-asyncio, httpx.MockTransport for the weather client

Do not add other libraries without explicit approval in the PR description
with justification.

## Design constraints

### Data model

Two tables, defined in `app/models.py`:

```
location
  id          INTEGER PRIMARY KEY
  latitude    REAL    NOT NULL
  longitude   REAL    NOT NULL
  UNIQUE (latitude, longitude)

forecast_observation
  id                INTEGER PRIMARY KEY
  location_id       INTEGER NOT NULL REFERENCES location(id)
  retrieved_at      TIMESTAMP NOT NULL  -- when we polled, UTC
  forecast_for      TIMESTAMP NOT NULL  -- the hour the forecast targets, UTC
  temperature       REAL    NOT NULL
  temperature_unit  ENUM('C', 'F') NOT NULL
  INDEX (location_id, forecast_for)
```

`temperature_unit` is stored as a Python enum
(`TemperatureUnit(str, Enum)` with values `CELSIUS = "C"` and
`FAHRENHEIT = "F"`), mapped via SQLAlchemy's `Enum` column type. This gives
type safety in code and prevents invalid values at the schema level.

Critical design point: each polling tick writes one row per forecast hour,
even when forecasts for the same `forecast_for` already exist from earlier
polls. This is intentional. The system tracks how forecasts vary over time;
multiple rows per `(location_id, forecast_for)` are the data, not a bug. Do
not add unique constraints or upserts that would collapse them.

Concrete example: at 14:00 UTC the poller may insert a row predicting 12.5°C
for tomorrow 09:00 UTC. At 15:00 UTC, weather.gov has updated its forecast to
13.1°C; the poller inserts a second row for the same `forecast_for`
timestamp. After 24 hours of hourly polling, there will be roughly 24 rows
for that single target hour, capturing how the prediction evolved. The query
endpoint aggregates across these rows with `MIN(temperature)` and
`MAX(temperature)`.

### API contract

`GET /healthz` returns `{"status": "ok"}` with HTTP 200.

`GET /forecasts/extremes` accepts query parameters:
- `lat`: float in [-90, 90]
- `lon`: float in [-180, 180]
- `date`: ISO date string (YYYY-MM-DD), interpreted as UTC
- `hour`: integer in [0, 23], UTC hour of day

Behavior:
- 404 if no `Location` row matches `(lat, lon)` (the location was never
  polled).
- 200 with response body otherwise, including a `count` field that is 0 when
  no observations match the target hour.

Response body:
```json
{
  "location": {"latitude": 39.7456, "longitude": -97.0892},
  "target_hour_utc": "2026-04-29T14:00:00Z",
  "min_temperature": 12.4,
  "max_temperature": 18.7,
  "unit": "C",
  "observation_count": 3
}
```

When `observation_count` is 0, `min_temperature`, `max_temperature`, and
`unit` are null.

### Configuration (env vars)

All configuration is read from environment variables via pydantic-settings.

Required:
- `TRACKED_LATITUDE`, `TRACKED_LONGITUDE`: location to poll
- `WEATHER_GOV_USER_AGENT`: User-Agent header for weather.gov requests. Do
  not put a real email address here. Default example value is
  `weather-tracker/0.1`.

With sensible defaults:
- `POLL_INTERVAL_MINUTES` (default 60)
- `FORECAST_HOURS_WINDOW` (default 72)
- `DATABASE_URL` (default `sqlite:////data/weather.db`)
- `LOG_LEVEL` (default `INFO`)
- `WEATHER_GOV_TIMEOUT_SECONDS` (default 30)
- `WEATHER_GOV_MAX_RETRIES` (default 3)
- `API_PORT` (default 8000)

`.env.example` must list all of these with example values.

### Time and units

All timestamps stored and returned in UTC. weather.gov returns ISO 8601
timestamps with offsets; convert to UTC at the boundary in the weather
client. Request temperatures in Celsius from weather.gov for unit
consistency. Store the unit enum value with each observation for
transparency.

### weather.gov client

Two-step API: hit `https://api.weather.gov/points/{lat},{lon}` to get the
gridpoint metadata containing the `forecastHourly` URL, then hit that URL.

Cache gridpoint responses in process memory keyed by `(lat, lon)`;
gridpoints are stable. If the hourly forecast endpoint returns 404 (forecast
URL expired), invalidate the cached gridpoint for that location so the next
tick re-fetches it.

The User-Agent header is mandatory. Without it, weather.gov returns 403.

Slice the hourly forecast response to the first `FORECAST_HOURS_WINDOW`
entries; do not assume the API returns exactly that many.

Error handling, applied via tenacity decorators on the HTTP calls:
- 5xx responses, network errors, and timeouts: retry up to
  `WEATHER_GOV_MAX_RETRIES` times with exponential backoff.
- 4xx responses: do not retry. Raise a typed `WeatherClientError`.
- Malformed responses (missing fields, unparseable times): raise
  `WeatherClientError`.

The poller is responsible for catching `WeatherClientError`, logging it
with structured fields, and allowing the next scheduled tick to proceed. A
failed tick writes no rows; this is acceptable.

### Scheduler

In-process AsyncIOScheduler started in the FastAPI lifespan handler. Run an
initial poll immediately on startup; do not wait one full interval for
first data. Wrap each poll tick in try/except so a single failure cannot
crash the scheduler. Log every tick start and end with structured fields.

## Coding standards

- Type hints everywhere.
- Docstrings on public functions and classes. One-line summary, then args
  and returns when non-obvious.
- No print statements. Use the structlog logger.
- No global mutable state outside of carefully scoped module-level
  singletons (engine, scheduler).
- Functions over classes when there is no state to manage.
- Prefer SQLAlchemy 2.x style (`select(...)`, `session.scalars(...)`) over
  the legacy `Query` API.
- Pydantic models for all request and response shapes. No raw dicts at the
  API boundary.
- All commits must pass `ruff check` and `ruff format --check`.

## Testing standards

- Every PR that adds non-trivial code adds tests for it. PR 1 is the only
  exception (scaffolding only, but ships with one healthz test).
- Tests live in `tests/`, mirror the `app/` structure.
- Use `httpx.MockTransport` for weather client tests; never hit the real
  weather.gov API in tests.
- Use an in-memory SQLite engine for repository and API tests.
- Use FastAPI's `TestClient` for endpoint tests.
- Aim for tests that demonstrate the system works correctly under
  realistic conditions, not coverage theater. A small number of
  well-chosen tests beats many trivial ones.

## PR workflow

The implementation is broken into 6 PRs, listed in `README.md` under "PR
sequence". Each session works on exactly one PR. Do not bundle multiple PRs
into one branch.

Each PR must:
1. Be opened against `main` from a branch named `prN-short-description`
   (e.g., `pr1-scaffolding`, `pr3-weather-client`).
2. Have a descriptive title and a body that summarizes the change,
   references the PR number from the README sequence, and lists any
   deviations from the plan.
3. Update the "PR sequence" section of `README.md` in the same diff to add
   the PR's URL inline next to the PR's bullet, in the format:
   `- **PR N: Title** ([#N](URL)) — short description`. Use the GitHub PR
   URL produced when this PR is opened.
4. Leave `main` in a working state when merged: `docker compose up`
   succeeds and `/healthz` returns 200.
5. Pass `ruff check`, `ruff format --check`, and `pytest`.

## What not to do

- Do not add features beyond the exercise requirements. List ideas in the
  README's "Future work" section.
- Do not add a frontend, an admin UI, or a CLI beyond what is needed to
  run the app.
- Do not add Postgres, Redis, Celery, or any other infrastructure
  component.
- Do not add authentication, rate limiting, or CORS configuration. These
  are noted as future work.
- Do not change the data model from the spec above without raising it in
  the PR description first.
- Do not bundle unrelated changes into a single PR.
- Do not put any real personal information (email addresses, names beyond
  what is already in repo metadata) into source files, commit messages,
  or PR descriptions.

## Assumptions to surface in the README

These belong in the README's "Assumptions" section. Do not silently make
different ones; if a constraint forces a different assumption, raise it in
the PR description.

1. The application tracks one location at startup, configured via env vars.
   The data model supports multiple locations; runtime registration is
   future work.
2. Temperatures are requested in Celsius and stored with the unit enum.
   Min/max comparisons assume consistent units.
3. All timestamps are stored and queried in UTC. Input dates are
   interpreted as UTC.
4. "The next 72 hours" means the first N hourly entries returned by
   weather.gov, sorted by start time ascending, where N is configurable
   (`FORECAST_HOURS_WINDOW`, default 72).
5. The scheduler runs in-process. On restart, the next tick runs
   immediately.
6. Each polling tick writes one row per forecast hour, including
   duplicates of prior `(location, forecast_for)` pairs. This is the
   data, not a bug.
7. The query endpoint returns 404 for unknown locations and 200 with
   `count: 0` for known locations with no matching observations.
8. SQLite is used as the local data store. The persistence layer
   abstracts it; migration to Postgres is a connection-string change
   plus running migrations.
9. weather.gov requires a User-Agent header; this is configured via env
   var.
10. No authentication on the API. Production deployment would front it
    with an authenticated gateway.
