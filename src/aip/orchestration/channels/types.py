"""Common types for retriever channels: ChannelFailure, ChannelResult, safe_retriever.

Every channel returns a ``ChannelResult`` — either hits or a structured
failure.  The ``safe_retriever`` wrapper converts raw exceptions into
``ChannelFailure`` objects so that failures are always structured data,
never just log lines.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from aip.foundation.schemas.retrieval import RetrievalHit

logger = logging.getLogger(__name__)

# A raw retriever is an async callable that takes a query string and returns hits.
RetrieverCallable = Callable[[str], Awaitable[list[RetrievalHit]]]


@dataclass
class ChannelFailure:
    """Structured record of a retriever channel failure.

    Every channel failure is represented as data, not just a log line.
    This enables downstream consumers (traces, dashboards, operators) to
    see *why* a channel failed without scraping logs.

    Attributes:
        channel: Name of the channel that failed (e.g. ``"vector"``, ``"graph"``).
        error_type: Category of the failure.  Common values:
            ``"store_unavailable"`` — the backing store is None or missing.
            ``"store_error"`` — the store raised an exception.
            ``"embed_failed"`` — embedding provider failed.
            ``"no_results"`` — the store succeeded but returned nothing.
            ``"initialization"`` — channel could not be wired at registration time.
        message: Human-readable error description.
        exception_type: Fully-qualified name of the original exception class,
            or empty string if no exception was raised.
        is_fatal: Whether this failure should prevent the pipeline from
            continuing.  All current channel failures are non-fatal
            (the pipeline degrades gracefully), but this flag exists for
            future policy enforcement.
    """

    channel: str
    error_type: str
    message: str
    exception_type: str = ""
    is_fatal: bool = False

    def to_dict(self) -> dict:
        """Serialize for trace/dashboards/API responses."""
        return {
            "channel": self.channel,
            "error_type": self.error_type,
            "message": self.message,
            "exception_type": self.exception_type,
            "is_fatal": self.is_fatal,
        }


@dataclass
class ChannelResult:
    """Outcome of a single retriever channel invocation.

    Wraps the hits list and an optional ``ChannelFailure`` so that
    callers always get structured data regardless of whether the
    channel succeeded, returned empty, or raised an exception.

    Attributes:
        hits: List of retrieval hits (may be empty).
        failure: Structured failure record, or None if the channel succeeded.
        elapsed_ms: Wall-clock time for this channel invocation.
    """

    hits: list[RetrievalHit] = field(default_factory=list)
    failure: ChannelFailure | None = None
    elapsed_ms: float = 0.0

    @property
    def succeeded(self) -> bool:
        """True when the channel returned results without failure."""
        return self.failure is None and len(self.hits) > 0

    @property
    def failed(self) -> bool:
        """True when the channel has a structured failure."""
        return self.failure is not None

    def to_dict(self) -> dict:
        """Serialize for trace/dashboards/API responses."""
        result: dict = {
            "hit_count": len(self.hits),
            "elapsed_ms": round(self.elapsed_ms, 2),
            "succeeded": self.succeeded,
            "failed": self.failed,
        }
        if self.failure is not None:
            result["failure"] = self.failure.to_dict()
        return result


def safe_retriever(
    channel_name: str,
    retriever_fn: RetrieverCallable,
    *,
    log_level: str = "debug",
) -> RetrieverCallable:
    """Wrap a retriever callable so that exceptions become structured ChannelFailures.

    The wrapper:
    1. Calls ``retriever_fn(query)`` and measures wall-clock time.
    2. On success, returns the hits list (the orchestrator stamps
       source_channel / rank_in_channel after this point).
    3. On exception, logs the failure at the requested level and
       returns an empty list.  The ``ChannelFailure`` is stored on
       the wrapper instance for later inspection by the pipeline.

    The returned callable has a ``last_failure`` attribute set to
    ``ChannelFailure | None`` after each invocation.

    Args:
        channel_name: Name of the channel (for logging and failure records).
        retriever_fn: The raw async retriever to wrap.
        log_level: Log level for failures (``"debug"``, ``"warning"``, or ``"error"``).
            Non-critical channels (vector, graph, wiki, procedural) default to
            ``"debug"``; primary channels (fts, corpus) default to ``"warning"``.

    Returns:
        An async callable with the same signature as ``retriever_fn``.
    """

    _last_failure: ChannelFailure | None = None

    async def _wrapped(query: str) -> list[RetrievalHit]:
        nonlocal _last_failure
        start = time.monotonic()
        try:
            hits = await retriever_fn(query)
            elapsed = (time.monotonic() - start) * 1000.0
            _last_failure = None
            if not hits:
                _last_failure = ChannelFailure(
                    channel=channel_name,
                    error_type="no_results",
                    message=f"Channel '{channel_name}' returned 0 hits for query",
                )
            return hits
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000.0
            failure = ChannelFailure(
                channel=channel_name,
                error_type="store_error",
                message=str(exc),
                exception_type=type(exc).__qualname__,
            )
            _last_failure = failure

            # Log at the configured level
            log_fn = getattr(logger, log_level, logger.debug)
            log_fn(
                "Channel '%s' retriever failed: %s",
                channel_name,
                exc,
            )
            return []

    # Attach the last_failure attribute for pipeline inspection
    _wrapped.last_failure = None  # type: ignore[attr-defined]

    def _get_last_failure() -> ChannelFailure | None:
        return _last_failure

    _wrapped.get_last_failure = _get_last_failure  # type: ignore[attr-defined]
    return _wrapped
