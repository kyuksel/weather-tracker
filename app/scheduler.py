"""APScheduler integration: builds the AsyncIOScheduler for the FastAPI lifespan."""

import asyncio
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.poller import poll_once
from app.weather_client import WeatherGovClient


def build_scheduler(
    settings: Settings,
    session_factory: sessionmaker,
    weather_client: WeatherGovClient,
) -> AsyncIOScheduler:
    """Construct and configure an AsyncIOScheduler for weather polling.

    The scheduler is returned but NOT started; the caller starts it inside
    the FastAPI lifespan to ensure it runs on the correct event loop.

    Args:
        settings: Application configuration.
        session_factory: SQLAlchemy sessionmaker for DB access.
        weather_client: Configured weather.gov HTTP client.

    Returns:
        A configured AsyncIOScheduler ready to be started.
    """
    scheduler = AsyncIOScheduler()

    async def _poll_job() -> None:
        await asyncio.to_thread(
            poll_once,
            session_factory,
            weather_client,
            settings.tracked_latitude,
            settings.tracked_longitude,
            settings.forecast_hours_window,
        )

    scheduler.add_job(
        _poll_job,
        trigger=IntervalTrigger(minutes=settings.poll_interval_minutes),
        id="poll_weather",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.utcnow(),
    )
    return scheduler
