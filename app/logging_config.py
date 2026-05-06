"""Configure structlog to emit JSON to stdout.

Why this is non-trivial: uvicorn pre-configures Python's root logger before
the FastAPI lifespan runs. logging.basicConfig is a no-op once the root logger
has handlers, so a naive basicConfig call does nothing and structlog records
get routed through whichever handler uvicorn installed. We explicitly install
our own stdout handler on the root logger and let our app loggers propagate to
it, while leaving uvicorn's own loggers alone.
"""

import logging
import sys

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    """Set up structlog with JSON output at the given log level.

    Args:
        log_level: Standard Python log level name (e.g. "INFO", "DEBUG").
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Install our own stdout handler on the root logger. We cannot rely on
    # logging.basicConfig because uvicorn has already attached handlers by the
    # time the FastAPI lifespan runs, which makes basicConfig a no-op.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    root.setLevel(level)
    # Remove any inherited handlers so we own root's output and structlog
    # records do not get swallowed or double-formatted.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
