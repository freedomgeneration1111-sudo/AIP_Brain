"""Health endpoint (public, no AutonomyGate).

Computes real uptime from container._app_start_time, returns "degraded"
when optional components are missing, includes budget status and
component availability summary, and checks database write connectivity
for critical stores.

Sprint 5.25 additions:
- Alerting status section (if AlertManager is wired)
- Per-batch graph extraction telemetry (from Sexton)
- Config watcher status (if ConfigWatcher is wired)
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
    actors: dict[str, dict[str, Any]]
    budget_status: str
    db_writable: bool
    store_health: dict[str, dict[str, Any]]
    read_pool_summary: dict[str, Any]
    # Sprint 6.2: Enhanced actor status, embedding coverage, store sizes
    actor_details: dict[str, dict[str, Any]]
    embedding_coverage: dict[str, Any]
    store_sizes: dict[str, Any]
    alerting_health: dict[str, Any]


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

    # Sprint 6.2: Detailed actor status from get_status_summary()
    actor_details: dict[str, dict[str, Any]] = {}
    for actor_name, actor_instance in [
        ("beast", container.beast),
        ("vigil", container.vigil),
        ("sexton", container.sexton_actor),
    ]:
        if actor_instance is not None and hasattr(actor_instance, "get_status_summary"):
            try:
                actor_details[actor_name] = actor_instance.get_status_summary()
            except Exception:
                actor_details[actor_name] = {"initialized": True, "summary_error": True}
        elif actor_instance is not None:
            actor_details[actor_name] = {"initialized": True}
        else:
            actor_details[actor_name] = {"initialized": False}

    # Sprint 6.2: Embedding coverage from corpus turn store
    embedding_coverage: dict[str, Any] = {
        "percentage": 0.0,
        "total": 0,
        "embedded": 0,
        "unembedded": 0,
        "sexton_pass_state": None,
    }
    cts = getattr(container, "corpus_turn_store", None)
    if cts is not None:
        try:
            total = await cts.total_turns()
            unembedded = await cts.count_unembedded()
            embedded = total - unembedded
            embedding_coverage["total"] = total
            embedding_coverage["embedded"] = embedded
            embedding_coverage["unembedded"] = unembedded
            embedding_coverage["percentage"] = round(embedded / total * 100, 2) if total > 0 else 0.0
        except Exception:
            pass
    if container.sexton_actor is not None:
        try:
            embedding_coverage["sexton_pass_state"] = dict(container.sexton_actor._embedding_pass_state)
        except Exception:
            pass

    # Sprint 6.2: Store sizes — vector count and graph node/edge counts
    store_sizes: dict[str, Any] = {
        "vector_store": {"available": False, "count": 0},
        "graph_store": {"available": False, "nodes": 0, "edges": 0},
    }
    if container.vector_store is not None and hasattr(container.vector_store, "count"):
        try:
            store_sizes["vector_store"]["available"] = True
            store_sizes["vector_store"]["count"] = await container.vector_store.count()
        except Exception:
            pass
    graph_store = getattr(container, "graph_store", None)
    if graph_store is not None and hasattr(graph_store, "node_count"):
        try:
            store_sizes["graph_store"]["available"] = True
            store_sizes["graph_store"]["nodes"] = await graph_store.node_count()
            store_sizes["graph_store"]["edges"] = await graph_store.edge_count()
        except Exception:
            pass

    # Sprint 6.2: High-level alerting health from StatusAggregator
    alerting_health: dict[str, Any] = {"enabled": False}
    alert_manager = getattr(container, "_alert_manager", None)
    if alert_manager is not None and hasattr(alert_manager, "get_status"):
        try:
            full_status = alert_manager.get_status()
            # Extract only the high-level health indicators, not the full detail
            alerting_health = {
                "enabled": full_status.get("enabled", False),
                "circuit_breaker_open": full_status.get("circuit_breaker", {}).get("active", False),
                "recent_alert_count": full_status.get("total_alerts_sent", 0),
                "delivery_failures": full_status.get("total_send_failures", 0),
                "realtime_subscribers": full_status.get("sse_subscribers", 0) + full_status.get("ws_subscribers", 0),
                "lifecycle_active_count": full_status.get("alert_groups", 0),
            }
        except Exception:
            pass

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

    # Read pool auto-sizer (Sprint 5.23 → Sprint 5.24 auto-apply)
    auto_sizer = getattr(container, "_read_pool_auto_sizer", None)
    if auto_sizer is None:
        from aip.adapter.read_pool import ReadPoolAutoSizer
        auto_sizer = ReadPoolAutoSizer()
        container._read_pool_auto_sizer = auto_sizer  # type: ignore[attr-defined]

    # Build a mapping of store_name -> store instance for auto-apply
    pool_stores: dict[str, Any] = {}
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
            # Collect the actual store instance for auto-apply
            store_instance = getattr(container, store_name, None)
            if store_instance is not None and hasattr(store_instance, "_read_pool_size"):
                pool_stores[store_name] = store_instance
            # Observe for auto-sizing (Sprint 5.24: pass store for auto-apply)
            try:
                auto_sizer.observe(store_name, pool_data, store=pool_stores.get(store_name))
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

    # Sprint 5.24: Auto-tuning status — dedicated section for operator visibility
    # into all auto-tuning features: read pool, batch size, Vigil LLM evaluation.
    auto_tuning_status: dict[str, Any] = {
        "read_pool_auto_sizing": auto_sizer.get_status(),
        "graph_batch_auto_tune": {
            "enabled": False,
            "current_batch_size": 0,
            "configured_batch_size": 0,
            "recent_adjustments": [],
        },
        "vigil_llm_evaluation": {
            "enabled": False,
            "model_slot": "evaluation",
            "recent_faithfulness_scores": [],
            "total_evaluations": 0,
            "total_hallucinations": 0,
        },
    }

    # Graph batch auto-tune status from Sexton actor
    if container.sexton_actor is not None:
        try:
            sexton_config = getattr(container.sexton_actor, "_config", None)
            if sexton_config is not None:
                auto_tuning_status["graph_batch_auto_tune"]["enabled"] = (
                    sexton_config.graph_extraction_batch_auto_tune_enabled
                )
                auto_tuning_status["graph_batch_auto_tune"]["configured_batch_size"] = (
                    sexton_config.graph_extraction_batch_size
                )
            auto_tuning_status["graph_batch_auto_tune"]["current_batch_size"] = (
                getattr(container.sexton_actor, "_current_batch_size", 0)
            )
            adjustments = getattr(container.sexton_actor, "_auto_tune_adjustments", [])
            auto_tuning_status["graph_batch_auto_tune"]["recent_adjustments"] = (
                adjustments[-5:] if adjustments else []
            )
        except Exception:
            pass

    # Vigil LLM evaluation status
    if container.vigil is not None:
        try:
            vigil_config = getattr(container.vigil, "config", None)
            if vigil_config is not None:
                auto_tuning_status["vigil_llm_evaluation"]["enabled"] = (
                    vigil_config.llm_faithfulness_enabled
                )
                auto_tuning_status["vigil_llm_evaluation"]["model_slot"] = (
                    vigil_config.llm_faithfulness_model_slot
                )
            vigil_telem = getattr(container.vigil, "_llm_faithfulness_telemetry", {})
            auto_tuning_status["vigil_llm_evaluation"]["total_evaluations"] = (
                vigil_telem.get("total_llm_evaluations", 0)
            )
            auto_tuning_status["vigil_llm_evaluation"]["total_hallucinations"] = (
                vigil_telem.get("total_hallucinations_detected", 0)
            )
            auto_tuning_status["vigil_llm_evaluation"]["avg_faithfulness_score"] = (
                vigil_telem.get("avg_llm_faithfulness_score", 0.0)
            )
            auto_tuning_status["vigil_llm_evaluation"]["recent_faithfulness_scores"] = (
                [e.get("faithfulness_score", 0.0) for e in vigil_telem.get("last_llm_evaluations", [])[-5:]]
            )
            # Sprint 5.24: Vigil cycle report history
            cycle_history = getattr(container.vigil, "_cycle_report_history", [])
            auto_tuning_status["vigil_llm_evaluation"]["cycle_report_count"] = len(cycle_history)
            if cycle_history:
                latest = cycle_history[-1]
                auto_tuning_status["vigil_llm_evaluation"]["latest_cycle"] = {
                    "avg_citation_rate": latest.get("avg_citation_rate", 0.0),
                    "avg_grounding_rate": latest.get("avg_grounding_rate", 0.0),
                    "avg_llm_faithfulness": latest.get("avg_llm_faithfulness", 0.0),
                    "evaluated_count": latest.get("evaluated_count", 0),
                    "flagged_count": latest.get("flagged_count", 0),
                }
        except Exception:
            pass

    # Sprint 5.25: Per-batch graph extraction telemetry
    per_batch_telemetry: dict[str, Any] = {
        "total_batch_successes": 0,
        "total_batch_failures": 0,
        "recent_batches": [],
        "failure_rate": 0.0,
    }
    if container.sexton_actor is not None:
        try:
            per_batch_telemetry["total_batch_successes"] = getattr(
                container.sexton_actor, "_total_batch_successes", 0
            )
            per_batch_telemetry["total_batch_failures"] = getattr(
                container.sexton_actor, "_total_batch_failures", 0
            )
            per_batch_telemetry["recent_batches"] = getattr(
                container.sexton_actor, "_per_batch_telemetry", []
            )[-10:]
            total = per_batch_telemetry["total_batch_successes"] + per_batch_telemetry["total_batch_failures"]
            per_batch_telemetry["failure_rate"] = (
                round(per_batch_telemetry["total_batch_failures"] / total, 3) if total > 0 else 0.0
            )
        except Exception:
            pass

    # Sprint 5.25: Alerting status (full detail — retained for backward compat)
    alerting_status: dict[str, Any] = {"enabled": False}
    if alert_manager is not None and hasattr(alert_manager, "get_status"):
        try:
            alerting_status = alert_manager.get_status()
        except Exception:
            pass

    # Sprint 5.25: Config watcher status
    config_watcher_status: dict[str, Any] = {"enabled": False}
    config_watcher = getattr(container, "_config_watcher", None)
    if config_watcher is not None and hasattr(config_watcher, "get_status"):
        try:
            config_watcher_status = config_watcher.get_status()
            # Also trigger a check while we're here (health endpoint is a
            # natural polling point for config file changes)
            config_watcher.check_and_reload()
        except Exception:
            pass

    # Sprint 5.47: Cleanup metrics from alert manager
    cleanup_metrics: dict[str, Any] = {}
    if alert_manager is not None and hasattr(alert_manager, "get_cleanup_metrics"):
        try:
            cleanup_metrics = alert_manager.get_cleanup_metrics()
        except Exception:
            pass

    # Sprint 5.50: Calibration drift and snapshot GC status
    calibration_drift_status: dict[str, Any] = {}
    if alert_manager is not None and hasattr(alert_manager, "get_calibration_drift_status"):
        try:
            calibration_drift_status = alert_manager.get_calibration_drift_status()
        except Exception:
            pass

    snapshot_gc_status: dict[str, Any] = {}
    if alert_manager is not None and hasattr(alert_manager, "get_snapshot_gc_status"):
        try:
            snapshot_gc_status = alert_manager.get_snapshot_gc_status()
        except Exception:
            pass

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
        "auto_tuning_status": auto_tuning_status,
        "per_batch_telemetry": per_batch_telemetry,
        "alerting_status": alerting_status,
        "config_watcher_status": config_watcher_status,
        "cleanup_metrics": cleanup_metrics,
        "calibration_drift": calibration_drift_status,
        "snapshot_gc": snapshot_gc_status,
        # Sprint 6.2: Enhanced observability fields
        "actor_details": actor_details,
        "embedding_coverage": embedding_coverage,
        "store_sizes": store_sizes,
        "alerting_health": alerting_health,
    }


@router.get("/health/datastore")
async def datastore_health(container: AipContainer = Depends(get_container)):
    """Datastore truth endpoint — where every store lives and its status.

    Returns the honest multi-file local datastore summary: which DB files
    exist, which stores share files, file sizes, and the backup story.
    This endpoint satisfies the Chunk 4 dogfood gate: startup validation
    can print exactly where each store lives, and a backup/export story
    exists for all of them.
    """
    return container.datastore_summary()
