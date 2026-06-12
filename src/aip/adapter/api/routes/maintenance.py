"""Maintenance Center routes — actor operations, maintenance jobs, and logs.

UI Cycle 12: Provides the Maintenance Center API surface for the Operator
Console. Exposes actor status, run-now, run history, maintenance job triggers,
and recent maintenance logs.

Architecture rules:
- Route modules MUST use container/protocol interfaces, never import
  from aip.orchestration directly.
- Maintenance actions are explicit DEFINER actions.
- No fake runs, no fake healthy states.
- Jobs that are not wired return unavailable/not_wired honestly.
- No secret exposure.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from aip.adapter.api.dependencies import AipContainer, get_container, require_definer

logger = logging.getLogger(__name__)

router = APIRouter()


# ------------------------------------------------------------------
# Actor status — enhanced for Maintenance Center
# ------------------------------------------------------------------


@router.get("/maintenance/status")
async def get_maintenance_status(container: AipContainer = Depends(get_container)):
    """Aggregated maintenance overview for the Maintenance Center.

    Combines actor states, backfill state, and capability availability
    into a single response. Honest about unavailable/not_wired states.
    """
    # Actor summaries
    actors: dict[str, Any] = {}
    for name, attr in [("beast", "beast"), ("vigil", "vigil"), ("sexton", "sexton_actor")]:
        actor_obj = getattr(container, attr, None)
        entry: dict[str, Any] = {
            "name": name,
            "initialized": actor_obj is not None,
            "scheduled": False,
            "running": False,
            "enabled": actor_obj is not None,
            "state": "not_configured" if actor_obj is None else "unknown",
            "last_run_at": None,
            "next_run_at": None,
            "last_result": None,
            "last_error": None,
            "degraded_reason": None,
            "run_now_supported": True,
        }
        if actor_obj is not None and hasattr(actor_obj, "get_status_summary"):
            try:
                summary = actor_obj.get_status_summary()
                entry["state"] = summary.get("state", "unknown")
                entry["last_run_at"] = summary.get("last_cycle_time")
                entry["last_result"] = summary.get("last_result")
                entry["last_error"] = summary.get("last_error")
                entry["degraded_reason"] = summary.get("degraded_reason")
                # Sprint 6.2 fields
                entry["cycle_count"] = summary.get("cycle_count", 0)
                entry["recent_errors"] = summary.get("recent_errors", [])
                entry["dependencies"] = summary.get("dependencies", {})
                entry["missing_core_dependencies"] = summary.get("missing_core_dependencies", [])
                # Interval / scheduling info
                interval = summary.get("interval_seconds")
                if interval is not None:
                    entry["interval_seconds"] = interval
                entry["role"] = summary.get("role", "")
            except Exception as exc:
                entry["health_error"] = str(exc)
        actors[name] = entry

    # Backfill state
    backfill_state = "not_configured"
    backfill_running = False
    sexton = getattr(container, "sexton_actor", None)
    if sexton is not None:
        try:
            summary = sexton.get_status_summary()
            backfill_state = summary.get("embedding_backfill_state", "unknown")
        except Exception:
            pass
    container_backfill = getattr(container, "backfill_status", {})
    backfill_running = container_backfill.get("running", False)

    # Capability availability
    capabilities: dict[str, Any] = {
        "embedding_backfill": {
            "available": getattr(container, "embedding_provider", None) is not None
            and getattr(container, "vector_store", None) is not None,
            "status": "available" if getattr(container, "embedding_provider", None) is not None else "not_wired",
        },
        "graph_rebuild": {
            "available": False,
            "status": "not_wired",
            "message": "Graph rebuild is run by Sexton's scheduled cycle. No standalone endpoint.",
        },
        "codex_rebuild": {
            "available": False,
            "status": "not_wired",
            "message": "CODEX/wiki rebuild is run by Sexton's scheduled cycle. No standalone endpoint.",
        },
        "retrieval_eval": {
            "available": False,
            "status": "not_wired",
            "message": "Retrieval eval is a CLI tool (aip eval retrieval). No API endpoint yet.",
        },
        "stale_docs_check": {
            "available": getattr(container, "corpus_turn_store", None) is not None,
            "status": "available" if getattr(container, "corpus_turn_store", None) is not None else "not_wired",
        },
        "contradiction_check": {
            "available": False,
            "status": "not_wired",
            "message": "Contradiction detection is not yet wired as a standalone job.",
        },
    }

    # Warnings from actor states
    warnings: list[str] = []
    for name, entry in actors.items():
        if entry.get("degraded_reason"):
            warnings.append(f"{name}: {entry['degraded_reason']}")
        if entry.get("last_error"):
            warnings.append(f"{name} last error: {entry['last_error']}")
        if entry.get("state") == "failed":
            warnings.append(f"{name} is in failed state")
        if not entry.get("initialized"):
            warnings.append(f"{name} is not initialized")

    return {
        "actors": actors,
        "backfill": {
            "state": backfill_state,
            "running": backfill_running,
            "progress": container_backfill.get("progress", {}),
            "last_result": container_backfill.get("last_result"),
        },
        "capabilities": capabilities,
        "warnings": warnings,
    }


# ------------------------------------------------------------------
# Actor run history — from event store
# ------------------------------------------------------------------


@router.get("/actors/{actor_name}/runs")
async def get_actor_runs(
    actor_name: str,
    limit: int = 20,
    container: AipContainer = Depends(get_container),
):
    """Get recent run history for an actor from the event store.

    Queries the event store for events by this actor. Returns honest
    empty list if event store is not available or no events exist.
    Does not fake run history.
    """
    # Validate actor name
    valid_actors = {"beast", "vigil", "sexton"}
    if actor_name not in valid_actors:
        raise HTTPException(status_code=404, detail=f"Unknown actor: {actor_name}")

    event_store = getattr(container, "event_store", None)
    if event_store is None:
        return {
            "actor": actor_name,
            "runs": [],
            "available": False,
            "message": "Event store not available",
        }

    try:
        # Query for actor-specific event types
        # Beast: beast_health_check, beast_heartbeat, beast_context_advisory
        # Vigil: vigil_eval_start, vigil_eval_complete
        # Sexton: sexton_vigil_start, sexton_vigil_complete
        events = await event_store.query(
            actor=actor_name,
            limit=limit,
        )
        runs = []
        for evt in events:
            runs.append(
                {
                    "event_type": evt.event_type,
                    "actor": evt.actor,
                    "artifact_id": evt.artifact_id,
                    "from_state": evt.from_state,
                    "to_state": evt.to_state,
                    "timestamp": evt.timestamp,
                    "metadata": evt.metadata if hasattr(evt, "metadata") else {},
                }
            )
        return {
            "actor": actor_name,
            "runs": runs,
            "available": True,
            "count": len(runs),
        }
    except Exception as exc:
        logger.warning("Event store query for actor %s failed: %s", actor_name, exc)
        return {
            "actor": actor_name,
            "runs": [],
            "available": False,
            "message": f"Event store query failed: {exc}",
        }


# ------------------------------------------------------------------
# Maintenance logs — recent maintenance-related events
# ------------------------------------------------------------------


@router.get("/maintenance/logs")
async def get_maintenance_logs(
    limit: int = 50,
    container: AipContainer = Depends(get_container),
):
    """Get recent maintenance-related events from the event store.

    Returns events from Beast, Vigil, and Sexton actors. Honest empty
    state when event store is unavailable or no events exist. Does not
    fake maintenance logs.
    """
    event_store = getattr(container, "event_store", None)
    if event_store is None:
        return {
            "logs": [],
            "available": False,
            "message": "Event store not available",
        }

    try:
        # Get recent events — query all actors
        # The event store's query method supports actor filtering
        all_events: list[dict[str, Any]] = []
        for actor_name in ["beast", "vigil", "sexton"]:
            events = await event_store.query(
                actor=actor_name,
                limit=limit,
            )
            for evt in events:
                all_events.append(
                    {
                        "event_type": evt.event_type,
                        "actor": evt.actor,
                        "artifact_id": evt.artifact_id,
                        "from_state": evt.from_state,
                        "to_state": evt.to_state,
                        "timestamp": evt.timestamp,
                        "metadata": evt.metadata if hasattr(evt, "metadata") else {},
                    }
                )

        # Sort by timestamp descending
        all_events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

        # Apply limit
        logs = all_events[:limit]

        return {
            "logs": logs,
            "available": True,
            "count": len(logs),
        }
    except Exception as exc:
        logger.warning("Maintenance logs query failed: %s", exc)
        return {
            "logs": [],
            "available": False,
            "message": f"Event store query failed: {exc}",
        }


# ------------------------------------------------------------------
# Maintenance job endpoints — explicit DEFINER actions
# ------------------------------------------------------------------


@router.post("/maintenance/backfill-embeddings")
async def maintenance_backfill_embeddings(
    payload: dict | None = None,
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Trigger embedding backfill. Explicit DEFINER action.

    Uses the same runtime path as POST /corpus/backfill (Cycle 10)
    and POST /admin/embeddings/backfill. Reports honest unavailable
    if the pipeline is not wired.
    """
    embedding_provider = getattr(container, "embedding_provider", None)
    if embedding_provider is None:
        return {
            "status": "not_wired",
            "message": "Embedding provider not configured. Configure an embedding model slot first.",
        }

    vector_store = getattr(container, "vector_store", None)
    if vector_store is None:
        return {
            "status": "not_wired",
            "message": "Vector store not configured. Embedding backfill requires a vector store.",
        }

    # Delegate to the existing corpus backfill endpoint logic
    body = payload or {}
    limit = body.get("limit", 500)
    batch_size = body.get("batch_size", 20)
    dry_run = body.get("dry_run", False)
    domain = body.get("domain")

    backfill_status = getattr(container, "backfill_status", {})
    if backfill_status.get("running"):
        return {
            "status": "already_running",
            "message": "Backfill is already in progress. Poll GET /api/v1/maintenance/status for progress.",
        }

    try:
        from aip.adapter.api.routes.admin import BackfillRequest, _run_backfill_in_background

        request = BackfillRequest(
            domain=domain,
            limit=limit,
            batch_size=batch_size,
            dry_run=dry_run,
        )

        if not backfill_status:
            container.backfill_status = {
                "running": False,
                "scanned": 0,
                "embedded": 0,
                "failed": 0,
                "skipped": 0,
                "total_estimated": 0,
                "last_result": None,
            }

        container.backfill_status["running"] = True
        container.backfill_status["scanned"] = 0
        container.backfill_status["embedded"] = 0
        container.backfill_status["failed"] = 0
        container.backfill_status["skipped"] = 0

        asyncio.create_task(_run_backfill_in_background(request, container))

        return {
            "status": "accepted",
            "message": "Embedding backfill started. Poll GET /api/v1/maintenance/status for progress.",
            "limit": limit,
            "batch_size": batch_size,
            "dry_run": dry_run,
        }
    except ImportError:
        return {
            "status": "not_wired",
            "message": (
                "Backfill pipeline not wired. Use POST /api/v1/corpus/backfill "
                "or POST /api/v1/admin/embeddings/backfill instead."
            ),
        }
    except Exception as exc:
        logger.warning("Maintenance backfill trigger failed: %s", exc)
        return {"status": "error", "message": f"Backfill failed to start: {exc}"}


@router.post("/maintenance/rebuild-graph")
async def maintenance_rebuild_graph(
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Trigger graph rebuild. Explicit DEFINER action.

    Graph extraction is currently run by Sexton's scheduled cycle
    (_run_graph_extraction). There is no standalone runtime path for
    graph rebuild. Returns not_wired honestly.
    """
    sexton = getattr(container, "sexton_actor", None)
    graph_store = getattr(container, "graph_store", None)

    if sexton is None:
        return {
            "status": "not_wired",
            "message": "Sexton actor not initialized. Graph rebuild requires Sexton.",
        }

    if graph_store is None:
        return {
            "status": "not_wired",
            "message": "Graph store not configured. Configure a graph store first.",
        }

    # Sexton has _run_graph_extraction but calling it directly is
    # not the same as triggering a standalone rebuild. The actor's
    # run_cycle() includes graph extraction as one of five operations.
    # Report scheduled_only honestly — the user can trigger Sexton's
    # full cycle via POST /actors/sexton/trigger instead.
    return {
        "status": "scheduled_only",
        "message": (
            "Graph rebuild runs as part of Sexton's scheduled cycle. "
            "Use POST /api/v1/actors/sexton/trigger to run a full Sexton cycle, "
            "which includes graph extraction. A standalone graph rebuild endpoint "
            "is not yet available."
        ),
        "alternative": "POST /api/v1/actors/sexton/trigger",
    }


@router.post("/maintenance/rebuild-codex")
async def maintenance_rebuild_codex(
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Trigger CODEX/wiki rebuild. Explicit DEFINER action.

    Wiki generation is currently run by Sexton's scheduled cycle
    (_run_wiki_generation). There is no standalone runtime path for
    CODEX rebuild. Returns not_wired honestly.
    """
    sexton = getattr(container, "sexton_actor", None)

    if sexton is None:
        return {
            "status": "not_wired",
            "message": "Sexton actor not initialized. CODEX rebuild requires Sexton.",
        }

    # Sexton has _run_wiki_generation but calling it directly is
    # not the same as triggering a standalone rebuild. Report
    # scheduled_only honestly.
    return {
        "status": "scheduled_only",
        "message": (
            "CODEX/wiki rebuild runs as part of Sexton's scheduled cycle. "
            "Use POST /api/v1/actors/sexton/trigger to run a full Sexton cycle, "
            "which includes wiki generation. A standalone CODEX rebuild endpoint "
            "is not yet available."
        ),
        "alternative": "POST /api/v1/actors/sexton/trigger",
    }


@router.post("/maintenance/run-retrieval-eval")
async def maintenance_run_retrieval_eval(
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Trigger retrieval evaluation. Explicit DEFINER action.

    Retrieval evaluation is currently a CLI tool (aip eval retrieval)
    that runs offline. There is no API endpoint for running retrieval
    evaluation. Returns not_wired honestly.
    """
    return {
        "status": "not_wired",
        "message": (
            "Retrieval evaluation is currently a CLI-only tool. "
            "Run 'aip eval retrieval' from the command line. "
            "An API endpoint for retrieval evaluation is not yet available."
        ),
        "alternative": "CLI: aip eval retrieval",
    }


@router.post("/maintenance/check-stale-docs")
async def maintenance_check_stale_docs(
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Check for stale documents. Explicit DEFINER action.

    Delegates to the existing corpus stale documents logic
    (GET /corpus/stale) which uses CorpusTurnStore.
    Returns honest unavailable if CorpusTurnStore is not wired.
    """
    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return {
            "status": "not_wired",
            "message": "CorpusTurnStore not wired. Cannot check for stale documents.",
            "stale_count": 0,
            "stale_docs": [],
        }

    try:
        problems = await cts.get_corpus_problems()
        stale = problems.get("stale_docs", [])
        return {
            "status": "completed",
            "message": f"Found {len(stale)} stale document(s).",
            "stale_count": len(stale),
            "stale_docs": stale,
        }
    except Exception as exc:
        logger.warning("Stale docs check failed: %s", exc)
        return {
            "status": "error",
            "message": f"Stale docs check failed: {exc}",
            "stale_count": 0,
            "stale_docs": [],
        }


@router.post("/maintenance/check-contradictions")
async def maintenance_check_contradictions(
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Check for contradictions in the knowledge base. Explicit DEFINER action.

    Contradiction detection is not yet wired as a standalone job.
    The wiki endpoint GET /wiki/contradictions provides read-only
    contradiction visibility. Returns not_wired honestly.
    """
    return {
        "status": "not_wired",
        "message": (
            "Contradiction detection is not yet available as a standalone "
            "maintenance job. Use GET /api/v1/wiki/contradictions for "
            "read-only contradiction visibility."
        ),
        "alternative": "GET /api/v1/wiki/contradictions",
    }
