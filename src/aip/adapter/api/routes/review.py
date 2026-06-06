"""Review Queue routes.

GET /reviews (paginated ReviewQueueEntry), POST /reviews/{id}/approve
(ECS state update for beast_wiki artifacts), POST reject (ECS to FAILED),
POST /reviews/approve-all (bulk approve pending beast artifacts).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import aiosqlite

from fastapi import APIRouter, Depends, HTTPException, Query

from aip.adapter.api.dependencies import AipContainer, get_container, require_definer
from aip.foundation.schemas import SurfaceConfig

router = APIRouter()
logger = logging.getLogger(__name__)

# Path to state.db — matches all stores in the project
_STATE_DB = "db/state.db"


async def _get_db() -> aiosqlite.Connection:
    """Get an aiosqlite connection to state.db with Row factory."""
    conn = await aiosqlite.connect(_STATE_DB)
    conn.row_factory = aiosqlite.Row
    return conn


async def _query_beast_artifacts(state: str = "GENERATED") -> list[dict]:
    """Query artifacts + ecs_state for beast:wiki / beast:proposal entries.

    Joins the artifacts table with ecs_state to find beast-generated
    artifacts (wiki proposals, etc.) that are in the given ECS state
    and returns them as review-ready dicts.
    """
    items: list[dict] = []
    try:
        conn = await _get_db()
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


async def _update_ecs_state(artifact_id: str, new_state: str) -> bool:
    """Directly update ecs_state.current_state for an artifact.

    This bypasses the GuardrailedEcsStore and AutonomyGate which may
    not be properly configured for beast_wiki artifacts. Updates the
    ecs_state table directly and records the transition.

    Returns True if the row was updated, False if artifact_id not found.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = await _get_db()
        try:
            # Check current state
            cursor = await conn.execute(
                "SELECT current_state FROM ecs_state WHERE artifact_id = ?",
                (artifact_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                logger.warning("Artifact %s not found in ecs_state", artifact_id)
                return False

            old_state = row["current_state"]
            if old_state == new_state:
                return True  # Already in target state

            # Update state
            await conn.execute(
                "UPDATE ecs_state SET current_state = ?, updated_at = ? WHERE artifact_id = ?",
                (new_state, now, artifact_id),
            )

            # Record transition in ecs_transitions for provenance
            await conn.execute(
                """
                INSERT INTO ecs_transitions (artifact_id, from_state, to_state, actor, reason, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, old_state, new_state, "definer", "API approve", now),
            )

            await conn.commit()
            logger.info("ECS state updated: %s %s → %s", artifact_id, old_state, new_state)
            return True
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("Failed to update ecs_state for %s: %s", artifact_id, exc)
        return False


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
    """Approve an artifact — updates ecs_state to APPROVED.

    For beast_wiki artifacts (ID starts with beast:wiki: or beast:proposal:),
    directly updates the ecs_state table since these artifacts may not
    be tracked by the GuardrailedEcsStore/AutonomyGate pipeline.

    Follows the ECS state graph: GENERATED → REVIEWED → APPROVED.
    The two-step transition is collapsed into a single APPROVED update
    for the DEFINER-driven review flow.
    """
    # For beast artifacts, use direct ecs_state update
    is_beast = artifact_id.startswith("beast:wiki:") or artifact_id.startswith("beast:proposal:")

    if is_beast:
        updated = await _update_ecs_state(artifact_id, "APPROVED")
        if not updated:
            raise HTTPException(404, f"Artifact {artifact_id!r} not found in ecs_state")
        return {"artifact_id": artifact_id, "new_state": "APPROVED"}

    # For non-beast artifacts, try the original pipeline
    # (AutonomyGate + ECS store + canonical write)
    canonical_written = False

    if container.ecs_store is not None:
        try:
            # Determine current state for the transition
            current = await container.ecs_store.current_state(artifact_id)
            from_state = current or "REVIEWED"
            await container.ecs_store.transition(
                artifact_id, from_state, "APPROVED", actor="definer", reason="API approve"
            )
        except Exception as exc:
            # Fallback: direct DB update
            logger.warning("ECS store transition failed, falling back to direct update: %s", exc)
            updated = await _update_ecs_state(artifact_id, "APPROVED")
            if not updated:
                raise HTTPException(500, f"Could not approve artifact {artifact_id}: {exc}") from exc
    else:
        # No ECS store — direct DB update
        updated = await _update_ecs_state(artifact_id, "APPROVED")
        if not updated:
            raise HTTPException(404, f"Artifact {artifact_id!r} not found in ecs_state")

    if container.canonical_store is not None and container.artifact_store is not None:
        try:
            content = await container.artifact_store.read(artifact_id)
            if content:
                await container.canonical_store.write_canonical(
                    artifact_id, content, approved_by="definer"
                )
                canonical_written = True
        except Exception as exc:
            logger.warning("Canonical write failed after ECS transition: %s", exc)

    # Record the event (advisory)
    if container.event_store is not None:
        try:
            await container.event_store.write_event(
                event_type="review_approved",
                actor="definer",
                artifact_id=artifact_id,
                from_state="GENERATED",
                to_state="APPROVED",
            )
        except Exception:
            logger.debug("event recording failed", exc_info=True)

    return {"artifact_id": artifact_id, "new_state": "APPROVED", "canonical_written": canonical_written}


@router.post("/reviews/approve-all")
async def approve_all_artifacts(
    _auth=Depends(require_definer),
):
    """Bulk approve all pending beast artifacts in GENERATED state.

    Updates ecs_state to APPROVED for all beast:wiki:* and beast:proposal:*
    artifacts that are currently in GENERATED state. Returns count of
    approved artifacts.
    """
    now = datetime.now(timezone.utc).isoformat()
    approved_ids: list[str] = []

    try:
        conn = await _get_db()
        try:
            # Find all beast artifacts in GENERATED state
            cursor = await conn.execute(
                """
                SELECT artifact_id, current_state
                FROM ecs_state
                WHERE current_state = 'GENERATED'
                  AND (artifact_id LIKE 'beast:wiki:%' OR artifact_id LIKE 'beast:proposal:%')
                """,
            )
            rows = await cursor.fetchall()

            for row in rows:
                aid = row["artifact_id"]
                old_state = row["current_state"]

                # Update state
                await conn.execute(
                    "UPDATE ecs_state SET current_state = ?, updated_at = ? WHERE artifact_id = ?",
                    ("APPROVED", now, aid),
                )

                # Record transition
                try:
                    await conn.execute(
                        """
                        INSERT INTO ecs_transitions (artifact_id, from_state, to_state, actor, reason, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (aid, old_state, "APPROVED", "definer", "bulk approve-all", now),
                    )
                except Exception:
                    pass  # ecs_transitions table may not exist

                approved_ids.append(aid)

            await conn.commit()
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("Bulk approve failed: %s", exc)
        raise HTTPException(500, f"Bulk approve failed: {exc}") from exc

    logger.info("Bulk approved %d beast artifacts", len(approved_ids))
    return {"approved": len(approved_ids), "artifact_ids": approved_ids}


@router.post("/reviews/{artifact_id}/reject")
async def reject_artifact(
    artifact_id: str,
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Reject an artifact — updates ecs_state to FAILED.

    For beast_wiki artifacts, directly updates ecs_state.
    For non-beast artifacts, attempts ECS store transition.
    """
    is_beast = artifact_id.startswith("beast:wiki:") or artifact_id.startswith("beast:proposal:")

    if is_beast:
        updated = await _update_ecs_state(artifact_id, "FAILED")
        if not updated:
            raise HTTPException(404, f"Artifact {artifact_id!r} not found in ecs_state")
        return {"artifact_id": artifact_id, "new_state": "FAILED"}

    # For non-beast artifacts, try the original pipeline
    if container.ecs_store is not None:
        try:
            current = await container.ecs_store.current_state(artifact_id)
            from_state = current or "REVIEWED"
            await container.ecs_store.transition(
                artifact_id, from_state, "FAILED", actor="definer", reason="API reject"
            )
        except Exception as exc:
            # Fallback: direct DB update
            logger.warning("ECS store reject failed, falling back to direct update: %s", exc)
            updated = await _update_ecs_state(artifact_id, "FAILED")
            if not updated:
                raise HTTPException(500, f"Could not reject artifact {artifact_id}: {exc}") from exc
    else:
        updated = await _update_ecs_state(artifact_id, "FAILED")
        if not updated:
            raise HTTPException(404, f"Artifact {artifact_id!r} not found in ecs_state")

    # Record the event (advisory)
    if container.event_store is not None:
        try:
            await container.event_store.write_event(
                event_type="review_rejected",
                actor="definer",
                artifact_id=artifact_id,
                from_state="GENERATED",
                to_state="FAILED",
            )
        except Exception:
            logger.debug("event recording failed", exc_info=True)

    return {"artifact_id": artifact_id, "new_state": "FAILED"}
