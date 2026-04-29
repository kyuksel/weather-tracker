# weather-tracker

A containerized Python application that polls [weather.gov](https://weather.gov) for hourly
temperature forecasts at a configured location, stores every observation in a
local SQLite database, and exposes a query endpoint that returns the highest
and lowest forecasted temperatures recorded for a given location, date, and
hour. Designed as a take-home coding exercise; scope is intentionally narrow.

## Table of contents

1. [Design overview](#design-overview)
2. [Data model](#data-model)
3. [API contract](#api-contract)
4. [Build and run](#build-and-run)
5. [Configuration](#configuration)
6. [Library choices and rationale](#library-choices-and-rationale)
7. [Assumptions](#assumptions)
8. [PR sequence](#pr-sequence)
9. [Future work](#future-work)

---

## Design overview

<!-- placeholder, filled in PR 6 -->

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

<!-- placeholder, filled in PR 5 -->

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
wait one full interval for the first data. To follow along in real time:

```bash
docker compose logs -f weather-tracker | grep poll_complete
```

You should see a `poll_complete` log line with `observations_written` matching
`FORECAST_HOURS_WINDOW` (default 72) within ~30 seconds of `docker compose up`.

`docker compose down` stops the container. The `weather-data` named volume
persists the SQLite database across restarts. Running `docker compose up`
again (without `--build`) reuses the existing image and volume.

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

## PR sequence

- **PR 1: Project scaffolding and healthcheck** ([#1](https://github.com/kyuksel/weather-tracker/pull/1)) — repo skeleton, uv,
  ruff, FastAPI app with `/healthz`, Dockerfile, docker-compose, README
  skeleton, CLAUDE.md.
- **PR 2: Database layer and migrations** ([#2](https://github.com/kyuksel/weather-tracker/pull/2)) — SQLAlchemy models, Alembic
  setup, initial migration, schema round-trip test.
- **PR 3: weather.gov client** ([#3](https://github.com/kyuksel/weather-tracker/pull/3)) — httpx-based client with tenacity
  retries, gridpoint cache, configurable forecast window, mocked
  tests.
- **PR 4: Poller and scheduler** — APScheduler integration in FastAPI
  lifespan, poll job that writes observations, error handling, tests. *(URL added after merge)*
- **PR 5: Query endpoint** — `GET /forecasts/extremes` with input
  validation, MIN/MAX aggregation, 404 vs count-zero behavior, tests.
- **PR 6: Documentation polish** — fill all README placeholders,
  optional `docs/architecture.md` diagram.

---

## Future work

- Refactor `app/db.py` to lazy engine and session construction so that importing
  application modules does not require all environment variables to be set.

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
