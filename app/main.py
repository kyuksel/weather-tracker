"""FastAPI application entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.logging_config import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Manage application startup and shutdown.

    Placeholder for scheduler wiring in a future PR.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = structlog.get_logger()
    logger.info("startup", poll_interval_minutes=settings.poll_interval_minutes)
    yield
    logger.info("shutdown")


app = FastAPI(title="weather-tracker", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Return service health status."""
    return JSONResponse({"status": "ok"})
