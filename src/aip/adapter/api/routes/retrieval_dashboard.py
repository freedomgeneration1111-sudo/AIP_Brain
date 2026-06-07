"""Retrieval Dashboard API — lightweight read-only observability endpoint.

Sprint 5.7: Exposes retrieval performance metrics via
``GET /api/v1/retrieval/dashboard`` so that operators can monitor
retrieval latency, channel usage, quality-gate verdicts, and retry
rates without needing direct database access.

Layer: adapter (API route).  May import foundation and orchestration
via the DI container.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from aip.adapter.api.dependencies import AipContainer, get_container

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/retrieval", tags=["retrieval"])


@router.get("/dashboard")
async def retrieval_dashboard(container: AipContainer = Depends(get_container)):
    """Return a lightweight retrieval performance summary.

    Aggregates recent retrieval trace data from TraceStore into a
    dashboard-friendly summary including latency percentiles, channel
    usage, retry rates, and quality-gate verdicts.

    Returns 200 with the summary dict, or a minimal placeholder if
    the trace store is not available.
    """
    trace_store = container.trace_store

    if trace_store is None:
        return {
            "status": "unavailable",
            "message": "TraceStore not configured — retrieval analytics unavailable.",
            "total_ask_queries": 0,
        }

    # Check if the trace store has the analytics method (TraceStoreAdapter)
    if hasattr(trace_store, "get_dashboard_summary"):
        try:
            summary = await trace_store.get_dashboard_summary()
            summary["status"] = "ok"
            return summary
        except Exception as exc:
            logger.warning("Dashboard summary failed: %s", exc)
            return {
                "status": "error",
                "message": f"Failed to compute dashboard summary: {exc}",
                "total_ask_queries": 0,
            }

    # Fallback for bare TraceStore protocol (no analytics method)
    return {
        "status": "limited",
        "message": "TraceStore does not support get_dashboard_summary(). "
                   "Use TraceStoreAdapter for full analytics.",
        "total_ask_queries": 0,
    }


@router.get("/stats")
async def retrieval_stats(container: AipContainer = Depends(get_container)):
    """Return raw retrieval statistics (per-channel).

    A simpler endpoint than /dashboard for programmatic consumers
    that just want channel-level timing and hit counts.
    """
    trace_store = container.trace_store

    if trace_store is None:
        return {"status": "unavailable", "channels": {}}

    if hasattr(trace_store, "get_dashboard_summary"):
        try:
            summary = await trace_store.get_dashboard_summary()
            return {
                "status": "ok",
                "channel_usage": summary.get("channel_usage", {}),
                "avg_retrieval_ms": summary.get("avg_retrieval_ms", 0),
                "p50_retrieval_ms": summary.get("p50_retrieval_ms", 0),
                "p95_retrieval_ms": summary.get("p95_retrieval_ms", 0),
                "retry_rate": summary.get("retry_rate", 0),
                "quality_gate_verdicts": summary.get("quality_gate_verdicts", {}),
            }
        except Exception as exc:
            logger.warning("Retrieval stats failed: %s", exc)
            return {"status": "error", "channels": {}}

    return {"status": "limited", "channels": {}}
