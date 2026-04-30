"""FastAPI application entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.forecasts import router as forecasts_router
from app.config import get_settings
from app.db import SessionLocal
from app.logging_config import configure_logging
from app.scheduler import build_scheduler
from app.weather_client import WeatherGovClient


def _run_migrations() -> None:
    """Run Alembic migrations to head before accepting traffic."""
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Manage application startup and shutdown."""
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = structlog.get_logger()
    try:
        _run_migrations()
        logger.info("migrations_complete")
    except Exception:
        logger.exception("migrations_failed")
        raise

    weather_client = WeatherGovClient(
        user_agent=settings.weather_gov_user_agent,
        timeout_seconds=settings.weather_gov_timeout_seconds,
        max_retries=settings.weather_gov_max_retries,
    )
    scheduler = build_scheduler(settings, SessionLocal, weather_client)

    app.state.weather_client = weather_client
    app.state.scheduler = scheduler

    scheduler.start()
    logger.info("startup", poll_interval_minutes=settings.poll_interval_minutes)

    yield

    logger.info("scheduler_shutdown")
    scheduler.shutdown(wait=True)
    logger.info("weather_client_shutdown")
    weather_client.close()
    logger.info("shutdown")


app = FastAPI(title="weather-tracker", lifespan=lifespan)
app.include_router(forecasts_router)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Return service health status."""
    return JSONResponse({"status": "ok"})
