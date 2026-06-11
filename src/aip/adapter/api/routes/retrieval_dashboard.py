"""Retrieval Dashboard API — read-only observability endpoints.

Exposes retrieval performance metrics via
``GET /api/v1/retrieval/dashboard`` so that operators can monitor
retrieval latency, channel usage, quality-gate verdicts, and retry
rates without needing direct database access.

Includes recent traces, top failing queries, latency trends, and
dedicated endpoints for individual trace inspection, per-channel health,
evaluation trend data, and adaptive per-channel budget tuning suggestions.
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
    - **Channel contributions**: which channels contributed
      hits that survived fusion and quality gate
    - **LLM entity extraction**: summary of LLM fallback
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
            "message": "TraceStore does not support get_dashboard_summary(). Use TraceStoreAdapter for full analytics.",
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
        {"name": ch, "dispatch_count": count} for ch, count in sorted(channel_usage.items(), key=lambda x: -x[1])
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

    # Channel contribution summary from recent traces
    result["channel_contribution_summary"] = await _compute_channel_contribution_summary(container)

    # LLM entity extraction observability summary
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
    verdict, and channel contributions and LLM entity
    extraction observability data.
    """
    traces = await _get_recent_traces(container, limit=limit)
    return {"status": "ok", "traces": traces, "count": len(traces)}


@router.get("/traces/session/{session_id}")
async def retrieval_trace_by_session(
    session_id: str,
    container: AipContainer = Depends(get_container),
):
    """Return the most recent retrieval trace for a given session.

    Queries the EventStore for the latest ``ask_query`` event whose
    actor (or metadata session_id) matches *session_id* and returns
    the trace metadata from that event's metadata_json.

    Returns ``{"status": "not_found", "trace": null}`` when no trace
    exists for the session.
    """
    if container.event_store is None:
        return {"status": "not_found", "trace": None}

    try:
        events = await container.event_store.query(
            event_type="ask_query",
            limit=100,
        )
    except Exception as exc:
        logger.debug("Failed to query traces for session %s: %s", session_id, exc)
        return {"status": "not_found", "trace": None}

    for ev in events:
        # Match by actor field first, then by metadata session_id
        actor = getattr(ev, "actor", "") or ""
        metadata = getattr(ev, "metadata", {}) or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}

        ev_session = metadata.get("session_id", "")
        if actor != session_id and ev_session != session_id:
            continue

        # Only include events with retrieval trace data
        if "retrieval_total_ms" not in metadata and "retrieval_verdict" not in metadata:
            continue

        # Build trace dict from event metadata
        channels_raw = metadata.get("retrieval_channels", "[]")
        try:
            channels = json.loads(channels_raw) if isinstance(channels_raw, str) else channels_raw
        except (json.JSONDecodeError, TypeError):
            channels = []

        per_channel_ms_raw = metadata.get("retrieval_per_channel_ms", "{}")
        try:
            per_channel_ms = (
                json.loads(per_channel_ms_raw) if isinstance(per_channel_ms_raw, str) else per_channel_ms_raw
            )
        except (json.JSONDecodeError, TypeError):
            per_channel_ms = {}

        channel_contributions_raw = metadata.get("retrieval_channel_contributions", "{}")
        try:
            channel_contributions = (
                json.loads(channel_contributions_raw)
                if isinstance(channel_contributions_raw, str)
                else channel_contributions_raw
            )
        except (json.JSONDecodeError, TypeError):
            channel_contributions = {}

        trace = {
            "session_id": session_id,
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
            "timestamp": getattr(ev, "timestamp", ""),
            "lexical_only": metadata.get("lexical_only", False),
            "vector_contributed": metadata.get("vector_contributed", False),
        }
        return {"status": "ok", "trace": trace}

    return {"status": "not_found", "trace": None}


@router.get("/channels")
async def retrieval_channels(container: AipContainer = Depends(get_container)):
    """Return per-channel health and performance metrics.

    Lists all known retrieval channels with their dispatch counts,
    average latency, hit-rate statistics, and contribution
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

    # Get contribution data for richer channel health
    contribution_summary = await _compute_channel_contribution_summary(container)

    total_contrib = sum(contribution_summary.values()) or 1
    channels = []
    for ch_name, dispatch_count in sorted(channel_usage.items(), key=lambda x: -x[1]):
        contrib = contribution_summary.get(ch_name, 0)
        channels.append(
            {
                "name": ch_name,
                "dispatch_count": dispatch_count,
                "contribution_count": contrib,
                "contribution_pct": round(contrib / total_contrib * 100, 1),
                "status": "active",
            }
        )

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

    Exposes the latest evaluation results and channel
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

    # Channel budget configuration snapshot (container-mediated, Chunk 6)
    OrchestratorConfig = getattr(container, "_orchestrator_config_class", None)
    if OrchestratorConfig is not None:
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


@router.post("/test")
async def retrieval_test(payload: dict, container: AipContainer = Depends(get_container)):
    """Execute a standalone retrieval test without synthesizing an answer.

    This endpoint runs the retrieval pipeline with user-specified channel
    selection and returns detailed per-channel results, health, latency,
    fusion/ranking outcomes, and selected context — without dispatching
    to any model for answer synthesis.

    Accepts:
      - query (str, required): The query text to test.
      - selected_channels (list[str], optional): Channels to enable.
        Supported: "fts", "vector", "graph", "wiki", "procedural", "corpus".
        Defaults to ["fts", "vector", "corpus"] if not specified.
      - limit (int, optional): Max total hits after fusion (default: 20).
      - context_budget (int, optional): Token budget for context packing
        (default: 4000). Not yet wired — reported as "not_wired" if requested.
      - include_trace (bool, optional): Whether to include full trace detail
        (default: true).

    Returns per-channel results with health, latency, scores, fusion/ranking
    results, selected context, degraded/failed channel warnings, and
    lexical_only/vector_contributed flags.

    No mutation: no artifacts, wiki updates, corpus changes, or model
    synthesis are performed.
    """
    query = payload.get("query", "").strip()
    if not query:
        return {
            "status": "error",
            "message": "query is required",
            "query": "",
            "channel_results": {},
            "channel_health": {},
            "latency_ms": 0,
            "per_channel_latency_ms": {},
            "scores": {},
            "fusion_results": [],
            "selected_context": [],
            "degraded_channels": [],
            "failed_channels": [],
            "warnings": ["query is required"],
            "trace": None,
            "lexical_only": False,
            "vector_contributed": False,
        }

    selected_channels = payload.get("selected_channels", ["fts", "vector", "corpus"])
    if not isinstance(selected_channels, list):
        selected_channels = ["fts", "vector", "corpus"]
    # Validate channel names
    valid_channels = {"fts", "vector", "graph", "wiki", "procedural", "corpus"}
    selected_channels = [ch for ch in selected_channels if ch in valid_channels]
    if not selected_channels:
        selected_channels = ["fts", "vector", "corpus"]

    limit = payload.get("limit", 20)
    include_trace = payload.get("include_trace", True)

    # Access orchestration through container (layer discipline)
    search_sources_fn = getattr(container, "_search_sources_fn", None)
    AskStores = getattr(container, "_ask_stores_class", None)

    if search_sources_fn is None or AskStores is None:
        return {
            "status": "unavailable",
            "message": "Retrieval pipeline not configured — orchestration not wired",
            "query": query,
            "selected_channels": selected_channels,
            "channel_results": {},
            "channel_health": {ch: "not_configured" for ch in valid_channels},
            "latency_ms": 0,
            "per_channel_latency_ms": {},
            "scores": {},
            "fusion_results": [],
            "selected_context": [],
            "degraded_channels": [],
            "failed_channels": list(valid_channels),
            "warnings": ["Retrieval pipeline not available"],
            "trace": None,
            "lexical_only": False,
            "vector_contributed": False,
        }

    # Build AskStores from container's wired components
    stores = AskStores(
        artifact_store=container.artifact_store,
        lexical_store=container.lexical_store,
        vector_store=container.vector_store,
        event_store=container.event_store,
        project_store=container.project_store,
        ecs_store=container.ecs_store,
        embedding_provider=container.embedding_provider,
        corpus_turn_store=container.corpus_turn_store,
        graph_store=getattr(container, "graph_store", None),
    )

    # Execute retrieval only — no model dispatch.
    # Pass channel enable flags directly to _search_sources_with_trace
    # and disable auto_channel_selection so the user's selection is respected.
    import time as _time

    start_ms = _time.monotonic()

    try:
        sources, trace, _packed = await search_sources_fn(
            query=query,
            stores=stores,
            source_filter="all",
            max_sources=limit,
            enable_fts="fts" in selected_channels,
            enable_vector="vector" in selected_channels,
            enable_graph="graph" in selected_channels,
            enable_wiki="wiki" in selected_channels,
            enable_procedural="procedural" in selected_channels,
            auto_channel_selection=False,
        )
    except Exception as exc:
        logger.error("Retrieval test failed: %s", exc, exc_info=True)
        return {
            "status": "error",
            "message": f"Retrieval test error: {exc}",
            "query": query,
            "selected_channels": selected_channels,
            "channel_results": {},
            "channel_health": {ch: "failed" for ch in selected_channels},
            "latency_ms": round((_time.monotonic() - start_ms) * 1000, 2),
            "per_channel_latency_ms": {},
            "scores": {},
            "fusion_results": [],
            "selected_context": [],
            "degraded_channels": [],
            "failed_channels": list(selected_channels),
            "warnings": [f"Retrieval test error: {exc}"],
            "trace": None,
            "lexical_only": True,
            "vector_contributed": False,
        }

    total_latency_ms = round((_time.monotonic() - start_ms) * 1000, 2)

    # Build per-channel results from the trace
    channel_results: dict[str, dict] = {}
    channel_health: dict[str, str] = {}
    per_channel_latency_ms: dict[str, float] = {}
    degraded_channels: list[str] = []
    failed_channels: list[str] = []
    warnings: list[str] = []

    if trace is not None:
        # Extract channel health from trace
        ch_health = getattr(trace, "channel_health", {}) or {}
        ch_reasons = getattr(trace, "channel_health_reasons", {}) or {}
        ch_details = getattr(trace, "channel_details", {}) or {}
        ch_elapsed = getattr(trace, "per_channel_elapsed_ms", {}) or {}
        ch_contributions = getattr(trace, "channel_contributions", {}) or {}

        for ch_name in valid_channels:
            health_state = ch_health.get(ch_name, "not_configured")
            channel_health[ch_name] = health_state
            per_channel_latency_ms[ch_name] = round(ch_elapsed.get(ch_name, 0), 2)

            if health_state == "degraded":
                degraded_channels.append(ch_name)
                reason = ch_reasons.get(ch_name, "degraded quality")
                warnings.append(f"{ch_name} channel degraded: {reason}")
            elif health_state == "failed":
                failed_channels.append(ch_name)
                reason = ch_reasons.get(ch_name, "unknown error")
                warnings.append(f"{ch_name} channel failed: {reason}")
            elif health_state == "unavailable":
                failed_channels.append(ch_name)
                reason = ch_reasons.get(ch_name, "store not present")
                warnings.append(f"{ch_name} channel unavailable: {reason}")
            elif health_state == "not_configured":
                reason = ch_reasons.get(ch_name, "missing dependency")
                warnings.append(f"{ch_name} channel not configured: {reason}")
            elif health_state == "empty":
                reason = ch_reasons.get(ch_name, "")
                if reason:
                    warnings.append(f"{ch_name} channel returned no results: {reason}")

            # Build per-channel result detail
            detail = ch_details.get(ch_name)
            if detail is not None:
                channel_results[ch_name] = {
                    "channel": ch_name,
                    "state": health_state,
                    "result_count": getattr(detail, "result_count", 0),
                    "latency_ms": round(getattr(detail, "latency_ms", 0), 2),
                    "items": [],
                    "warning": getattr(detail, "degradation_reason", ""),
                    "error": getattr(detail, "error_summary", ""),
                    "backend_type": getattr(detail, "backend_type", ""),
                    "vss_available": getattr(detail, "vss_available", None),
                    "embedding_provider_configured": getattr(detail, "embedding_provider_configured", None),
                }
            else:
                channel_results[ch_name] = {
                    "channel": ch_name,
                    "state": health_state,
                    "result_count": 0,
                    "latency_ms": round(ch_elapsed.get(ch_name, 0), 2),
                    "items": [],
                    "warning": ch_reasons.get(ch_name, ""),
                    "error": "",
                }

        # Populate items from sources grouped by channel
        for source in sources:
            src_channel = getattr(source, "source_type", "") or ""
            # Map source_type to channel name
            ch_map = {
                "ingested": "fts",
                "fts": "fts",
                "lexical": "fts",
                "vector": "vector",
                "semantic": "vector",
                "corpus": "corpus",
                "graph": "graph",
                "wiki": "wiki",
                "procedural": "procedural",
                "artifact": "wiki",
            }
            mapped_ch = ch_map.get(src_channel, src_channel)
            if mapped_ch in channel_results:
                item = {
                    "id": getattr(source, "source_id", ""),
                    "title": getattr(source, "title", ""),
                    "snippet": getattr(source, "content_snippet", "")[:300],
                    "score": getattr(source, "score", 0),
                    "source_type": src_channel,
                    "source_id": getattr(source, "source_id", ""),
                    "domain": getattr(source, "domain", ""),
                    "metadata": getattr(source, "metadata", {}),
                }
                channel_results[mapped_ch]["items"].append(item)
                # Update result count from actual items
                channel_results[mapped_ch]["result_count"] = len(channel_results[mapped_ch]["items"])

    # Build fusion results (ranked context)
    fusion_results = [
        {
            "id": getattr(s, "source_id", ""),
            "title": getattr(s, "title", ""),
            "snippet": getattr(s, "content_snippet", "")[:300],
            "score": getattr(s, "score", 0),
            "source_type": getattr(s, "source_type", ""),
            "domain": getattr(s, "domain", ""),
        }
        for s in sources
    ]

    # Build scores summary
    scores: dict[str, Any] = {}
    if trace is not None:
        top_scores = getattr(trace, "top_scores", []) or []
        scores["top_rrf_scores"] = top_scores[:10]
        scores["hits_before_fusion"] = getattr(trace, "hits_before_fusion", 0)
        scores["hits_after_fusion"] = getattr(trace, "hits_after_fusion", 0)
        scores["hits_after_quality_gate"] = getattr(trace, "hits_after_quality_gate", 0)
        scores["verdict"] = getattr(trace, "verdict", "OK")

    # Build selected context (same as fusion results for now — no context packing in test mode)
    selected_context = fusion_results

    # Honesty flags
    lexical_only = getattr(trace, "lexical_only", False) if trace is not None else True
    vector_contributed = getattr(trace, "vector_contributed", False) if trace is not None else False

    # Build trace detail if requested
    trace_dict = None
    if include_trace and trace is not None and hasattr(trace, "to_diagnostic_dict"):
        trace_dict = trace.to_diagnostic_dict()

    result: dict[str, Any] = {
        "status": "ok",
        "query": query,
        "selected_channels": selected_channels,
        "channel_results": channel_results,
        "channel_health": channel_health,
        "latency_ms": total_latency_ms,
        "per_channel_latency_ms": per_channel_latency_ms,
        "scores": scores,
        "fusion_results": fusion_results,
        "selected_context": selected_context,
        "degraded_channels": degraded_channels,
        "failed_channels": failed_channels,
        "warnings": warnings,
        "trace": trace_dict,
        "lexical_only": lexical_only,
        "vector_contributed": vector_contributed,
    }

    return result


@router.get("/health")
async def retrieval_health(container: AipContainer = Depends(get_container)):
    """Return per-channel retrieval health and availability.

    Provides a snapshot of each retrieval channel's health state,
    including whether the backing store is available, vector backend
    type and degradation status, embedding provider configuration,
    and reasons for any unavailable/degraded channels.

    This endpoint is read-only and does not mutate any state.
    """
    # Determine per-channel health from container store availability
    channels: dict[str, dict[str, Any]] = {}

    # Lexical (FTS5) channel
    lexical_available = container.lexical_store is not None
    channels["lexical"] = {
        "channel": "fts",
        "state": "active" if lexical_available else "unavailable",
        "backend_type": "sqlite_fts5" if lexical_available else "none",
        "available": lexical_available,
        "degradation_reason": "" if lexical_available else "LexicalStore not initialized",
        "embedding_provider_configured": None,
        "vss_available": None,
    }

    # Vector channel
    vector_available = container.vector_store is not None
    embedding_configured = container.embedding_provider is not None
    vector_backend_type = "none"
    vector_degraded = False
    vss_available = None
    vector_count = None

    if vector_available and container.vector_store is not None:
        vs = container.vector_store
        # Get backend status if available
        if hasattr(vs, "get_backend_status"):
            try:
                from aip.foundation.schemas.vector import VectorBackendStatus as _VBS

                vbs = vs.get_backend_status()
                if vbs == _VBS.AVAILABLE:
                    vector_backend_type = getattr(vs, "_backend_name", "sqlite_vss")
                elif vbs == _VBS.DEGRADED_BRUTEFORCE:
                    vector_backend_type = "brute_force"
                    vector_degraded = True
                elif vbs == _VBS.DISABLED:
                    vector_backend_type = "disabled"
                    vector_available = False
                elif vbs == _VBS.FAILED:
                    vector_backend_type = "failed"
                    vector_available = False
            except Exception:
                vector_backend_type = "unknown"
        elif hasattr(vs, "_backend_name"):
            vector_backend_type = vs._backend_name

        if hasattr(vs, "_vss_available"):
            vss_available = vs._vss_available

        # Try to get vector count (sync approximation)
        if hasattr(vs, "count") and not vector_degraded:
            try:
                vector_count = 0  # async; would need await, report as unavailable
            except Exception:
                vector_count = None

    # Determine vector state
    if not vector_available:
        vector_state = "unavailable"
    elif vector_degraded:
        vector_state = "degraded"
    elif not embedding_configured:
        vector_state = "not_configured"
    else:
        vector_state = "active"

    channels["vector"] = {
        "channel": "vector",
        "state": vector_state,
        "backend_type": vector_backend_type,
        "available": vector_available,
        "degraded": vector_degraded,
        "degradation_reason": (
            "Vector search using brute-force fallback (no VSS index)"
            if vector_degraded
            else ("" if vector_available else "VectorStore not initialized")
        ),
        "embedding_provider_configured": embedding_configured,
        "vss_available": vss_available,
        "vector_count": vector_count,
    }

    # Graph channel
    graph_available = getattr(container, "graph_store", None) is not None
    channels["graph"] = {
        "channel": "graph",
        "state": "active" if graph_available else "unavailable",
        "backend_type": "sqlite_adjacency" if graph_available else "none",
        "available": graph_available,
        "degradation_reason": "" if graph_available else "GraphStore not initialized",
        "embedding_provider_configured": None,
        "vss_available": None,
    }

    # Wiki/CODEX channel
    wiki_available = container.artifact_store is not None and container.ecs_store is not None
    channels["wiki"] = {
        "channel": "wiki",
        "state": "active" if wiki_available else "unavailable",
        "backend_type": "artifact_store+ecs_store" if wiki_available else "none",
        "available": wiki_available,
        "degradation_reason": ("" if wiki_available else "ArtifactStore or EcsStore not initialized"),
        "embedding_provider_configured": None,
        "vss_available": None,
    }

    # Procedural channel
    procedural_available = container.artifact_store is not None
    channels["procedural"] = {
        "channel": "procedural",
        "state": "active" if procedural_available else "unavailable",
        "backend_type": "artifact_store" if procedural_available else "none",
        "available": procedural_available,
        "degradation_reason": "" if procedural_available else "ArtifactStore not initialized",
        "embedding_provider_configured": None,
        "vss_available": None,
    }

    # Corpus channel
    corpus_available = container.corpus_turn_store is not None
    channels["corpus"] = {
        "channel": "corpus",
        "state": "active" if corpus_available else "unavailable",
        "backend_type": "corpus_turn_store" if corpus_available else "none",
        "available": corpus_available,
        "degradation_reason": "" if corpus_available else "CorpusTurnStore not initialized",
        "embedding_provider_configured": None,
        "vss_available": None,
    }

    # Embedding coverage
    embedding_coverage: dict[str, Any] = {
        "status": "unavailable",
        "coverage_percent": 0.0,
        "total_turns": 0,
        "embedded_turns": 0,
    }
    if container.corpus_turn_store is not None:
        try:
            cts = container.corpus_turn_store
            if hasattr(cts, "get_corpus_status"):
                status = await cts.get_corpus_status()
                embedding_coverage = {
                    "status": "available",
                    "coverage_percent": status.get("embed_coverage", 0.0),
                    "total_turns": status.get("total_turns", 0),
                    "embedded_turns": status.get("embedded", 0),
                }
        except Exception:
            pass

    # Vector backend fallback chain
    vector_fallback_chain: list[str] = []
    if vector_available:
        vector_fallback_chain.append(vector_backend_type)
        if vector_degraded:
            vector_fallback_chain.append("brute_force → install sqlite-vss for production quality")

    # Summary counts
    active_count = sum(1 for ch in channels.values() if ch["state"] == "active")
    degraded_count = sum(1 for ch in channels.values() if ch["state"] == "degraded")
    unavailable_count = sum(1 for ch in channels.values() if ch["state"] in ("unavailable", "not_configured"))

    return {
        "status": "ok",
        "channels": channels,
        "embedding_coverage": embedding_coverage,
        "vector_fallback_chain": vector_fallback_chain,
        "summary": {
            "total_channels": len(channels),
            "active": active_count,
            "degraded": degraded_count,
            "unavailable": unavailable_count,
        },
    }


@router.get("/budget-tune")
async def retrieval_budget_tune(
    auto_apply: bool = Query(False, description="Whether to auto-apply suggested adjustments"),
    max_change_fraction: float = Query(
        0.30, ge=0.01, le=1.0, description="Maximum fractional change per channel per cycle"
    ),
    container: AipContainer = Depends(get_container),
):
    """Return adaptive per-channel budget tuning suggestions.

    Analyzes channel contribution data from recent traces
    and suggests per-channel budget adjustments.  Channels that
    consistently contribute few hits may have their budgets reduced,
    while high-value channels may receive budget increases.

    By default (``auto_apply=False``), this endpoint returns suggestions
    only and does not modify any configuration.  Set ``auto_apply=True``
    to apply the adjustments to a fresh ``OrchestratorConfig`` instance.

    **Note**: Even with ``auto_apply=True``, this endpoint only modifies
    a newly created config object returned in the response — it does not
    persist changes to any global state or database.

    Query Parameters:
        auto_apply: Whether to auto-apply suggested adjustments (default: False).
        max_change_fraction: Maximum fractional change per channel per cycle
            (default: 0.30, range: 0.01-1.0).

    Returns:
        JSON object with tuning results including:
        - ``adjustments``: List of per-channel budget adjustment suggestions
        - ``applied``: Whether adjustments were auto-applied
        - ``summary``: Human-readable summary of the tuning result
        - ``current_budgets``: Current per-channel budget configuration
    """
    # Chunk 6: Use container-mediated access instead of importlib
    # (adapter must not import orchestration, even via importlib in route modules)
    OrchestratorConfig = getattr(container, "_orchestrator_config_class", None)
    AdaptiveBudgetTuner = getattr(container, "_adaptive_budget_tuner_class", None)

    if OrchestratorConfig is None or AdaptiveBudgetTuner is None:
        return {
            "status": "unavailable",
            "message": "Retrieval budget tuning requires orchestration wiring",
            "adjustments": [],
            "applied": False,
            "summary": "Not configured",
            "channel_contributions": {},
            "total_queries": 0,
            "current_budgets": {},
        }

    # Compute channel contribution summary from recent traces
    channel_contributions = await _compute_channel_contribution_summary(container)

    # Count total queries from recent traces for min_samples check
    traces = await _get_recent_traces(container, limit=100)
    total_queries = len(traces)

    # Create OrchestratorConfig and AdaptiveBudgetTuner
    config = OrchestratorConfig()
    tuner = AdaptiveBudgetTuner(
        max_change_fraction=max_change_fraction,
        auto_apply=auto_apply,
    )

    # Run the tuner
    tuning_result = tuner.tune(
        config=config,
        channel_contributions=channel_contributions,
        total_queries=total_queries,
    )

    # Build response
    adjustments = []
    for adj in tuning_result.adjustments:
        adjustments.append(
            {
                "channel_name": adj.channel_name,
                "current_budget": adj.current_budget,
                "suggested_budget": adj.suggested_budget,
                "change": adj.suggested_budget - adj.current_budget,
                "reason": adj.reason,
                "confidence": adj.confidence,
            }
        )

    # Current budget snapshot (post-tuning if auto_apply)
    current_budgets = {
        "fts": config.fts_max_hits,
        "vector": config.vector_max_hits,
        "graph": config.graph_max_hits,
        "wiki": config.wiki_max_hits,
        "procedural": config.procedural_max_hits,
        "corpus": config.corpus_max_hits,
        "global_max_hits_per_channel": config.max_hits_per_channel,
    }

    return {
        "status": "ok",
        "adjustments": adjustments,
        "applied": tuning_result.applied,
        "summary": tuning_result.summary,
        "channel_contributions": channel_contributions,
        "total_queries": total_queries,
        "current_budgets": current_budgets,
    }


# ---------------------------------------------------------------------------
# Internal helpers for enhanced dashboard data
# ---------------------------------------------------------------------------


async def _get_recent_traces(container: AipContainer, limit: int = 10) -> list[dict]:
    """Retrieve the most recent retrieval traces from EventStore.

    Returns a list of trace dicts with query, timing, channel, and
    verdict information extracted from the event metadata.

    Added channel_contributions and llm_entity_extraction
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
            per_channel_ms = (
                json.loads(per_channel_ms_raw) if isinstance(per_channel_ms_raw, str) else per_channel_ms_raw
            )
        except (json.JSONDecodeError, TypeError):
            per_channel_ms = {}

        # Extract channel contributions
        channel_contributions_raw = metadata.get("retrieval_channel_contributions", "{}")
        try:
            channel_contributions = (
                json.loads(channel_contributions_raw)
                if isinstance(channel_contributions_raw, str)
                else channel_contributions_raw
            )
        except (json.JSONDecodeError, TypeError):
            channel_contributions = {}

        # Extract LLM entity extraction data
        llm_entity_extraction = {
            "ms": metadata.get("retrieval_llm_entity_extraction_ms", 0),
            "status": metadata.get("retrieval_llm_entity_extraction_status", "not_used"),
            "entity_count": metadata.get("retrieval_llm_entity_count", 0),
        }

        traces.append(
            {
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
            }
        )

        if len(traces) >= limit:
            break

    return traces


async def _get_top_failing_queries(container: AipContainer, limit: int = 5) -> list[dict]:
    """Return queries with the worst quality-gate outcomes.

    Prioritises NO_RESULTS and NEEDS_MORE_CONTEXT verdicts,
    sorted by total_elapsed_ms descending (slowest failures first).
    """
    traces = await _get_recent_traces(container, limit=100)
    failing = [t for t in traces if t.get("verdict") in ("NO_RESULTS", "NEEDS_MORE_CONTEXT")]
    # Sort: NO_RESULTS first, then by slowest elapsed time
    failing.sort(
        key=lambda t: (0 if t.get("verdict") == "NO_RESULTS" else 1, -t.get("total_elapsed_ms", 0)),
    )
    return failing[:limit]


async def _compute_channel_contribution_summary(container: AipContainer, limit: int = 100) -> dict[str, int]:
    """Compute aggregated channel contribution counts from recent traces.

    Aggregates the channel_contributions field across
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


async def _compute_llm_extraction_summary(container: AipContainer, limit: int = 100) -> dict[str, Any]:
    """Compute LLM entity extraction observability summary from recent traces.

    Aggregates LLM entity extraction timing and success
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

    Scans the default eval_results/ directory for the most
    recent timestamped eval JSON file and returns its contents.  Returns
    None if no eval results exist.
    """
    eval_dir = os.environ.get("AIP_EVAL_DIR", "eval_results")
    if not os.path.isdir(eval_dir):
        return None

    try:
        eval_files = [f for f in os.listdir(eval_dir) if f.startswith("eval_") and f.endswith(".json")]
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
