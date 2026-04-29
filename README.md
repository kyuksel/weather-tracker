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

<!-- placeholder, filled in PR 2 -->

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

`docker compose down` stops the container. The `weather-data` named volume
persists the SQLite database across restarts. Running `docker compose up`
again (without `--build`) reuses the existing image and volume.

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

**SQLAlchemy 2.x + Alembic** — SQLAlchemy's 2.x API (`select(...)`,
`session.scalars(...)`) gives clean, type-safe database access without the
verbosity of raw SQL. Alembic is the standard migration tool for SQLAlchemy
projects, making schema evolution repeatable and reversible.

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

- **PR 1: Project scaffolding and healthcheck** — repo skeleton, uv,
  ruff, FastAPI app with `/healthz`, Dockerfile, docker-compose, README
  skeleton, CLAUDE.md.
- **PR 2: Database layer and migrations** — SQLAlchemy models, Alembic
  setup, initial migration, schema round-trip test.
- **PR 3: weather.gov client** — httpx-based client with tenacity
  retries, gridpoint cache, configurable forecast window, mocked
  tests.
- **PR 4: Poller and scheduler** — APScheduler integration in FastAPI
  lifespan, poll job that writes observations, error handling, tests.
- **PR 5: Query endpoint** — `GET /forecasts/extremes` with input
  validation, MIN/MAX aggregation, 404 vs count-zero behavior, tests.
- **PR 6: Documentation polish** — fill all README placeholders,
  optional `docs/architecture.md` diagram.

---

## Future work

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
