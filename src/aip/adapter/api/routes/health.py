"""Health endpoint (public, no AutonomyGate)."""

from fastapi import APIRouter, Depends

from aip.adapter.api.dependencies import AipContainer, get_container

router = APIRouter()


@router.get("/health")
async def health(container: AipContainer = Depends(get_container)):
    """Public health check. Returns vector backend, model slots, uptime."""
    # In real wiring the container would have the real stores; returning placeholder shape.
    return {
        "status": "ok",
        "vector_backend": "placeholder",
        "model_slots": ["synthesis", "evaluation", "sexton", "embedding"],
        "uptime_seconds": 0,
    }
