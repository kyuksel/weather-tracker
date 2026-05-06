"""Regression tests for the logging configuration."""

import json
import logging
from io import StringIO
from pathlib import Path

import pytest
import structlog

from app.logging_config import configure_logging


@pytest.fixture(autouse=True)
def reset_logging():
    """Reset Python's root logger and structlog config between tests.

    Logging state is process-global; without this fixture, ordering between
    tests in this file determines whether they pass or fail.
    """
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(logging.WARNING)
    structlog.reset_defaults()


def test_configure_logging_emits_json_to_stdout_even_after_handlers_exist(
    capsys,
) -> None:
    """configure_logging must produce visible JSON output even when the root
    logger already has handlers attached (mirroring uvicorn's startup behavior).

    Regression: an earlier version used logging.basicConfig, which is a no-op
    once root has handlers. In containers under uvicorn this caused structlog
    records to be silently absorbed by uvicorn's pre-installed handlers
    instead of being rendered as JSON to stdout.
    """
    pre_existing = logging.StreamHandler(StringIO())
    logging.getLogger().addHandler(pre_existing)

    configure_logging("INFO")

    log = structlog.get_logger("app.poller")
    log.info("poll_complete", observations_written=72, duration_ms=400)

    captured = capsys.readouterr().out
    lines = [line for line in captured.splitlines() if line.startswith("{")]
    assert lines, f"no JSON output captured; got: {captured!r}"

    payload = json.loads(lines[-1])
    assert payload["event"] == "poll_complete"
    assert payload["observations_written"] == 72


def test_running_alembic_migrations_does_not_break_structlog_output(capsys, tmp_path: Path) -> None:
    """Regression: migrations/env.py used to call fileConfig(alembic.ini),
    which replaced the application's logging configuration with one that set
    root to WARNING with a stderr handler and a non-JSON formatter. Structlog
    INFO output stopped reaching stdout afterwards.

    This test sets up the app's logging, runs migrations the same way the
    FastAPI lifespan does, and asserts structlog output is still visible.
    """
    from alembic import command
    from alembic.config import Config

    configure_logging("INFO")

    # Sanity: structlog works before migrations.
    structlog.get_logger("app.poller").info("before_migrations")
    pre = capsys.readouterr().out
    assert any(line.startswith("{") and "before_migrations" in line for line in pre.splitlines()), (
        f"structlog output not visible before migrations; got: {pre!r}"
    )

    # Run migrations the way the app does.
    project_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path}/test.db")
    command.upgrade(cfg, "head")

    # Sanity: structlog still works after migrations.
    structlog.get_logger("app.poller").info("after_migrations")
    post = capsys.readouterr().out
    assert any(line.startswith("{") and "after_migrations" in line for line in post.splitlines()), (
        f"structlog output suppressed after running migrations; got: {post!r}\n"
        f"This usually means migrations/env.py is calling fileConfig, which "
        f"overwrites the app's logging configuration."
    )
