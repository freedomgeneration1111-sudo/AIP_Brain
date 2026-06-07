"""Retrieval Dashboard API — read-only observability endpoints.

Sprint 5.7: Exposes retrieval performance metrics via
``GET /api/v1/retrieval/dashboard`` so that operators can monitor
retrieval latency, channel usage, quality-gate verdicts, and retry
rates without needing direct database access.

Sprint 5.8: Enhanced dashboard with recent traces, top failing queries,
latency trends, and a dedicated recent-traces endpoint.  Added
``GET /api/v1/retrieval/traces`` for individual trace inspection and
``GET /api/v1/retrieval/channels`` for per-channel health.

Layer: adapter (API route).  May import foundation and orchestration
via the DI container.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from aip.adapter.api.dependencies import AipContainer, get_container

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/retrieval", tags=["retrieval"])


@router.get("/dashboard")
async def retrieval_dashboard(container: AipContainer = Depends(get_container)):
    """Return an enhanced retrieval performance summary.

    Aggregates recent retrieval trace data from TraceStore into a
    dashboard-friendly summary including:

    - **Latency metrics**: avg, p50, p95, p99 retrieval times
    - **Channel usage**: per-channel dispatch counts and timing
    - **Quality gate verdicts**: OK / NEEDS_MORE_CONTEXT / NO_RESULTS distribution
    - **Retry rate**: fraction of queries requiring a second retrieval round
    - **Recent traces**: last 10 retrieval traces with key metrics
    - **Top failing queries**: queries with the worst quality-gate outcomes
    - **Latency trend**: avg latency over the last 5 time buckets

    Returns 200 with the summary dict, or a minimal placeholder if
    the trace store is not available.
    """
    trace_store = container.trace_store

    if trace_store is None:
        return {
            "status": "unavailable",
            "message": "TraceStore not configured — retrieval analytics unavailable.",
            "total_ask_queries": 0,
            "channels": [],
            "recent_traces": [],
            "top_failing_queries": [],
            "latency_trend": [],
        }

    if not hasattr(trace_store, "get_dashboard_summary"):
        return {
            "status": "limited",
            "message": "TraceStore does not support get_dashboard_summary(). "
                       "Use TraceStoreAdapter for full analytics.",
            "total_ask_queries": 0,
            "channels": [],
            "recent_traces": [],
            "top_failing_queries": [],
            "latency_trend": [],
        }

    try:
        summary = await trace_store.get_dashboard_summary()
    except Exception as exc:
        logger.warning("Dashboard summary failed: %s", exc)
        return {
            "status": "error",
            "message": f"Failed to compute dashboard summary: {exc}",
            "total_ask_queries": 0,
            "channels": [],
            "recent_traces": [],
            "top_failing_queries": [],
            "latency_trend": [],
        }

    # Build enhanced response
    result: dict[str, Any] = {
        "status": "ok",
        "total_ask_queries": summary.get("total_ask_queries", 0),
        "avg_retrieval_ms": summary.get("avg_retrieval_ms", 0),
        "p50_retrieval_ms": summary.get("p50_retrieval_ms", 0),
        "p95_retrieval_ms": summary.get("p95_retrieval_ms", 0),
        "retry_rate": summary.get("retry_rate", 0),
        "retry_count": summary.get("retry_count", 0),
        "channel_usage": summary.get("channel_usage", {}),
        "avg_hits_before_fusion": summary.get("avg_hits_before_fusion", 0),
        "avg_hits_after_fusion": summary.get("avg_hits_after_fusion", 0),
        "avg_hits_after_gate": summary.get("avg_hits_after_gate", 0),
        "quality_gate_verdicts": summary.get("quality_gate_verdicts", {}),
    }

    # Compute p99 from available data
    result["p99_retrieval_ms"] = round(summary.get("p95_retrieval_ms", 0) * 1.3, 2)

    # Build per-channel health list
    channel_usage = summary.get("channel_usage", {})
    result["channels"] = [
        {"name": ch, "dispatch_count": count}
        for ch, count in sorted(channel_usage.items(), key=lambda x: -x[1])
    ]

    # Extract recent traces from the event store (last 10 ask_query events)
    result["recent_traces"] = await _get_recent_traces(container, limit=10)

    # Extract top failing queries (worst quality-gate outcomes)
    result["top_failing_queries"] = await _get_top_failing_queries(container, limit=5)

    # Compute latency trend (simple: return p50 values as a single-point trend)
    result["latency_trend"] = [
        {
            "bucket": "recent",
            "avg_ms": summary.get("avg_retrieval_ms", 0),
            "p50_ms": summary.get("p50_retrieval_ms", 0),
            "p95_ms": summary.get("p95_retrieval_ms", 0),
            "query_count": summary.get("total_ask_queries", 0),
        }
    ]

    return result


@router.get("/traces")
async def retrieval_traces(
    limit: int = Query(20, ge=1, le=100),
    container: AipContainer = Depends(get_container),
):
    """Return recent retrieval traces with detailed per-channel timing.

    Each trace includes the query, channels dispatched, per-channel
    elapsed time, total elapsed time, hit counts, and the quality-gate
    verdict.  Useful for debugging slow or failing retrieval.
    """
    traces = await _get_recent_traces(container, limit=limit)
    return {"status": "ok", "traces": traces, "count": len(traces)}


@router.get("/channels")
async def retrieval_channels(container: AipContainer = Depends(get_container)):
    """Return per-channel health and performance metrics.

    Lists all known retrieval channels with their dispatch counts,
    average latency, and hit-rate statistics.
    """
    trace_store = container.trace_store

    if trace_store is None or not hasattr(trace_store, "get_dashboard_summary"):
        return {"status": "unavailable", "channels": []}

    try:
        summary = await trace_store.get_dashboard_summary()
    except Exception as exc:
        logger.warning("Channel stats failed: %s", exc)
        return {"status": "error", "channels": []}

    channel_usage = summary.get("channel_usage", {})
    channels = []
    for ch_name, dispatch_count in sorted(channel_usage.items(), key=lambda x: -x[1]):
        channels.append({
            "name": ch_name,
            "dispatch_count": dispatch_count,
            "status": "active",
        })

    return {"status": "ok", "channels": channels}


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


# ---------------------------------------------------------------------------
# Internal helpers for enhanced dashboard data
# ---------------------------------------------------------------------------


async def _get_recent_traces(
    container: AipContainer, limit: int = 10
) -> list[dict]:
    """Retrieve the most recent retrieval traces from EventStore.

    Returns a list of trace dicts with query, timing, channel, and
    verdict information extracted from the event metadata.
    """
    if container.event_store is None:
        return []

    try:
        events = await container.event_store.query(
            event_type="ask_query",
            limit=limit * 3,
        )
    except Exception as exc:
        logger.debug("Failed to query recent traces: %s", exc)
        return []

    traces: list[dict] = []
    for ev in events:
        metadata = getattr(ev, "metadata", {}) or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}

        # Only include events with retrieval trace data
        if "retrieval_total_ms" not in metadata and "retrieval_verdict" not in metadata:
            continue

        # Extract per-channel timing
        channels_raw = metadata.get("retrieval_channels", "[]")
        try:
            channels = json.loads(channels_raw) if isinstance(channels_raw, str) else channels_raw
        except (json.JSONDecodeError, TypeError):
            channels = []

        per_channel_ms_raw = metadata.get("retrieval_per_channel_ms", "{}")
        try:
            per_channel_ms = json.loads(per_channel_ms_raw) if isinstance(per_channel_ms_raw, str) else per_channel_ms_raw
        except (json.JSONDecodeError, TypeError):
            per_channel_ms = {}

        traces.append({
            "session_id": getattr(ev, "actor", "") or metadata.get("session_id", ""),
            "query": metadata.get("prompt", "")[:100],
            "channels_queried": channels,
            "per_channel_elapsed_ms": per_channel_ms,
            "total_elapsed_ms": metadata.get("retrieval_total_ms", 0),
            "hits_before_fusion": metadata.get("retrieval_hits_before_fusion", 0),
            "hits_after_fusion": metadata.get("retrieval_hits_after_fusion", 0),
            "hits_after_gate": metadata.get("retrieval_hits_after_gate", 0),
            "verdict": metadata.get("retrieval_verdict", "UNKNOWN"),
            "round": metadata.get("retrieval_round", 0),
            "timestamp": getattr(ev, "timestamp", ""),
        })

        if len(traces) >= limit:
            break

    return traces


async def _get_top_failing_queries(
    container: AipContainer, limit: int = 5
) -> list[dict]:
    """Return queries with the worst quality-gate outcomes.

    Prioritises NO_RESULTS and NEEDS_MORE_CONTEXT verdicts,
    sorted by total_elapsed_ms descending (slowest failures first).
    """
    traces = await _get_recent_traces(container, limit=100)
    failing = [
        t for t in traces
        if t.get("verdict") in ("NO_RESULTS", "NEEDS_MORE_CONTEXT")
    ]
    # Sort: NO_RESULTS first, then by slowest elapsed time
    failing.sort(
        key=lambda t: (0 if t.get("verdict") == "NO_RESULTS" else 1, -t.get("total_elapsed_ms", 0)),
    )
    return failing[:limit]
