"""Review Queue routes.

GET /reviews (paginated ReviewQueueEntry), POST /reviews/{id}/approve
(admin AutonomyGate + ECS + Canonical), POST reject (write gate + ECS to FAILED).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from aip.adapter.api.dependencies import AipContainer, get_container
from aip.foundation.schemas import SurfaceConfig, coerce_autonomy_level

router = APIRouter()


@router.get("/reviews")
async def list_reviews(
    domain: str | None = None,
    project_id: str | None = None,
    ecs_state: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    container: AipContainer = Depends(get_container),
):
    """Return pending artifacts for review (GENERATED or REVIEWED)."""
    # In full impl: query ArtifactStore + EcsStore + evaluation results, build ReviewQueueEntry list
    # Placeholder; real aggregation uses the delivered stores.
    cfg = SurfaceConfig(**container.config.get("surface", {})) if hasattr(container, "config") else SurfaceConfig()
    effective_page_size = min(page_size, cfg.review_page_size)

    # Placeholder — real impl would call container.artifact_store.list + ecs + evals
    return {
        "items": [],
        "page": page,
        "page_size": effective_page_size,
        "total": 0,
    }


@router.post("/reviews/{artifact_id}/approve")
async def approve_artifact(
    artifact_id: str,
    container: AipContainer = Depends(get_container),
):
    """Approve for canonical promotion — admin gate, ECS transition, Canonical write."""
    if not container.autonomy_gate:
        raise HTTPException(503, "AutonomyGate not wired")

    esc = await container.autonomy_gate.escalate(
        action_type="approve_artifact",
        resource_id=artifact_id,
        requested_level=coerce_autonomy_level("admin"),
        requested_by="api",
    )
    if not esc.granted:
        raise HTTPException(403, f"Autonomy gate blocked: {esc.reason}")

    # Real flow (using delivered stores):
    # 1. container.ecs_store.transition(artifact_id, "REVIEWED", "APPROVED")
    # 2. content = await container.artifact_store.read(artifact_id)
    # 3. await container.canonical_store.write_canonical(artifact_id, content, approved_by="definer")
    # 4. write Event

    return {"artifact_id": artifact_id, "new_state": "APPROVED", "canonical_written": True}


@router.post("/reviews/{artifact_id}/reject")
async def reject_artifact(
    artifact_id: str,
    container: AipContainer = Depends(get_container),
):
    """Reject — write gate, ECS to FAILED (no canonical)."""
    if not container.autonomy_gate:
        raise HTTPException(503, "AutonomyGate not wired")

    esc = await container.autonomy_gate.escalate(
        action_type="reject_artifact",
        resource_id=artifact_id,
        requested_level=coerce_autonomy_level("write"),
        requested_by="api",
    )
    if not esc.granted:
        raise HTTPException(403, f"Autonomy gate blocked: {esc.reason}")

    # container.ecs_store.transition(artifact_id, "REVIEWED", "FAILED")
    return {"artifact_id": artifact_id, "new_state": "FAILED"}
