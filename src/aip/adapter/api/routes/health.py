"""Health endpoint (public, no AutonomyGate).

Phase 2 enhancement: includes real uptime, model provider status,
and actor initialization status.
"""

import time

from fastapi import APIRouter, Depends

from aip.adapter.api.dependencies import AipContainer, get_container

router = APIRouter()


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
            model_slots = []

    # Actor status (lightweight — just initialized yes/no)
    actors_status = {
        "beast": {"initialized": container.beast is not None},
        "vigil": {"initialized": container.vigil is not None},
        "sexton": {"initialized": container.sexton is not None},
    }

    return {
        "status": "ok",
        "vector_backend": "placeholder" if container.vector_store is None else "configured",
        "model_slots": model_slots,
        "ci_mode": ci_mode,
        "actors": actors_status,
        "uptime_seconds": uptime_seconds,
    }
