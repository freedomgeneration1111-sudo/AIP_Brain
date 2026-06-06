"""Review Queue routes.

GET /reviews (paginated ReviewQueueEntry), POST /reviews/{id}/approve
(admin AutonomyGate + ECS + Canonical), POST reject (write gate + ECS to FAILED).
"""

from __future__ import annotations

import json
import logging

import aiosqlite

from fastapi import APIRouter, Depends, HTTPException, Query

from aip.adapter.api.dependencies import AipContainer, get_container, require_definer
from aip.foundation.schemas import SurfaceConfig, coerce_autonomy_level

router = APIRouter()
logger = logging.getLogger(__name__)

# Path to state.db — matches all stores in the project
_STATE_DB = "db/state.db"


async def _query_beast_artifacts(state: str = "GENERATED") -> list[dict]:
    """Query artifacts + ecs_state for beast:wiki / beast:proposal entries.

    Joins the artifacts table with ecs_state to find beast-generated
    artifacts (wiki proposals, etc.) that are in the given ECS state
    and returns them as review-ready dicts.
    """
    items: list[dict] = []
    try:
        conn = await aiosqlite.connect(_STATE_DB)
        conn.row_factory = aiosqlite.Row
        try:
            cursor = await conn.execute(
                """
                SELECT a.id, a.version, a.content, a.metadata_json, a.created_at,
                       e.current_state, e.updated_at
                FROM artifacts a
                INNER JOIN ecs_state e ON a.id = e.artifact_id
                WHERE e.current_state = ?
                  AND (a.id LIKE 'beast:wiki:%' OR a.id LIKE 'beast:proposal:%')
                ORDER BY e.updated_at DESC
                """,
                (state,),
            )
            rows = await cursor.fetchall()
            for row in rows:
                metadata = {}
                raw_meta = row["metadata_json"]
                if raw_meta:
                    try:
                        metadata = json.loads(raw_meta)
                    except (json.JSONDecodeError, TypeError):
                        pass
                items.append({
                    "artifact_id": row["id"],
                    "id": row["id"],
                    "artifact_version": row["version"],
                    "ecs_state": row["current_state"],
                    "domain": metadata.get("domain", ""),
                    "content": row["content"],
                    "metadata": metadata,
                    "artifact_type": "wiki" if row["id"].startswith("beast:wiki:") else "proposal",
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                })
        finally:
            await conn.close()
    except Exception:
        logger.warning("Failed to query beast artifacts from artifacts+ecs_state", exc_info=True)
    return items


@router.get("/reviews")
async def list_reviews(
    domain: str | None = None,
    project_id: str | None = None,
    ecs_state: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    container: AipContainer = Depends(get_container),
):
    """Return pending artifacts for review (GENERATED or REVIEWED).

    Merges items from two sources:
    1. ReviewQueueStore — explicitly enqueued review items
    2. Beast artifacts — beast:wiki:* and beast:proposal:* entries from
       artifacts table joined with ecs_state where current_state='GENERATED'
    """
    cfg = SurfaceConfig(**container.config.get("surface", {})) if hasattr(container, "config") else SurfaceConfig()
    effective_page_size = min(page_size, cfg.review_page_size)

    all_items: list[dict] = []
    seen_ids: set[str] = set()

    # Source 1: ReviewQueueStore — explicitly enqueued pending items
    if container.review_queue_store is not None:
        try:
            pending = await container.review_queue_store.list_pending(limit=effective_page_size)
            for item in pending:
                aid = item.get("artifact_id") or item.get("id", "")
                if aid not in seen_ids:
                    seen_ids.add(aid)
                    all_items.append(item)
        except Exception:
            logger.warning("list_pending failed", exc_info=True)

    # Source 2: Beast artifacts in GENERATED state (artifacts + ecs_state)
    beast_items = await _query_beast_artifacts(state="GENERATED")
    for item in beast_items:
        aid = item.get("artifact_id") or item.get("id", "")
        if aid not in seen_ids:
            seen_ids.add(aid)
            all_items.append(item)

    # Apply pagination
    start = (page - 1) * effective_page_size
    page_items = all_items[start : start + effective_page_size]

    return {"items": page_items, "page": page, "page_size": effective_page_size, "total": len(all_items)}


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
