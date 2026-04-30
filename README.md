# weather-tracker

A containerized Python application that polls [weather.gov](https://weather.gov) for hourly
temperature forecasts at a configured location, stores every observation in a
local SQLite database, and exposes a query endpoint that returns the highest
and lowest forecasted temperatures recorded for a given location, date, and
hour. Designed as a take-home coding exercise; scope is intentionally narrow.

## How to evaluate this submission

1. Copy `.env.example` to `.env` and edit `TRACKED_LATITUDE`,
   `TRACKED_LONGITUDE`, and `WEATHER_GOV_USER_AGENT`.
2. Run `docker compose up --build`.
3. Wait ~10 seconds for the first poll. Verify with:
   ```bash
   docker compose exec weather-tracker python -c "import sqlite3; print(sqlite3.connect('/data/weather.db').execute('SELECT COUNT(*) FROM forecast_observation').fetchone())"
   ```
   Expect a count > 0.
4. Query the API:
   ```bash
   curl "http://localhost:8000/forecasts/extremes?lat=<your_lat>&lon=<your_lon>&date=<UTC_date>&hour=<UTC_hour>"
   ```
5. Browse the auto-generated API docs at <http://localhost:8000/docs>.

> **Note on log visibility:** structured log lines for poll events may not
> surface in container stdout (see [Known issues](#known-issues)). Use the
> database query in step 3 above to confirm polling is working — it is the
> authoritative verification path.

---

## Table of contents

1. [Design overview](#design-overview)
2. [Data model](#data-model)
3. [API contract](#api-contract)
4. [Build and run](#build-and-run)
5. [Configuration](#configuration)
6. [Library choices and rationale](#library-choices-and-rationale)
7. [Assumptions](#assumptions)
8. [Known issues](#known-issues)
9. [PR sequence](#pr-sequence)
10. [Future work](#future-work)

---

## Design overview

The application has three cooperating subsystems running inside a single
container:

- **FastAPI web server** (uvicorn) handles inbound HTTP requests and manages
  the application lifespan — running Alembic migrations on startup and starting
  the scheduler.
- **In-process APScheduler** (`AsyncIOScheduler`) fires the poll job immediately
  on startup and then on a configurable interval (default 60 minutes).
- **Poller + WeatherGovClient** fetches hourly forecast data from weather.gov's
  two-step JSON API and persists each forecast entry as a `ForecastObservation`
  row via the repository layer.

Poll data flow: APScheduler → Poller → WeatherGovClient → weather.gov →
Repositories → SQLAlchemy ORM → SQLite.

Query data flow: API client → FastAPI → Repositories → SQLAlchemy ORM → SQLite.

See [`docs/architecture.md`](docs/architecture.md) for a Mermaid system diagram
and a description of the data model relationships.

---

## Data model

Two tables are defined in `app/models.py`.

**`location`** — a geographic point identified by latitude and longitude.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | auto-increment |
| `latitude` | REAL | not null |
| `longitude` | REAL | not null |

A `UNIQUE (latitude, longitude)` constraint (`uq_location_lat_lon`) prevents
duplicate registrations of the same point.

**`forecast_observation`** — a single hourly forecast entry captured during
one polling tick.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | auto-increment |
| `location_id` | INTEGER FK | → `location.id`, not null, indexed |
| `retrieved_at` | TIMESTAMP | when the poll ran, naive UTC |
| `forecast_for` | TIMESTAMP | the hour this forecast targets, naive UTC |
| `temperature` | REAL | not null |
| `temperature_unit` | ENUM (`C`/`F`) | stored as `TemperatureUnit` enum |

A composite index `ix_observation_location_forecast_for` covers
`(location_id, forecast_for)`, which is the access pattern used by the query
endpoint.

**Critical design point — multiple observations per target hour are intentional.**
Each polling tick writes one new row per forecast hour, even when a row for the
same `(location_id, forecast_for)` already exists from an earlier tick. There
is no unique constraint or upsert on that pair. The system tracks how forecasts
evolve over time; multiple rows are the data, not a bug.

Concrete example: at 14:00 UTC the poller inserts a row predicting 12.5 °C for
tomorrow 09:00 UTC. At 15:00 UTC weather.gov has revised its forecast to
13.1 °C; the poller inserts a second row for the same `forecast_for`
timestamp. After 24 hours of hourly polling there will be roughly 24 rows for
that single target hour. The `GET /forecasts/extremes` endpoint aggregates
across them with `MIN(temperature)` and `MAX(temperature)`.

---

## API contract

### `GET /healthz`

Returns `{"status": "ok"}` with HTTP 200. Used by Docker health checks and
load balancers.

### `GET /forecasts/extremes`

Returns the highest and lowest forecast temperatures recorded for a specific
location, date, and UTC hour. Aggregates across all polling ticks that have
ever stored a forecast for that `(location, forecast_for)` pair, capturing
how predictions evolved over time.

#### Query parameters

| Parameter | Type | Required | Valid range | Description |
|---|---|---|---|---|
| `lat` | float | Yes | −90 to 90 | Latitude of the location |
| `lon` | float | Yes | −180 to 180 | Longitude of the location |
| `date` | string | Yes | `YYYY-MM-DD` | UTC date |
| `hour` | integer | Yes | 0–23 | UTC hour of day |

#### Status codes

| Code | Condition |
|---|---|
| 200 | Location is tracked (even if `observation_count` is 0) |
| 404 | Location has never been polled |
| 422 | A query parameter fails validation |

When `observation_count` is 0, `min_temperature`, `max_temperature`, and
`unit` are all `null`.

#### Example response (200)

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

#### Example response (200, no observations yet)

```json
{
  "location": {"latitude": 39.7456, "longitude": -97.0892},
  "target_hour_utc": "2026-04-29T14:00:00Z",
  "min_temperature": null,
  "max_temperature": null,
  "unit": null,
  "observation_count": 0
}
```

#### Sample `curl` command

```bash
# Query extremes for the configured location, today at 14:00 UTC
curl "http://localhost:8000/forecasts/extremes?lat=39.7456&lon=-97.0892&date=$(date -u +%Y-%m-%d)&hour=14"

# Query an unconfigured location — returns 404
curl "http://localhost:8000/forecasts/extremes?lat=40.0&lon=-98.0&date=2026-04-29&hour=14"
```

---

## Build and run

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with the Compose plugin (or `docker-compose` v2)
- No local Python install required — everything runs inside the container

### Steps

```bash
# 1. Copy the example env file and fill in your location
cp .env.example .env
# Edit TRACKED_LATITUDE and TRACKED_LONGITUDE in .env

# 2. Build and start the service
docker compose up --build

# 3. Verify it is running
curl http://localhost:8000/healthz
# Expected: {"status":"ok"}
```

**Migrations run automatically on startup.** When the container starts, the
lifespan handler calls `alembic upgrade head` before the server accepts
traffic. No manual `alembic upgrade` step is required on first boot or after
pulling a new image with schema changes. `docker-compose.yml` overrides
`DATABASE_URL` to the volume-backed path `sqlite:////data/weather.db`
regardless of what `.env` contains.

**The poller starts immediately.** On container startup the app polls
weather.gov for the configured location within seconds; you do not need to
wait one full interval for the first data.

> **Log visibility caveat:** structured log lines for poll events (e.g.
> `poll_complete`) may not surface in container stdout due to a known issue
> (see [Known issues](#known-issues)). Prefer the database query below to
> confirm polling is working rather than relying on log output.

To verify the first poll completed, query the database directly:

```bash
docker compose exec weather-tracker python -c "import sqlite3; print(sqlite3.connect('/data/weather.db').execute('SELECT COUNT(*) FROM forecast_observation').fetchone())"
```

Expect a count equal to `FORECAST_HOURS_WINDOW` (default 72) within ~30
seconds of `docker compose up`. You can also attempt the log-based check:

```bash
docker compose logs -f weather-tracker | grep poll_complete
```

`docker compose down` stops the container. The `weather-data` named volume
persists the SQLite database across restarts. Running `docker compose up`
again (without `--build`) reuses the existing image and volume.

### Querying the endpoint after polling

After startup the poller runs immediately, so observations are available within
~30 seconds. Wait for at least two poll intervals so the endpoint returns
`observation_count >= 2` for near-future hours. Use the database query to
confirm polls have run (see the caveat about log visibility in
[Known issues](#known-issues)):

```bash
# Confirm the row count has grown (run twice, ~60 minutes apart)
docker compose exec weather-tracker python -c "import sqlite3; print(sqlite3.connect('/data/weather.db').execute('SELECT COUNT(*) FROM forecast_observation').fetchone())"

# Then query — substitute your configured lat/lon and a future UTC hour
curl "http://localhost:8000/forecasts/extremes?lat=39.7456&lon=-97.0892&date=$(date -u +%Y-%m-%d)&hour=20"
```

Expected response shape (temperatures in Celsius, count grows with each poll):

```json
{
  "location": {"latitude": 39.7456, "longitude": -97.0892},
  "target_hour_utc": "2026-04-30T20:00:00Z",
  "min_temperature": 14.2,
  "max_temperature": 14.5,
  "unit": "C",
  "observation_count": 2
}
```

To confirm that unknown locations return 404:

```bash
curl -i "http://localhost:8000/forecasts/extremes?lat=40.0&lon=-98.0&date=$(date -u +%Y-%m-%d)&hour=20"
# HTTP/1.1 404 Not Found
```

### Running Alembic locally (outside Docker)

The default database path (`/data/weather.db`) only exists inside the
container. To run migrations locally — for example to inspect the schema or
generate new revisions — set `DATABASE_URL` to a writable local path first:

```bash
# .env.example already sets DATABASE_URL=sqlite:///./weather.db; if you
# copied it to .env that value is picked up automatically.
uv run alembic upgrade head   # creates ./weather.db
uv run alembic downgrade base # drops all tables
```

---

## Configuration

All settings are read from environment variables (or a `.env` file).
See `.env.example` for a template.

| Variable | Default | Required | Description |
|---|---|---|---|
| `TRACKED_LATITUDE` | — | Yes | Latitude of the location to poll. Range: −90 to 90. |
| `TRACKED_LONGITUDE` | — | Yes | Longitude of the location to poll. Range: −180 to 180. |
| `WEATHER_GOV_USER_AGENT` | — | Yes | `User-Agent` header sent to weather.gov. Required by the API. |
| `POLL_INTERVAL_MINUTES` | `60` | No | How often to poll weather.gov, in minutes. |
| `FORECAST_HOURS_WINDOW` | `72` | No | Number of hourly forecast entries stored per poll. |
| `DATABASE_URL` | `sqlite:////data/weather.db` | No | SQLAlchemy database URL. |
| `LOG_LEVEL` | `INFO` | No | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `WEATHER_GOV_TIMEOUT_SECONDS` | `30` | No | HTTP timeout for weather.gov requests, in seconds. |
| `WEATHER_GOV_MAX_RETRIES` | `3` | No | Max retries on 5xx or network errors. |
| `API_PORT` | `8000` | No | Port the uvicorn server listens on inside the container. |

---

## Library choices and rationale

**FastAPI** — Chosen over Flask or plain Starlette for its first-class Pydantic
integration, automatic OpenAPI docs, and async support. The lifespan handler
makes scheduler wiring clean.

**uvicorn** — ASGI server that runs the FastAPI application. The `[standard]`
extra bundles `uvloop` and `httptools` for better event-loop throughput at
minimal configuration cost.

**Pydantic** — Data validation and serialization library used for all request
and response models. FastAPI's dependency on Pydantic v2 provides declarative
field validation with type-safe schema generation and clear error messages.

**pydantic-settings** — Extension of Pydantic for reading application
configuration from environment variables (and `.env` files). Provides type
coercion and validation for all `Settings` fields without custom parsing code.

**SQLAlchemy 2.x** — The 2.x API (`select(...)`, `session.scalars(...)`,
`Mapped[...]` typed columns) gives clean, type-safe database access. It avoids
the legacy `Query` API and pairs naturally with modern Python type hints. The
sync engine is used here; the abstraction would migrate cleanly to async
SQLAlchemy in a future revision.

**Alembic** — The de facto migration tool for SQLAlchemy projects. Supports
autogeneration of migration scripts from model metadata, integrates with the
application config to avoid duplicating the database URL, and provides
repeatable, reversible schema evolution via `upgrade`/`downgrade` commands.

**SQLite** — Sufficient for a single-location polling app. Requires no external
service, and the SQLAlchemy abstraction means a migration to Postgres is
effectively a connection-string change plus running existing migrations.

**httpx** — Modern, async-capable HTTP client with a clean API. Pairs naturally
with FastAPI's async context and supports `MockTransport` for deterministic
tests without network calls.

**tenacity** — Declarative retry logic via decorators keeps the weather client
readable. Supports exponential backoff, per-exception predicates, and a
configurable attempt ceiling — exactly the error-handling profile required.

**APScheduler 3.x** — Lightweight in-process scheduler with an
`AsyncIOScheduler` that integrates cleanly with FastAPI's event loop. Avoids
adding Celery/Redis infrastructure for a single recurring task.

**structlog** — Structured, JSON-formatted logging with minimal boilerplate.
Machine-readable logs from day one without a log-parsing pipeline.

**uv** — Fast Python package manager and project tool. Resolves and installs
dependencies significantly faster than pip, produces a reproducible `uv.lock`,
and its `uv sync --frozen` pattern is ideal for deterministic Docker builds.

**ruff** — Single tool replacing black, isort, flake8, and several plugins.
Fast, zero-config for standard rule sets, and enforces consistent style without
a multi-tool chain.

---

## Assumptions

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

---

## Known issues

### Structured log output not visible in container stdout

Structured log lines for poll events (e.g. `poll_start`, `poll_complete`) do
not surface in container stdout despite structlog being correctly configured and
verified working in isolation. The root cause has not been identified; it may
relate to how APScheduler executes async jobs or how uvicorn captures output
from the background scheduler thread.

**Functional impact:** none. The system polls weather.gov and persists
observations on schedule. The log visibility gap is a developer-experience
issue, not a correctness issue.

**Verification without logs:** query the SQLite database directly to confirm
that observations are being written:

```bash
docker compose exec weather-tracker python -c "import sqlite3; print(sqlite3.connect('/data/weather.db').execute('SELECT COUNT(*) FROM forecast_observation').fetchone())"
```

Run this command once after startup (expect `(72,)`) and again after one poll
interval (expect `(144,)` with default settings). A growing count confirms the
scheduler is firing and data is being persisted correctly.

This issue is tracked for follow-up. See also the "Future work" item for adding
a smoke test that would catch this class of issue earlier.

---

## PR sequence

- **PR 1: Project scaffolding and healthcheck** ([#1](https://github.com/kyuksel/weather-tracker/pull/1)) — repo skeleton, uv,
  ruff, FastAPI app with `/healthz`, Dockerfile, docker-compose, README
  skeleton, CLAUDE.md.
- **PR 2: Database layer and migrations** ([#2](https://github.com/kyuksel/weather-tracker/pull/2)) — SQLAlchemy models, Alembic
  setup, initial migration, schema round-trip test.
- **PR 3: weather.gov client** ([#3](https://github.com/kyuksel/weather-tracker/pull/3)) — httpx-based client with tenacity
  retries, gridpoint cache, configurable forecast window, mocked
  tests.
- **PR 4: Poller and scheduler** ([#4](https://github.com/kyuksel/weather-tracker/pull/4)) — APScheduler integration in FastAPI
  lifespan, poll job that writes observations, error handling, tests.
- **PR 5: Query endpoint** ([#5](https://github.com/kyuksel/weather-tracker/pull/5)) — `GET /forecasts/extremes` with input
  validation, MIN/MAX aggregation, 404 vs count-zero behavior, tests.
- **PR 6: Documentation polish** ([#6](https://github.com/kyuksel/weather-tracker/pull/6)) — fill all README placeholders, add
  `docs/architecture.md` diagram, Known issues section, How to evaluate
  submission section, and Future work additions.

---

## Future work

- Refactor `app/db.py` to lazy engine and session construction so that importing
  application modules does not require all environment variables to be set
  (surfaced during PR 3 conftest fix).
- Smoke test asserting that log output is JSON-formatted on stdout (would have
  caught the deferred logging visibility issue documented in Known issues
  earlier).
- Investigate and resolve the deferred logging visibility issue documented in
  Known issues: identify why structlog poll-event lines do not appear in
  container stdout and fix or work around the root cause.

- POST endpoint to register new locations to track at runtime.
- API authentication (JWT, API key, or fronting gateway).
- Frontend UI for visualizing forecast variation over time.
- Migration to Postgres for production scale.
- Migration to async SQLAlchemy paired with an async-friendly job runner.
- In-memory or Redis-backed caching layer for repeated queries.
- Prometheus `/metrics` endpoint and a sample Grafana dashboard JSON.
  Initial metrics: poll success/failure counter, poll duration
  histogram, API request counter by endpoint and status, observation
  count gauge.
- Multi-replica deployment with leader election or external scheduler
  to avoid double-polling.
- Deduplication strategy for unchanged forecast values across polls
  (storage cost optimization).
- Bulk insert and database connection pool tuning for higher polling
  cadence or many tracked locations.
- Configurable data retention policy (delete observations older than N
  days).
- Continuous integration via GitHub Actions: ruff, pytest, and docker
  build on every PR.
- Continuous deployment: container registry push on merge, deploy to a
  managed orchestrator (Kubernetes, Cloud Run, ECS).
