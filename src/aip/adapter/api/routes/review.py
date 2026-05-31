"""Review Queue routes.

GET /reviews (paginated ReviewQueueEntry), POST /reviews/{id}/approve
(admin AutonomyGate + ECS + Canonical), POST reject (write gate + ECS to FAILED).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from aip.adapter.api.dependencies import AipContainer, get_container, require_definer
from aip.foundation.schemas import SurfaceConfig, coerce_autonomy_level

router = APIRouter()
logger = logging.getLogger(__name__)


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

    # Use ReviewQueueStore when available for real pending items
    if container.review_queue_store is not None:
        try:
            items = await container.review_queue_store.list_pending(limit=effective_page_size)
            return {"items": items, "page": page, "page_size": effective_page_size, "total": len(items)}
        except Exception:
            logger.warning("list_pending failed", exc_info=True)
            pass  # Fall through to placeholder

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
    _auth=Depends(require_definer),
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

    # Real flow: ECS transition + canonical write
    canonical_written = False
    if container.ecs_store is not None:
        try:
            await container.ecs_store.transition(
                artifact_id, "REVIEWED", "APPROVED", actor="definer", reason="API approve"
            )
        except Exception as exc:
            raise HTTPException(500, f"ECS transition failed: {exc}")

    if container.canonical_store is not None and container.artifact_store is not None:
        try:
            content = await container.artifact_store.read(artifact_id)
            if content:
                await container.canonical_store.write_canonical(
                    artifact_id, content, approved_by="definer"
                )
                canonical_written = True
        except Exception as exc:
            # ECS state already transitioned; canonical write failure is logged but non-fatal
            logger.warning("Canonical write failed after ECS transition: %s", exc)

    # Record the event
    if container.event_store is not None:
        try:
            await container.event_store.write_event(
                event_type="review_approved",
                actor="definer",
                artifact_id=artifact_id,
                from_state="REVIEWED",
                to_state="APPROVED",
            )
        except Exception:
            logger.debug("event recording failed", exc_info=True)
            pass  # Event recording is advisory

    return {"artifact_id": artifact_id, "new_state": "APPROVED", "canonical_written": canonical_written}


@router.post("/reviews/{artifact_id}/reject")
async def reject_artifact(
    artifact_id: str,
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
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

    # ECS transition to FAILED
    if container.ecs_store is not None:
        try:
            await container.ecs_store.transition(
                artifact_id, "REVIEWED", "FAILED", actor="definer", reason="API reject"
            )
        except Exception as exc:
            raise HTTPException(500, f"ECS transition failed: {exc}")

    # Record the event
    if container.event_store is not None:
        try:
            await container.event_store.write_event(
                event_type="review_rejected",
                actor="definer",
                artifact_id=artifact_id,
                from_state="REVIEWED",
                to_state="FAILED",
            )
        except Exception:
            logger.debug("event recording failed", exc_info=True)
            pass  # Event recording is advisory

    return {"artifact_id": artifact_id, "new_state": "FAILED"}
