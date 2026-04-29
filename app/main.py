"""FastAPI application entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.logging_config import configure_logging


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
    logger.info("startup", poll_interval_minutes=settings.poll_interval_minutes)
    yield
    logger.info("shutdown")


app = FastAPI(title="weather-tracker", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Return service health status."""
    return JSONResponse({"status": "ok"})
