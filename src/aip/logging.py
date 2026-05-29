"""Structured logging with correlation ID propagation.

Uses structlog for structured, human-readable console output in development
and JSON output in production (controlled by AIP_JSON_LOGS env var).

Correlation IDs are propagated via contextvars, so they flow naturally
through async code and background tasks without explicit threading.
"""

from __future__ import annotations

import logging
import os
import uuid
from contextvars import ContextVar

import structlog

# ---------------------------------------------------------------------------
# Correlation ID propagation
# ---------------------------------------------------------------------------

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    """Return the current correlation ID, if set."""
    return _correlation_id.get()


def set_correlation_id(request_id: str | None) -> None:
    """Set the correlation ID for the current async context."""
    _correlation_id.set(request_id)


def new_correlation_id() -> str:
    """Generate a fresh correlation ID (UUID4)."""
    return uuid.uuid4().hex[:16]


# ---------------------------------------------------------------------------
# structlog configuration
# ---------------------------------------------------------------------------

_configured = False


def _inject_correlation_id(logger, method_name, event_dict):
    """Processor that adds the current correlation ID to every log event."""
    cid = get_correlation_id()
    if cid is not None:
        event_dict["request_id"] = cid
    return event_dict


def configure_logging() -> None:
    """Initialize structlog and stdlib logging integration.

    Call once at application startup. Safe to call multiple times —
    subsequent calls are no-ops.

    Output format:
      - Development (default): human-readable console output
      - Production (AIP_JSON_LOGS=true): JSON output for log aggregation
    """
    global _configured
    if _configured:
        return
    _configured = True

    json_mode = os.environ.get("AIP_JSON_LOGS", "").lower() in ("true", "1", "yes")

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        _inject_correlation_id,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if json_mode:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging through structlog so that `logging.getLogger()`
    # calls in third-party code also get structured output.
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)

    # Default level; individual modules can override
    level = os.environ.get("AIP_LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, level, logging.INFO))

    # Quieten noisy third-party loggers
    for name in ("httpx", "httpcore", "uvicorn.access", "aiosqlite"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structured logger bound to the given name.

    Usage::

        from aip.logging import get_logger
        log = get_logger(__name__)
        log.info("component_started", component="beast")
    """
    return structlog.get_logger(name)
