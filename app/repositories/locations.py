"""Repository functions for the Location model."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Location


def get_or_create_location(session: Session, latitude: float, longitude: float) -> Location:
    """Return the Location row for (latitude, longitude), creating it if absent.

    If two callers attempt to create the same location concurrently, the loser
    catches IntegrityError on flush, rolls back, and re-queries. This is
    defensive: the poller is single-threaded, but the unique constraint should
    always have a matching code path.

    Args:
        session: Active SQLAlchemy session. The caller controls the transaction.
        latitude: Location latitude.
        longitude: Location longitude.

    Returns:
        The existing or newly created Location row.
    """
    existing = session.scalars(
        select(Location).where(
            Location.latitude == latitude,
            Location.longitude == longitude,
        )
    ).first()
    if existing is not None:
        return existing

    location = Location(latitude=latitude, longitude=longitude)
    session.add(location)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return session.scalars(  # type: ignore[return-value]
            select(Location).where(
                Location.latitude == latitude,
                Location.longitude == longitude,
            )
        ).first()

    return location
