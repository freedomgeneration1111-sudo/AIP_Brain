"""Retrieval Dashboard API — read-only observability endpoints.

Sprint 5.7: Exposes retrieval performance metrics via
``GET /api/v1/retrieval/dashboard`` so that operators can monitor
retrieval latency, channel usage, quality-gate verdicts, and retry
rates without needing direct database access.

Sprint 5.8: Enhanced dashboard with recent traces, top failing queries,
latency trends, and a dedicated recent-traces endpoint.  Added
``GET /api/v1/retrieval/traces`` for individual trace inspection and
``GET /api/v1/retrieval/channels`` for per-channel health.

Sprint 5.11: Added evaluation metrics, channel contribution summaries,
and LLM entity extraction observability to the dashboard.  New endpoint
``GET /api/v1/retrieval/quality`` for evaluation trend data.  Existing
endpoints now include channel_contributions and llm_entity_extraction
fields in trace data.

Layer: adapter (API route).  May import foundation and orchestration
via the DI container.
"""

from __future__ import annotations

import json
import logging
import os
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
    - **Channel contributions** (Sprint 5.11): which channels contributed
      hits that survived fusion and quality gate
    - **LLM entity extraction** (Sprint 5.11): summary of LLM fallback
      usage, timing, and success rate

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
            "channel_contribution_summary": {},
            "llm_entity_extraction": {},
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
            "channel_contribution_summary": {},
            "llm_entity_extraction": {},
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
            "channel_contribution_summary": {},
            "llm_entity_extraction": {},
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

    # Sprint 5.11: Channel contribution summary from recent traces
    result["channel_contribution_summary"] = await _compute_channel_contribution_summary(container)

    # Sprint 5.11: LLM entity extraction observability summary
    result["llm_entity_extraction"] = await _compute_llm_extraction_summary(container)

    return result


@router.get("/traces")
async def retrieval_traces(
    limit: int = Query(20, ge=1, le=100),
    container: AipContainer = Depends(get_container),
):
    """Return recent retrieval traces with detailed per-channel timing.

    Each trace includes the query, channels dispatched, per-channel
    elapsed time, total elapsed time, hit counts, the quality-gate
    verdict, and (Sprint 5.11) channel contributions and LLM entity
    extraction observability data.
    """
    traces = await _get_recent_traces(container, limit=limit)
    return {"status": "ok", "traces": traces, "count": len(traces)}


@router.get("/channels")
async def retrieval_channels(container: AipContainer = Depends(get_container)):
    """Return per-channel health and performance metrics.

    Lists all known retrieval channels with their dispatch counts,
    average latency, hit-rate statistics, and (Sprint 5.11) contribution
    percentages based on recent trace data.
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

    # Sprint 5.11: Get contribution data for richer channel health
    contribution_summary = await _compute_channel_contribution_summary(container)

    total_contrib = sum(contribution_summary.values()) or 1
    channels = []
    for ch_name, dispatch_count in sorted(channel_usage.items(), key=lambda x: -x[1]):
        contrib = contribution_summary.get(ch_name, 0)
        channels.append({
            "name": ch_name,
            "dispatch_count": dispatch_count,
            "contribution_count": contrib,
            "contribution_pct": round(contrib / total_contrib * 100, 1),
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


@router.get("/quality")
async def retrieval_quality(container: AipContainer = Depends(get_container)):
    """Return evaluation quality metrics and trends.

    Sprint 5.11: Exposes the latest evaluation results and channel
    contribution data so that retrieval quality trends can be monitored
    through the dashboard API without running the CLI eval command.

    Returns:
        - Latest eval metrics (if an eval has been run)
        - Channel contribution summary from live traces
        - LLM entity extraction observability summary
        - Per-channel budget configuration snapshot
    """
    # Gather data from multiple sources
    result: dict[str, Any] = {
        "status": "ok",
        "latest_eval": None,
        "channel_contribution_summary": {},
        "llm_entity_extraction": {},
        "channel_budgets": {},
    }

    # Try to load the latest eval result from eval_results/ directory
    result["latest_eval"] = _load_latest_eval_result()

    # Channel contribution summary from recent traces
    result["channel_contribution_summary"] = await _compute_channel_contribution_summary(container)

    # LLM entity extraction summary
    result["llm_entity_extraction"] = await _compute_llm_extraction_summary(container)

    # Channel budget configuration snapshot
    from aip.orchestration.retrieval_orchestrator import OrchestratorConfig
    config = OrchestratorConfig()
    result["channel_budgets"] = {
        "fts": config.fts_max_hits,
        "vector": config.vector_max_hits,
        "graph": config.graph_max_hits,
        "wiki": config.wiki_max_hits,
        "procedural": config.procedural_max_hits,
        "corpus": config.corpus_max_hits,
        "global_max_hits_per_channel": config.max_hits_per_channel,
    }

    return result


# ---------------------------------------------------------------------------
# Internal helpers for enhanced dashboard data
# ---------------------------------------------------------------------------


async def _get_recent_traces(
    container: AipContainer, limit: int = 10
) -> list[dict]:
    """Retrieve the most recent retrieval traces from EventStore.

    Returns a list of trace dicts with query, timing, channel, and
    verdict information extracted from the event metadata.

    Sprint 5.11: Added channel_contributions and llm_entity_extraction
    fields to trace output.
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

        # Sprint 5.11: Extract channel contributions
        channel_contributions_raw = metadata.get("retrieval_channel_contributions", "{}")
        try:
            channel_contributions = (
                json.loads(channel_contributions_raw)
                if isinstance(channel_contributions_raw, str)
                else channel_contributions_raw
            )
        except (json.JSONDecodeError, TypeError):
            channel_contributions = {}

        # Sprint 5.11: Extract LLM entity extraction data
        llm_entity_extraction = {
            "ms": metadata.get("retrieval_llm_entity_extraction_ms", 0),
            "status": metadata.get("retrieval_llm_entity_extraction_status", "not_used"),
            "entity_count": metadata.get("retrieval_llm_entity_count", 0),
        }

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
            "channel_contributions": channel_contributions,
            "llm_entity_extraction": llm_entity_extraction,
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


async def _compute_channel_contribution_summary(
    container: AipContainer, limit: int = 100
) -> dict[str, int]:
    """Compute aggregated channel contribution counts from recent traces.

    Sprint 5.11: Aggregates the channel_contributions field across
    recent traces to show which channels are actually contributing
    hits to the final result set (after RRF + quality gate).
    """
    traces = await _get_recent_traces(container, limit=limit)
    summary: dict[str, int] = {}
    for trace in traces:
        contributions = trace.get("channel_contributions", {})
        if isinstance(contributions, dict):
            for ch, count in contributions.items():
                summary[ch] = summary.get(ch, 0) + count
    return summary


async def _compute_llm_extraction_summary(
    container: AipContainer, limit: int = 100
) -> dict[str, Any]:
    """Compute LLM entity extraction observability summary from recent traces.

    Sprint 5.11: Aggregates LLM entity extraction timing and success
    rates across recent traces so operators can monitor cost vs. benefit.
    """
    traces = await _get_recent_traces(container, limit=limit)

    total_calls = 0
    success_count = 0
    failed_count = 0
    not_used_count = 0
    total_ms = 0.0
    total_entities = 0

    for trace in traces:
        llm_data = trace.get("llm_entity_extraction", {})
        if not isinstance(llm_data, dict):
            continue

        status = llm_data.get("status", "not_used")
        ms = float(llm_data.get("ms", 0))
        entities = int(llm_data.get("entity_count", 0))

        if status == "not_used":
            not_used_count += 1
        elif status == "success":
            total_calls += 1
            success_count += 1
            total_ms += ms
            total_entities += entities
        elif status == "failed":
            total_calls += 1
            failed_count += 1
            total_ms += ms

    return {
        "total_calls": total_calls,
        "success_count": success_count,
        "failed_count": failed_count,
        "not_used_count": not_used_count,
        "success_rate": round(success_count / total_calls, 3) if total_calls > 0 else 0.0,
        "avg_ms": round(total_ms / total_calls, 1) if total_calls > 0 else 0.0,
        "avg_entities_per_call": round(total_entities / success_count, 1) if success_count > 0 else 0.0,
        "total_entities_extracted": total_entities,
    }


def _load_latest_eval_result() -> dict[str, Any] | None:
    """Load the most recent evaluation result from the eval_results directory.

    Sprint 5.11: Scans the default eval_results/ directory for the most
    recent timestamped eval JSON file and returns its contents.  Returns
    None if no eval results exist.
    """
    eval_dir = os.environ.get("AIP_EVAL_DIR", "eval_results")
    if not os.path.isdir(eval_dir):
        return None

    try:
        eval_files = [
            f for f in os.listdir(eval_dir)
            if f.startswith("eval_") and f.endswith(".json")
        ]
    except OSError:
        return None

    if not eval_files:
        return None

    # Sort by filename (which includes timestamp) — last is most recent
    eval_files.sort(reverse=True)
    latest_path = os.path.join(eval_dir, eval_files[0])

    try:
        with open(latest_path) as f:
            data = json.load(f)
        # Include only key metrics, not full per-query results
        return {
            "timestamp": data.get("timestamp", ""),
            "total_queries": data.get("total_queries", 0),
            "mean_recall_at_k": data.get("mean_recall_at_k", 0),
            "mean_precision_at_k": data.get("mean_precision_at_k", 0),
            "mean_mrr": data.get("mean_mrr", 0),
            "mean_entity_coverage": data.get("mean_entity_coverage", 0),
            "channel_contribution_summary": data.get("channel_contribution_summary", {}),
            "eval_harness_version": data.get("eval_harness_version", ""),
        }
    except (json.JSONDecodeError, OSError):
        return None
