"""API performance metrics routes.

Adapter-layer. Admin-level (require DEFINER auth).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from aip.adapter.api.dependencies import get_container, require_definer

router = APIRouter(prefix="/performance", tags=["performance"])


@router.get("/metrics")
async def get_metrics(container=Depends(get_container), _=Depends(require_definer)):
    profiler = getattr(container, "performance_profiler", None)
    if profiler is None:
        return {"error": "Profiler not wired"}
    return await profiler.get_system_metrics()


@router.get("/slow")
async def get_slow(container=Depends(get_container), _=Depends(require_definer)):
    profiler = getattr(container, "performance_profiler", None)
    if profiler is None:
        return {"slow": []}
    return {"slow": await profiler.get_slow_operations()}


@router.get("/memory")
async def get_memory(container=Depends(get_container), _=Depends(require_definer)):
    profiler = getattr(container, "performance_profiler", None)
    if profiler is None:
        return {"memory": {}}
    return await profiler.get_memory_usage()
