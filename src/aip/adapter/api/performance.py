"""API performance metrics routes.

Adapter-layer. Admin-level (require DEFINER auth).

Behavior:
- If profiler is configured and initialized, returns real profiler data.
- If profiler is not configured, returns structured BACKEND_UNAVAILABLE response.
- Never returns fake metrics or empty pretend-success.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from aip.adapter.api.dependencies import get_container, require_definer

router = APIRouter(prefix="/performance", tags=["performance"])

# Structured response contract for unavailable/disabled backend
_UNAVAILABLE_RESPONSE = {
    "ok": False,
    "error": {
        "code": "BACKEND_UNAVAILABLE",
        "message": "PerformanceProfiler is not configured or not initialized. "
        "Set profiling_enabled=true in config and ensure the profiler is wired in the app lifespan.",
        "details": {},
    },
}

_DISABLED_RESPONSE = {
    "ok": False,
    "error": {
        "code": "DISABLED",
        "message": "Performance profiling is disabled in the current configuration. "
        "Set profiling_enabled=true in config to enable.",
        "details": {},
    },
}


@router.get("/metrics")
async def get_metrics(container=Depends(get_container), _=Depends(require_definer)):
    profiler = getattr(container, "performance_profiler", None)
    if profiler is None:
        return _UNAVAILABLE_RESPONSE
    # Check if profiling is enabled in config
    if hasattr(profiler, "config") and hasattr(profiler.config, "profiling_enabled"):
        if not profiler.config.profiling_enabled:
            return _DISABLED_RESPONSE
    metrics = await profiler.get_system_metrics()
    return {"ok": True, "data": metrics}


@router.get("/slow")
async def get_slow(container=Depends(get_container), _=Depends(require_definer)):
    profiler = getattr(container, "performance_profiler", None)
    if profiler is None:
        return _UNAVAILABLE_RESPONSE
    if hasattr(profiler, "config") and hasattr(profiler.config, "profiling_enabled"):
        if not profiler.config.profiling_enabled:
            return _DISABLED_RESPONSE
    slow_ops = await profiler.get_slow_operations()
    return {"ok": True, "data": {"slow": slow_ops}}


@router.get("/memory")
async def get_memory(container=Depends(get_container), _=Depends(require_definer)):
    profiler = getattr(container, "performance_profiler", None)
    if profiler is None:
        return _UNAVAILABLE_RESPONSE
    if hasattr(profiler, "config") and hasattr(profiler.config, "profiling_enabled"):
        if not profiler.config.profiling_enabled:
            return _DISABLED_RESPONSE
    memory = await profiler.get_memory_usage()
    return {"ok": True, "data": memory}
