"""Health endpoint (public, no AutonomyGate).

Phase 5 hardening:
- Computes real uptime from container._app_start_time
- Returns "degraded" when optional components are missing
- Includes budget status and component availability summary
- Checks database write connectivity for critical stores
"""

import logging
import time

from fastapi import APIRouter, Depends

from aip.adapter.api.dependencies import AipContainer, get_container

router = APIRouter()
logger = logging.getLogger(__name__)


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
    }
