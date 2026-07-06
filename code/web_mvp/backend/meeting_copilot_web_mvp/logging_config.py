"""Structured logging configuration for the Meeting Copilot web MVP backend.

Configures structlog to emit JSON lines to stdout, and routes stdlib logging
(uvicorn, fastapi) through the same renderer so all logs share one format.

Usage:
    from meeting_copilot_web_mvp.logging_config import configure_logging, get_logger
    configure_logging()
    log = get_logger("meeting_copilot_web_mvp.app")
    log.info("request.end", path="/health", status_code=200, duration_ms=3.2)
"""
from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(json_output: bool = True, stream=None) -> None:
    """Configure structlog + stdlib logging. Safe to call multiple times.

    Args:
        json_output: True emits JSON lines (production); False emits console
            renderer (local dev). Defaults to JSON.
        stream: output stream; defaults to sys.stdout (captured at call time).
    """
    out = stream or sys.stdout
    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=out),
        cache_logger_on_first_use=True,
    )
    # Route stdlib logging through structlog so uvicorn/access logs are JSON too.
    logging.basicConfig(level=logging.INFO, stream=out, format="%(message)s")
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
        logger = logging.getLogger(name)
        logger.handlers = [logging.StreamHandler(out)]
        logger.propagate = False


def get_logger(name: str | None = None):
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
