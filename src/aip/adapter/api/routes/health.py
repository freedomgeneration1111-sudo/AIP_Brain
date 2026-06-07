"""Health endpoint (public, no AutonomyGate).

Computes real uptime from container._app_start_time, returns "degraded"
when optional components are missing, includes budget status and
component availability summary, and checks database write connectivity
for critical stores.
"""

import logging
import time
from typing import Any, TypedDict

from fastapi import APIRouter, Depends

from aip.adapter.api.dependencies import AipContainer, get_container
from aip.adapter.store_health import ConnectionHealth

router = APIRouter()
logger = logging.getLogger(__name__)


class HealthEndpointResponse(TypedDict):
    """Structured type for the /health endpoint response."""

    status: str
    uptime_seconds: int
    ci_mode: bool
    critical_components: bool
    optional_components: dict[str, bool]
    optional_available: int
    optional_total: int
    vector_backend: str
    model_slots: list[Any]
    actors: dict[str, dict[str, bool]]
    budget_status: str
    db_writable: bool
    store_health: dict[str, dict[str, Any]]
    read_pool_summary: dict[str, Any]


@router.get("/health")
async def health(container: AipContainer = Depends(get_container)):
    """Public health check. Returns system status, model slots, actors, and uptime."""
    # Calculate uptime
    start_time = getattr(container, "_app_start_time", None)
    uptime_seconds = 0
    if start_time:
        uptime_seconds = int(time.time() - start_time)

    # Get model provider info
    model_slots = []
    ci_mode = True
    if container.model_provider is not None:
        try:
            model_slots = container.model_provider.list_slots()
            ci_mode = getattr(container.model_provider, "_ci_mode", True)
        except Exception:
            logger.warning("Health check: model provider list_slots failed", exc_info=True)
            model_slots = []

    # Actor status (lightweight — just initialized yes/no)
    actors_status = {
        "beast": {"initialized": container.beast is not None},
        "vigil": {"initialized": container.vigil is not None},
        "sexton": {"initialized": container.sexton_actor is not None},
    }

    # Sexton batch telemetry (if available)
    sexton_batch_telemetry = {}
    if container.sexton_actor is not None and hasattr(container.sexton_actor, "_batch_telemetry"):
        try:
            sexton_batch_telemetry = dict(container.sexton_actor._batch_telemetry)
        except Exception:
            sexton_batch_telemetry = {}

    # Vigil LLM faithfulness telemetry (Sprint 5.23)
    vigil_llm_telemetry = {}
    if container.vigil is not None and hasattr(container.vigil, "_llm_faithfulness_telemetry"):
        try:
            vigil_llm_telemetry = dict(container.vigil._llm_faithfulness_telemetry)
        except Exception:
            vigil_llm_telemetry = {}

    # Component availability — determine degraded status
    critical_available = all([
        container.entity_store is not None,
        container.canonical_store is not None,
        container.event_store is not None,
        container.autonomy_gate is not None,
        container.artifact_store is not None,
    ])

    optional_components = {
        "lexical_store": container.lexical_store is not None,
        "vector_store": container.vector_store is not None,
        "embedding_provider": container.embedding_provider is not None,
        "project_store": container.project_store is not None,
        "budget_store": container.budget_store is not None,
        "budget_manager": container.budget_manager is not None,
        "vigil_store": container.vigil_store is not None,
        "model_provider": container.model_provider is not None,
        "knowledge_store": container.knowledge_store is not None,
        "session_store": container.session_store is not None,
        "ecs_store": container.ecs_store is not None,
        "review_queue_store": container.review_queue_store is not None,
        "trace_store": container.trace_store is not None,
        "graph_store": getattr(container, "graph_store", None) is not None,
    }
    optional_count = sum(optional_components.values())
    optional_total = len(optional_components)

    # Compute overall status
    if not critical_available:
        status = "unhealthy"
    elif optional_count < optional_total:
        status = "degraded"
    else:
        status = "ok"

    # Budget status summary — actually verify the budget manager is responsive
    budget_status = "unconfigured"
    if container.budget_manager is not None:
        try:
            status_result = await container.budget_manager.get_status(
                scope="session", scope_id="health_check",
            )
            budget_status = "active" if status_result else "error"
        except Exception:
            logger.warning("Health check: budget manager get_status failed", exc_info=True)
            budget_status = "error"

    # DB write check — try a lightweight write to event_store
    db_writable = False
    if container.event_store is not None:
        try:
            await container.event_store.write_event(
                event_type="health_check",
                actor="health_endpoint",
                artifact_id="",
                from_state=None,
                to_state=None,
            )
            db_writable = True
        except Exception:
            logger.warning("Health check: DB write verification failed", exc_info=True)
            db_writable = False

    # Connection health metrics — gather from stores that support StoreHealthMixin
    store_health = {}
    for store_name in (
        "entity_store", "event_store", "artifact_store", "ecs_store",
        "canonical_store", "budget_store", "project_store", "session_store",
        "review_queue_store", "vigil_store", "corpus_turn_store", "lexical_store",
        "graph_store", "vector_store", "knowledge_store", "autonomy_gate",
        "auth_session_store",
    ):
        store = getattr(container, store_name, None)
        if store is not None and hasattr(store, "connection_health"):
            try:
                store_health[store_name] = store.connection_health()
            except Exception:
                store_health[store_name] = {"error": "health_check_failed"}

    # Aggregate read pool summary across all pool-enabled stores
    read_pool_summary: dict[str, Any] = {
        "pool_stores": [],
        "total_checkouts": 0,
        "total_fallbacks": 0,
        "total_exhaustions": 0,
        "aggregate_exhaustion_rate": 0.0,
        "stores_with_high_exhaustion": [],
        "recommendation": "",
        "auto_size_suggestions": [],  # Sprint 5.23: auto-sizing suggestions
    }

    # Read pool auto-sizer (Sprint 5.23)
    auto_sizer = getattr(container, "_read_pool_auto_sizer", None)
    if auto_sizer is None:
        from aip.adapter.read_pool import ReadPoolAutoSizer
        auto_sizer = ReadPoolAutoSizer()
        container._read_pool_auto_sizer = auto_sizer  # type: ignore[attr-defined]
    for store_name, health_data in store_health.items():
        pool_data = health_data.get("read_pool")
        if isinstance(pool_data, dict):
            read_pool_summary["pool_stores"].append(store_name)
            read_pool_summary["total_checkouts"] += pool_data.get("checkout_count", 0)
            read_pool_summary["total_fallbacks"] += pool_data.get("fallback_count", 0)
            read_pool_summary["total_exhaustions"] += pool_data.get("exhaustion_count", 0)
            # Flag stores with high exhaustion rate (>0.3 suggests pool too small)
            rate = pool_data.get("exhaustion_rate", 0.0)
            if rate > 0.3:
                read_pool_summary["stores_with_high_exhaustion"].append({
                    "store": store_name,
                    "exhaustion_rate": rate,
                    "pool_size": pool_data.get("pool_size", 0),
                })
            # Observe for auto-sizing (Sprint 5.23)
            try:
                auto_sizer.observe(store_name, pool_data)
            except Exception:
                pass
    total_co = read_pool_summary["total_checkouts"]
    read_pool_summary["aggregate_exhaustion_rate"] = (
        round(read_pool_summary["total_exhaustions"] / total_co, 4) if total_co > 0 else 0.0
    )

    # Generate top-level recommendation when pool exhaustion is high
    high_exhaustion_stores = read_pool_summary["stores_with_high_exhaustion"]
    if high_exhaustion_stores:
        store_names = [s["store"] for s in high_exhaustion_stores]
        if len(high_exhaustion_stores) == 1:
            s = high_exhaustion_stores[0]
            if s["exhaustion_rate"] > 0.6:
                read_pool_summary["recommendation"] = (
                    f"Critical: {s['store']} exhaustion_rate={s['exhaustion_rate']:.2%}. "
                    f"Double pool_size from {s['pool_size']} to {s['pool_size'] * 2} and investigate read patterns."
                )
            else:
                read_pool_summary["recommendation"] = (
                    f"High: {s['store']} exhaustion_rate={s['exhaustion_rate']:.2%}. "
                    f"Consider increasing pool_size from {s['pool_size']} to {s['pool_size'] + 2}."
                )
        else:
            critical = [s for s in high_exhaustion_stores if s["exhaustion_rate"] > 0.6]
            if critical:
                read_pool_summary["recommendation"] = (
                    f"Critical: {len(critical)} store(s) ({', '.join(s['store'] for s in critical)}) "
                    f"have exhaustion_rate > 60%. Double their pool_size values and investigate. "
                    f"Also check: {', '.join(store_names)}."
                )
            else:
                read_pool_summary["recommendation"] = (
                    f"High: {len(high_exhaustion_stores)} store(s) ({', '.join(store_names)}) "
                    f"have exhaustion_rate > 30%. Consider increasing pool_size in [read_pool] config."
                )

    # Add auto-sizing suggestions to read_pool_summary (Sprint 5.23)
    try:
        read_pool_summary["auto_size_suggestions"] = auto_sizer.get_suggestions()
    except Exception:
        read_pool_summary["auto_size_suggestions"] = []

    # Batch telemetry summary (Sprint 5.23) — dedicated section for operator visibility
    batch_telemetry_summary: dict[str, Any] = {
        "batch_extractions": sexton_batch_telemetry.get("total_batch_extractions", 0),
        "per_turn_extractions": sexton_batch_telemetry.get("total_per_turn_extractions", 0),
        "turns_via_batch": sexton_batch_telemetry.get("total_turns_via_batch", 0),
        "turns_via_per_turn": sexton_batch_telemetry.get("total_turns_via_per_turn", 0),
        "estimated_tokens_saved": sexton_batch_telemetry.get("total_estimated_tokens_saved", 0),
        "batch_efficiency_ratio": 0.0,
        "summary": "",
    }
    total_extractions = (
        batch_telemetry_summary["batch_extractions"]
        + batch_telemetry_summary["per_turn_extractions"]
    )
    if total_extractions > 0:
        batch_telemetry_summary["batch_efficiency_ratio"] = round(
            batch_telemetry_summary["batch_extractions"] / total_extractions, 3
        )
        batch_telemetry_summary["summary"] = (
            f"Batch mode: {batch_telemetry_summary['batch_extractions']} calls "
            f"({batch_telemetry_summary['turns_via_batch']} turns), "
            f"Per-turn mode: {batch_telemetry_summary['per_turn_extractions']} calls "
            f"({batch_telemetry_summary['turns_via_per_turn']} turns). "
            f"Estimated tokens saved: {batch_telemetry_summary['estimated_tokens_saved']:,}. "
            f"Batch efficiency: {batch_telemetry_summary['batch_efficiency_ratio']:.1%}."
        )
    else:
        batch_telemetry_summary["summary"] = "No batch telemetry data yet."

    return {
        "status": status,
        "uptime_seconds": uptime_seconds,
        "ci_mode": ci_mode,
        "critical_components": critical_available,
        "optional_components": optional_components,
        "optional_available": optional_count,
        "optional_total": optional_total,
        "vector_backend": "placeholder" if container.vector_store is None else "configured",
        "model_slots": model_slots,
        "actors": actors_status,
        "budget_status": budget_status,
        "db_writable": db_writable,
        "store_health": store_health,
        "read_pool_summary": read_pool_summary,
        "sexton_batch_telemetry": sexton_batch_telemetry,
        "batch_telemetry_summary": batch_telemetry_summary,
        "vigil_llm_telemetry": vigil_llm_telemetry,
    }
