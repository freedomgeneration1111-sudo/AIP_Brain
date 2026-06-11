"""Artifact Workbench routes — full lifecycle management.

Provides the API surface for the Artifact Workbench UI:
  - List artifacts with filtering by state, type, source, search query
  - Get artifact detail (content, metadata, state, sources, reviews, ledger)
  - Get artifact sources (provenance)
  - Get artifact reviews (review history/ledger)
  - Approve artifact (explicit DEFINER action)
  - Reject artifact (explicit DEFINER action)
  - Mark artifact as needs-revision (explicit DEFINER action)
  - Export artifact (only from APPROVED state; force-export with audit)
  - Get artifact dashboard summary

Architecture:
  - Routes use container-stored references, never import orchestration directly.
  - Every review action requires require_definer auth dependency.
  - No auto-approve, no auto-export, no silent state changes.
  - Force-export is visibly exceptional with mandatory audit trail.
  - Honest empty/unavailable states — never fake data.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from aip.adapter.api.dependencies import AipContainer, get_container, require_definer
from aip.foundation.ecs_graph import ALL_STATES, InvalidTransitionError, validate_transition
from aip.foundation.schemas import SurfaceConfig

router = APIRouter()
logger = logging.getLogger(__name__)

# Valid ECS states for filtering
VALID_FILTER_STATES = ALL_STATES | {"NEEDS_REVISION"}
# NEEDS_REVISION is a verdict/event, not an ECS state, but it is a valid filter
# because the UI groups artifacts with that verdict under a "Needs Revision" tab.


# ── Request/Response Models ──────────────────────────────────────────────


class ForceExportRequest(BaseModel):
    """Request body for force-export — requires explicit confirmation and reason."""

    force: bool = Field(True, description="Must be explicitly True — this is a sovereign override")
    reason: str = Field(
        ...,
        min_length=1,
        description="Mandatory reason for bypassing the APPROVED gate",
    )


class NeedsRevisionRequest(BaseModel):
    """Request body for needs-revision — carries revision instruction."""

    instruction: str = Field(
        default="",
        description="Revision instruction for the artifact",
    )


class RejectRequest(BaseModel):
    """Request body for reject — carries optional note."""

    note: str = Field(
        default="",
        description="Rejection note / reason",
    )


# ── Helper: resolve DB path ──────────────────────────────────────────────

_STATE_DB: str | None = None


def _resolve_state_db(container: AipContainer) -> str:
    """Resolve state.db path from container config."""
    global _STATE_DB
    if _STATE_DB is not None:
        return _STATE_DB
    cfg = getattr(container, "config", {}) or {}
    return (
        cfg.get("database", {}).get("db_path")
        or cfg.get("db_path")
        or "db/state.db"
    )


def _set_state_db(path: str | None) -> None:
    """Override the state DB path (for tests or explicit config)."""
    global _STATE_DB
    _STATE_DB = path


# ── Helper: build artifact list item from stores ────────────────────────


async def _build_artifact_list_item(
    artifact_id: str,
    container: AipContainer,
) -> dict[str, Any] | None:
    """Build a single artifact list item from artifact_store + ecs_store.

    Returns None if the artifact has no content or metadata.
    """
    if container.artifact_store is None:
        return None

    try:
        metadata = await container.artifact_store.read_metadata(artifact_id)
    except (KeyError, Exception):
        return None

    if metadata is None:
        return None

    # Get ECS state
    ecs_state = "UNKNOWN"
    if container.ecs_store is not None:
        try:
            ecs_state = await container.ecs_store.current_state(artifact_id) or "UNKNOWN"
        except Exception:
            ecs_state = "UNKNOWN"

    # Check for NEEDS_REVISION verdict
    has_needs_revision = False
    if container.event_store is not None:
        try:
            events = await container.event_store.query(
                artifact_id=artifact_id,
                event_type="review_verdict",
                limit=50,
            )
            for ev in events:
                meta = ev.metadata if hasattr(ev, "metadata") else {}
                if isinstance(meta, dict) and meta.get("verdict") == "NEEDS_REVISION":
                    has_needs_revision = True
                    break
        except Exception:
            pass

    # Check for export events
    has_export = False
    if container.event_store is not None:
        try:
            export_events = await container.event_store.query(
                artifact_id=artifact_id,
                event_type="artifact_exported",
                limit=1,
            )
            if export_events:
                has_export = True
        except Exception:
            pass

    source_ids = metadata.get("source_ids", [])

    return {
        "artifact_id": artifact_id,
        "title": metadata.get("title", metadata.get("prompt", artifact_id))[:120],
        "ecs_state": ecs_state,
        "has_needs_revision": has_needs_revision,
        "has_export": has_export,
        "artifact_type": metadata.get("artifact_type", ""),
        "domain": metadata.get("domain", ""),
        "project": metadata.get("project_name", metadata.get("project_id", "")),
        "model_slot": metadata.get("model_slot", ""),
        "model_name": metadata.get("model_name", ""),
        "source_count": len(source_ids),
        "created_at": metadata.get("generated_at", metadata.get("created_at", "")),
        "updated_at": metadata.get("updated_at", ""),
    }


# ── GET /artifacts — list with filtering ────────────────────────────────


@router.get("/artifacts")
async def list_artifacts(
    ecs_state: str | None = None,
    artifact_type: str | None = None,
    created_by: str | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    container: AipContainer = Depends(get_container),
):
    """List artifacts with optional filtering by state, type, source, search.

    When ecs_state=NEEDS_REVISION is requested, returns artifacts that have
    a NEEDS_REVISION review verdict event (regardless of their actual ECS state,
    which is typically GENERATED).

    When ecs_state=EXPORTED is requested, returns artifacts that have
    an artifact_exported event.

    Returns honest empty list if stores are unavailable.
    """
    cfg = SurfaceConfig(**container.config.get("surface", {})) if hasattr(container, "config") else SurfaceConfig()
    effective_page_size = min(page_size, cfg.artifact_page_size)

    # Collect artifact IDs based on filter
    artifact_ids: list[str] = []

    if ecs_state == "NEEDS_REVISION":
        # Special case: NEEDS_REVISION is a verdict, not an ECS state.
        # Find all artifacts with a NEEDS_REVISION review_verdict event.
        if container.event_store is not None and container.ecs_store is not None:
            try:
                # Get all GENERATED artifacts and check for NEEDS_REVISION verdict
                generated_ids = await container.ecs_store.list_by_state("GENERATED")
                for aid in generated_ids:
                    events = await container.event_store.query(
                        artifact_id=aid,
                        event_type="review_verdict",
                        limit=50,
                    )
                    for ev in events:
                        meta = ev.metadata if hasattr(ev, "metadata") else {}
                        if isinstance(meta, dict) and meta.get("verdict") == "NEEDS_REVISION":
                            artifact_ids.append(aid)
                            break
            except Exception:
                logger.warning("Failed to query NEEDS_REVISION artifacts", exc_info=True)
    elif ecs_state == "EXPORTED":
        # Special case: EXPORTED is an event, not an ECS state.
        # Find artifacts with artifact_exported events.
        if container.event_store is not None and container.ecs_store is not None:
            try:
                # Get all APPROVED artifacts and check for export events
                approved_ids = await container.ecs_store.list_by_state("APPROVED")
                for aid in approved_ids:
                    export_events = await container.event_store.query(
                        artifact_id=aid,
                        event_type="artifact_exported",
                        limit=1,
                    )
                    if export_events:
                        artifact_ids.append(aid)
            except Exception:
                logger.warning("Failed to query EXPORTED artifacts", exc_info=True)
    elif ecs_state is not None and ecs_state in ALL_STATES:
        # Standard ECS state filter
        if container.ecs_store is not None:
            try:
                artifact_ids = await container.ecs_store.list_by_state(ecs_state)
            except Exception:
                logger.warning("Failed to list artifacts by state %s", ecs_state, exc_info=True)
    else:
        # No state filter — return all artifacts
        if container.ecs_store is not None:
            try:
                for state in ALL_STATES:
                    ids = await container.ecs_store.list_by_state(state)
                    artifact_ids.extend(ids)
            except Exception:
                logger.warning("Failed to list all artifacts", exc_info=True)

    # Remove duplicates while preserving order
    seen = set()
    unique_ids: list[str] = []
    for aid in artifact_ids:
        if aid not in seen:
            seen.add(aid)
            unique_ids.append(aid)
    artifact_ids = unique_ids

    # Build list items
    items: list[dict[str, Any]] = []
    for aid in artifact_ids:
        item = await _build_artifact_list_item(aid, container)
        if item is None:
            continue

        # Apply artifact_type filter
        if artifact_type and item.get("artifact_type") != artifact_type:
            continue

        # Apply created_by filter
        if created_by:
            # Match against model_slot or model_name or domain
            match_fields = [
                item.get("model_slot", ""),
                item.get("model_name", ""),
                item.get("domain", ""),
                item.get("artifact_id", ""),
            ]
            if not any(created_by.lower() in f.lower() for f in match_fields):
                continue

        # Apply search filter
        if search:
            search_lower = search.lower()
            searchable = " ".join([
                item.get("artifact_id", ""),
                item.get("title", ""),
                item.get("domain", ""),
                item.get("artifact_type", ""),
                item.get("project", ""),
            ]).lower()
            if search_lower not in searchable:
                continue

        items.append(item)

    # Sort by created_at descending (newest first)
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    # Paginate
    total = len(items)
    start = (page - 1) * effective_page_size
    page_items = items[start: start + effective_page_size]

    return {
        "items": page_items,
        "page": page,
        "page_size": effective_page_size,
        "total": total,
    }


# ── GET /artifacts/dashboard — summary (MUST be before {artifact_id} routes) ─


@router.get("/artifacts/dashboard")
async def get_artifact_dashboard(
    container: AipContainer = Depends(get_container),
):
    """Get artifact review queue summary for dashboard.

    Returns counts by ECS state, NEEDS_REVISION count, force-export count,
    and recent activity. Honest zeros if stores are unavailable.
    """
    counts: dict[str, int] = {state: 0 for state in ALL_STATES}
    needs_revision_count = 0
    force_export_count = 0
    recent_events: list[dict[str, Any]] = []

    if container.ecs_store is not None:
        try:
            for state in ALL_STATES:
                ids = await container.ecs_store.list_by_state(state)
                counts[state] = len(ids)
        except Exception:
            logger.warning("Failed to count artifacts by state", exc_info=True)

    # Count NEEDS_REVISION verdicts
    if container.event_store is not None:
        try:
            # Check all GENERATED artifacts for NEEDS_REVISION verdicts
            generated_ids = await container.ecs_store.list_by_state("GENERATED") if container.ecs_store else []
            for aid in generated_ids:
                events = await container.event_store.query(
                    artifact_id=aid,
                    event_type="review_verdict",
                    limit=50,
                )
                for ev in events:
                    meta = ev.metadata if hasattr(ev, "metadata") else {}
                    if isinstance(meta, dict) and meta.get("verdict") == "NEEDS_REVISION":
                        needs_revision_count += 1
                        break
        except Exception:
            logger.warning("Failed to count NEEDS_REVISION verdicts", exc_info=True)

    # Count force-export events
    if container.event_store is not None:
        try:
            force_events = await container.event_store.query(
                event_type="force_export",
                limit=100,
            )
            force_export_count = len(force_events)
        except Exception:
            logger.warning("Failed to count force-export events", exc_info=True)

    # Recent events (last 10)
    if container.event_store is not None:
        try:
            recent = await container.event_store.query(limit=10)
            for ev in recent:
                recent_events.append({
                    "event_type": ev.event_type,
                    "artifact_id": ev.artifact_id,
                    "actor": ev.actor,
                    "timestamp": ev.timestamp,
                })
        except Exception:
            logger.warning("Failed to get recent events", exc_info=True)

    return {
        "counts": counts,
        "needs_revision_count": needs_revision_count,
        "force_export_count": force_export_count,
        "total_active": counts.get("GENERATED", 0) + counts.get("REVIEWED", 0) + counts.get("APPROVED", 0),
        "total_pending_review": counts.get("GENERATED", 0) + needs_revision_count,
        "recent_events": recent_events,
    }


# ── GET /artifacts/{artifact_id} — detail ──────────────────────────────


@router.get("/artifacts/{artifact_id}")
async def get_artifact(
    artifact_id: str,
    container: AipContainer = Depends(get_container),
):
    """Get artifact detail including content, metadata, ECS state, sources,
    review history, and export eligibility.

    Returns honest 404 if artifact not found.
    Returns honest empty arrays if reviews/sources unavailable.
    """
    # Read content + metadata
    if container.artifact_store is None:
        raise HTTPException(503, "Artifact store unavailable")

    try:
        content, metadata = await container.artifact_store.read_with_metadata(artifact_id)
    except KeyError:
        raise HTTPException(404, f"Artifact '{artifact_id}' not found")
    except Exception as exc:
        raise HTTPException(503, f"Artifact store error: {exc}") from exc

    # Get ECS state
    ecs_state = "UNKNOWN"
    if container.ecs_store is not None:
        try:
            ecs_state = await container.ecs_store.current_state(artifact_id) or "UNKNOWN"
        except Exception:
            ecs_state = "UNKNOWN"

    # Get transition history
    transition_history: list[dict] = []
    if container.ecs_store is not None:
        try:
            transition_history = await container.ecs_store.get_transition_history(artifact_id)
        except Exception:
            transition_history = []

    # Get review events
    review_events_raw: list = []
    if container.event_store is not None:
        try:
            review_events_raw = await container.event_store.query(
                artifact_id=artifact_id,
                event_type="review_verdict",
                limit=50,
            )
        except Exception:
            review_events_raw = []

    # Build review notes from events
    review_notes: list[dict[str, Any]] = []
    for ev in review_events_raw:
        meta = ev.metadata if hasattr(ev, "metadata") else {}
        if isinstance(meta, dict):
            review_notes.append({
                "verdict": meta.get("verdict", ""),
                "detail": meta.get("detail", ""),
                "actor": ev.actor,
                "timestamp": ev.timestamp,
                "instruction": meta.get("revision_instruction", ""),
                "note": meta.get("rejection_note", ""),
            })

    # Check for NEEDS_REVISION verdict
    has_needs_revision = any(
        rn.get("verdict") == "NEEDS_REVISION" for rn in review_notes
    )

    # Check for export events
    export_events_raw: list = []
    if container.event_store is not None:
        try:
            export_events_raw = await container.event_store.query(
                artifact_id=artifact_id,
                event_type="artifact_exported",
                limit=10,
            )
        except Exception:
            export_events_raw = []

    # Check for force-export events
    force_export_events_raw: list = []
    if container.event_store is not None:
        try:
            force_export_events_raw = await container.event_store.query(
                artifact_id=artifact_id,
                event_type="force_export",
                limit=10,
            )
        except Exception:
            force_export_events_raw = []

    has_export = len(export_events_raw) > 0 or len(force_export_events_raw) > 0

    # Source count
    source_ids = metadata.get("source_ids", [])
    source_types = metadata.get("source_types", [])

    # Export eligibility — honest assessment
    export_eligible = ecs_state == "APPROVED"
    export_requires_force = ecs_state in ("GENERATED", "REVIEWED", "REJECTED")

    # Version info
    versions: list[dict] = []
    try:
        version_list = await container.artifact_store.list_versions(artifact_id)
        versions = [{"version": v} for v in version_list] if version_list else []
    except Exception:
        versions = []

    return {
        "artifact_id": artifact_id,
        "title": metadata.get("title", metadata.get("prompt", artifact_id))[:120],
        "ecs_state": ecs_state,
        "has_needs_revision": has_needs_revision,
        "has_export": has_export,
        "artifact_type": metadata.get("artifact_type", ""),
        "content": content or "",
        "metadata": metadata,
        "domain": metadata.get("domain", ""),
        "project": metadata.get("project_name", metadata.get("project_id", "")),
        "prompt": metadata.get("prompt", ""),
        "model_slot": metadata.get("model_slot", ""),
        "model_name": metadata.get("model_name", ""),
        "model_provider": metadata.get("model_provider", ""),
        "generated_at": metadata.get("generated_at", metadata.get("created_at", "")),
        "session_id": metadata.get("session_id", ""),
        "source_ids": source_ids,
        "source_types": source_types,
        "source_count": len(source_ids),
        "review_notes": review_notes,
        "transition_history": transition_history,
        "export_events": [
            {
                "event_type": ev.event_type,
                "timestamp": ev.timestamp,
                "actor": ev.actor,
            }
            for ev in export_events_raw
        ],
        "force_export_events": [
            {
                "event_type": ev.event_type,
                "timestamp": ev.timestamp,
                "actor": ev.actor,
                "metadata": ev.metadata if hasattr(ev, "metadata") else {},
            }
            for ev in force_export_events_raw
        ],
        "export_eligible": export_eligible,
        "export_requires_force": export_requires_force,
        "versions": versions,
        "created_at": metadata.get("generated_at", metadata.get("created_at", "")),
        "updated_at": metadata.get("updated_at", ""),
    }


# ── GET /artifacts/{artifact_id}/sources — provenance ──────────────────


@router.get("/artifacts/{artifact_id}/sources")
async def get_artifact_sources(
    artifact_id: str,
    container: AipContainer = Depends(get_container),
):
    """Get source/provenance links for an artifact.

    Returns honest empty list if sources unavailable.
    Returns honest 404 if artifact not found.
    """
    if container.artifact_store is None:
        raise HTTPException(503, "Artifact store unavailable")

    try:
        metadata = await container.artifact_store.read_metadata(artifact_id)
    except KeyError:
        raise HTTPException(404, f"Artifact '{artifact_id}' not found")
    except Exception as exc:
        raise HTTPException(503, f"Artifact store error: {exc}") from exc

    source_ids = metadata.get("source_ids", [])
    source_types = metadata.get("source_types", [])

    if not source_ids:
        return {
            "artifact_id": artifact_id,
            "source_count": 0,
            "sources": [],
        }

    # Try batch-read for source details
    sources: list[dict[str, Any]] = []
    try:
        batch_data = await container.artifact_store.read_with_metadata_batch(source_ids)
    except Exception:
        batch_data = {}

    for i, sid in enumerate(source_ids):
        src_type = source_types[i] if i < len(source_types) else "unknown"
        snippet = ""
        source_title = sid

        if sid in batch_data:
            src_content, src_metadata = batch_data[sid]
            snippet = src_content[:200] if src_content else ""
            source_title = src_metadata.get("source_file", src_metadata.get("title", sid))

        sources.append({
            "source_id": sid,
            "source_type": src_type,
            "title": source_title,
            "snippet": snippet[:200],
        })

    return {
        "artifact_id": artifact_id,
        "source_count": len(sources),
        "sources": sources,
    }


# ── GET /artifacts/{artifact_id}/reviews — review history/ledger ───────


@router.get("/artifacts/{artifact_id}/reviews")
async def get_artifact_reviews(
    artifact_id: str,
    container: AipContainer = Depends(get_container),
):
    """Get review history/ledger for an artifact.

    Returns honest empty list if reviews unavailable.
    Returns honest 404 if artifact not found.
    """
    if container.artifact_store is None:
        raise HTTPException(503, "Artifact store unavailable")

    # Verify artifact exists
    try:
        await container.artifact_store.read_metadata(artifact_id)
    except KeyError:
        raise HTTPException(404, f"Artifact '{artifact_id}' not found")
    except Exception as exc:
        raise HTTPException(503, f"Artifact store error: {exc}") from exc

    # Get ECS transition history
    transition_history: list[dict] = []
    if container.ecs_store is not None:
        try:
            transition_history = await container.ecs_store.get_transition_history(artifact_id)
        except Exception:
            transition_history = []

    # Get all review events
    review_events_raw: list = []
    if container.event_store is not None:
        try:
            review_events_raw = await container.event_store.query(
                artifact_id=artifact_id,
                event_type="review_verdict",
                limit=50,
            )
        except Exception:
            review_events_raw = []

    # Get reviewer notes
    note_events_raw: list = []
    if container.event_store is not None:
        try:
            note_events_raw = await container.event_store.query(
                artifact_id=artifact_id,
                event_type="reviewer_note",
                limit=50,
            )
        except Exception:
            note_events_raw = []

    # Get export events
    export_events_raw: list = []
    if container.event_store is not None:
        try:
            export_events_raw = await container.event_store.query(
                artifact_id=artifact_id,
                event_type="artifact_exported",
                limit=20,
            )
        except Exception:
            export_events_raw = []

    # Get force-export events
    force_export_events_raw: list = []
    if container.event_store is not None:
        try:
            force_export_events_raw = await container.event_store.query(
                artifact_id=artifact_id,
                event_type="force_export",
                limit=20,
            )
        except Exception:
            force_export_events_raw = []

    # Build combined ledger
    ledger: list[dict[str, Any]] = []

    # ECS transitions
    for t in transition_history:
        ledger.append({
            "event_type": "ecs_transition",
            "from_state": t.get("from_state"),
            "to_state": t.get("to_state"),
            "actor": t.get("actor", ""),
            "reason": t.get("reason", ""),
            "timestamp": t.get("timestamp", ""),
        })

    # Review verdicts
    for ev in review_events_raw:
        meta = ev.metadata if hasattr(ev, "metadata") else {}
        if not isinstance(meta, dict):
            meta = {}
        ledger.append({
            "event_type": "review_verdict",
            "verdict": meta.get("verdict", ""),
            "detail": meta.get("detail", ""),
            "actor": ev.actor,
            "timestamp": ev.timestamp,
            "instruction": meta.get("revision_instruction", ""),
            "note": meta.get("rejection_note", ""),
        })

    # Reviewer notes
    for ev in note_events_raw:
        meta = ev.metadata if hasattr(ev, "metadata") else {}
        if not isinstance(meta, dict):
            meta = {}
        ledger.append({
            "event_type": "reviewer_note",
            "detail": meta.get("detail", ""),
            "actor": ev.actor,
            "timestamp": ev.timestamp,
        })

    # Export events
    for ev in export_events_raw:
        meta = ev.metadata if hasattr(ev, "metadata") else {}
        if not isinstance(meta, dict):
            meta = {}
        ledger.append({
            "event_type": "artifact_exported",
            "format": meta.get("format", ""),
            "detail": meta.get("detail", ""),
            "actor": ev.actor,
            "timestamp": ev.timestamp,
        })

    # Force-export events
    for ev in force_export_events_raw:
        meta = ev.metadata if hasattr(ev, "metadata") else {}
        if not isinstance(meta, dict):
            meta = {}
        ledger.append({
            "event_type": "force_export",
            "bypassed_state": meta.get("bypassed_state", ""),
            "reason": meta.get("reason", ""),
            "detail": meta.get("detail", ""),
            "actor": ev.actor,
            "timestamp": ev.timestamp,
        })

    # Sort by timestamp
    ledger.sort(key=lambda x: x.get("timestamp", ""))

    return {
        "artifact_id": artifact_id,
        "ledger": ledger,
        "transition_count": len(transition_history),
        "review_count": len(review_events_raw),
        "note_count": len(note_events_raw),
        "export_count": len(export_events_raw),
        "force_export_count": len(force_export_events_raw),
    }


# ── POST /artifacts/{artifact_id}/approve ──────────────────────────────


@router.post("/artifacts/{artifact_id}/approve")
async def approve_artifact(
    artifact_id: str,
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Approve an artifact — explicit DEFINER action only.

    Transition path: GENERATED → REVIEWED → APPROVED (two transitions).
    If artifact is already REVIEWED: REVIEWED → APPROVED.
    Writes to CanonicalStore on APPROVED transition.
    Records event in EventStore.
    No auto-approve. No silent state change.
    """
    if container.artifact_store is None:
        raise HTTPException(503, "Artifact store unavailable")

    # Validate artifact exists
    try:
        content, metadata = await container.artifact_store.read_with_metadata(artifact_id)
    except KeyError:
        raise HTTPException(404, f"Artifact '{artifact_id}' not found")
    except Exception as exc:
        raise HTTPException(503, f"Artifact store error: {exc}") from exc

    # Validate non-empty content
    if not content or not content.strip():
        raise HTTPException(400, f"Artifact '{artifact_id}' has empty content — cannot approve")

    # Get current state
    current_state = None
    if container.ecs_store is not None:
        try:
            current_state = await container.ecs_store.current_state(artifact_id)
        except Exception:
            pass

    if current_state is None:
        raise HTTPException(400, f"Artifact '{artifact_id}' has no ECS state recorded")

    if current_state == "APPROVED":
        raise HTTPException(400, f"Artifact '{artifact_id}' is already APPROVED")

    if current_state == "REJECTED":
        raise HTTPException(400, f"Artifact '{artifact_id}' is REJECTED — re-generate before approving")

    if current_state == "SUPERSEDED":
        raise HTTPException(400, f"Artifact '{artifact_id}' is SUPERSEDED — cannot approve")

    # Execute transitions
    try:
        if current_state == "GENERATED":
            # GENERATED → REVIEWED
            if container.ecs_store is not None:
                await container.ecs_store.transition(
                    artifact_id=artifact_id,
                    from_state="GENERATED",
                    to_state="REVIEWED",
                    actor="definer",
                    reason="DEFINER approved via Artifact Workbench — quality gate passed",
                )

            # REVIEWED → APPROVED
            if container.ecs_store is not None:
                await container.ecs_store.transition(
                    artifact_id=artifact_id,
                    from_state="REVIEWED",
                    to_state="APPROVED",
                    actor="definer",
                    reason="DEFINER approved — promoted to canonical",
                )

        elif current_state == "REVIEWED":
            if container.ecs_store is not None:
                await container.ecs_store.transition(
                    artifact_id=artifact_id,
                    from_state="REVIEWED",
                    to_state="APPROVED",
                    actor="definer",
                    reason="DEFINER approved — promoted to canonical",
                )
        else:
            raise HTTPException(400, f"Cannot approve artifact in {current_state} state")
    except InvalidTransitionError as exc:
        raise HTTPException(400, str(exc)) from exc

    # Write to CanonicalStore (DEFINER sovereignty)
    canonical_written = False
    if container.canonical_store is not None and container.artifact_store is not None:
        try:
            await container.canonical_store.write_canonical(
                artifact_id=artifact_id,
                content={"text": content, "metadata": metadata},
                approved_by="definer",
            )
            canonical_written = True
        except Exception as exc:
            logger.warning("Canonical write failed after approve: %s", exc)

    # Record review event
    if container.event_store is not None:
        try:
            source_ids = metadata.get("source_ids", [])
            await container.event_store.write_event(
                event_type="review_verdict",
                actor="definer",
                artifact_id=artifact_id,
                from_state=current_state,
                to_state="APPROVED",
                verdict="APPROVED",
                detail="DEFINER approved via Artifact Workbench — promoted to canonical",
                source_count=len(source_ids),
            )
        except Exception:
            logger.debug("Event recording failed after approve", exc_info=True)

    new_state = "APPROVED"
    if container.ecs_store is not None:
        try:
            new_state = await container.ecs_store.current_state(artifact_id) or "APPROVED"
        except Exception:
            pass

    return {
        "artifact_id": artifact_id,
        "previous_state": current_state,
        "new_state": new_state,
        "canonical_written": canonical_written,
        "actor": "definer",
    }


# ── POST /artifacts/{artifact_id}/reject ───────────────────────────────


@router.post("/artifacts/{artifact_id}/reject")
async def reject_artifact(
    artifact_id: str,
    body: RejectRequest | None = None,
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Reject an artifact — explicit DEFINER action only.

    Transition: GENERATED/REVIEWED → REJECTED.
    Preserves artifact and source links.
    Records rejection note in EventStore.
    No silent state change.
    """
    if container.artifact_store is None:
        raise HTTPException(503, "Artifact store unavailable")

    note = body.note if body else ""

    # Validate artifact exists
    try:
        await container.artifact_store.read_with_metadata(artifact_id)
    except KeyError:
        raise HTTPException(404, f"Artifact '{artifact_id}' not found")
    except Exception as exc:
        raise HTTPException(503, f"Artifact store error: {exc}") from exc

    # Get current state
    current_state = None
    if container.ecs_store is not None:
        try:
            current_state = await container.ecs_store.current_state(artifact_id)
        except Exception:
            pass

    if current_state is None:
        raise HTTPException(400, f"Artifact '{artifact_id}' has no ECS state recorded")

    if current_state == "REJECTED":
        raise HTTPException(400, f"Artifact '{artifact_id}' is already REJECTED")

    if current_state == "APPROVED":
        raise HTTPException(400, f"Artifact '{artifact_id}' is APPROVED — cannot reject approved artifact")

    if current_state == "SUPERSEDED":
        raise HTTPException(400, f"Artifact '{artifact_id}' is SUPERSEDED — cannot reject")

    # Execute transition
    try:
        if container.ecs_store is not None:
            await container.ecs_store.transition(
                artifact_id=artifact_id,
                from_state=current_state,
                to_state="REJECTED",
                actor="definer",
                reason=f"DEFINER rejected via Artifact Workbench: {note}" if note else "DEFINER rejected via Artifact Workbench",
            )
    except InvalidTransitionError as exc:
        raise HTTPException(400, str(exc)) from exc

    # Record rejection event
    if container.event_store is not None:
        try:
            await container.event_store.write_event(
                event_type="review_verdict",
                actor="definer",
                artifact_id=artifact_id,
                from_state=current_state,
                to_state="REJECTED",
                verdict="REJECTED",
                detail=note or "DEFINER rejected via Artifact Workbench",
                rejection_note=note,
            )
        except Exception:
            logger.debug("Event recording failed after reject", exc_info=True)

    new_state = "REJECTED"
    if container.ecs_store is not None:
        try:
            new_state = await container.ecs_store.current_state(artifact_id) or "REJECTED"
        except Exception:
            pass

    return {
        "artifact_id": artifact_id,
        "previous_state": current_state,
        "new_state": new_state,
        "actor": "definer",
        "note": note,
        "artifact_preserved": True,
    }


# ── POST /artifacts/{artifact_id}/needs-revision ───────────────────────


@router.post("/artifacts/{artifact_id}/needs-revision")
async def needs_revision_artifact(
    artifact_id: str,
    body: NeedsRevisionRequest | None = None,
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Mark artifact as needing revision — explicit DEFINER action only.

    The artifact stays in its current ECS state (typically GENERATED).
    NEEDS_REVISION is a verdict stored as an event, not an ECS state.
    The revision instruction is stored as a review event.
    No ECS transition occurs.
    """
    if container.artifact_store is None:
        raise HTTPException(503, "Artifact store unavailable")

    instruction = body.instruction if body else ""

    # Validate artifact exists
    try:
        await container.artifact_store.read_with_metadata(artifact_id)
    except KeyError:
        raise HTTPException(404, f"Artifact '{artifact_id}' not found")
    except Exception as exc:
        raise HTTPException(503, f"Artifact store error: {exc}") from exc

    # Get current state
    current_state = None
    if container.ecs_store is not None:
        try:
            current_state = await container.ecs_store.current_state(artifact_id)
        except Exception:
            pass

    if current_state is None:
        raise HTTPException(400, f"Artifact '{artifact_id}' has no ECS state recorded")

    if current_state == "APPROVED":
        raise HTTPException(400, f"Artifact '{artifact_id}' is APPROVED — use reject instead if you want to change its state")

    if current_state == "REJECTED":
        raise HTTPException(400, f"Artifact '{artifact_id}' is REJECTED — re-generate before requesting revision")

    # Store the revision instruction as a review event (no ECS transition)
    if container.event_store is not None:
        try:
            await container.event_store.write_event(
                event_type="review_verdict",
                actor="definer",
                artifact_id=artifact_id,
                from_state=current_state,
                to_state=current_state,  # State doesn't change
                verdict="NEEDS_REVISION",
                detail=instruction or "DEFINER requests revision via Artifact Workbench",
                revision_instruction=instruction,
            )
        except Exception:
            logger.debug("Event recording failed after needs-revision", exc_info=True)

    return {
        "artifact_id": artifact_id,
        "ecs_state": current_state,  # Unchanged
        "actor": "definer",
        "instruction": instruction,
        "artifact_preserved": True,
    }


# ── POST /artifacts/{artifact_id}/export ───────────────────────────────


@router.post("/artifacts/{artifact_id}/export")
async def export_artifact(
    artifact_id: str,
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Export an APPROVED artifact — records export event.

    Only APPROVED artifacts can be exported normally.
    Non-APPROVED artifacts require the force-export endpoint.
    No silent export. No auto-export.
    """
    if container.artifact_store is None:
        raise HTTPException(503, "Artifact store unavailable")

    # Validate artifact exists
    try:
        content, metadata = await container.artifact_store.read_with_metadata(artifact_id)
    except KeyError:
        raise HTTPException(404, f"Artifact '{artifact_id}' not found")
    except Exception as exc:
        raise HTTPException(503, f"Artifact store error: {exc}") from exc

    # Validate non-empty content
    if not content or not content.strip():
        raise HTTPException(400, f"Artifact '{artifact_id}' has empty content — refusing to export")

    # Check ECS state
    ecs_state = "UNKNOWN"
    if container.ecs_store is not None:
        try:
            ecs_state = await container.ecs_store.current_state(artifact_id) or "UNKNOWN"
        except Exception:
            pass

    # Export gate: only APPROVED
    if ecs_state != "APPROVED":
        raise HTTPException(
            400,
            f"Artifact '{artifact_id}' is in {ecs_state} state — only APPROVED artifacts can be exported. "
            "Use the force-export endpoint for non-approved artifacts.",
        )

    # Record export event
    if container.event_store is not None:
        try:
            await container.event_store.write_event(
                event_type="artifact_exported",
                actor="definer",
                artifact_id=artifact_id,
                from_state=ecs_state,
                to_state=ecs_state,
                detail=f"Artifact exported from {ecs_state} state via Artifact Workbench",
            )
        except Exception:
            logger.debug("Event recording failed after export", exc_info=True)

    now = datetime.now(timezone.utc).isoformat()

    return {
        "artifact_id": artifact_id,
        "ecs_state": ecs_state,
        "exported": True,
        "exported_at": now,
        "force_bypass": False,
        "actor": "definer",
    }


# ── POST /artifacts/{artifact_id}/force-export ─────────────────────────


@router.post("/artifacts/{artifact_id}/force-export")
async def force_export_artifact(
    artifact_id: str,
    body: ForceExportRequest,
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Force-export an artifact from a non-APPROVED state.

    This is a SOVEREIGN OVERRIDE — visibly exceptional and audited.
    Requires explicit confirmation (force=True) and a reason.
    Every force-export writes a force_export audit event to EventStore.
    The artifact state does not change.
    """
    if not body.force:
        raise HTTPException(400, "force must be explicitly True for sovereign override")

    if container.artifact_store is None:
        raise HTTPException(503, "Artifact store unavailable")

    # Validate artifact exists
    try:
        content, metadata = await container.artifact_store.read_with_metadata(artifact_id)
    except KeyError:
        raise HTTPException(404, f"Artifact '{artifact_id}' not found")
    except Exception as exc:
        raise HTTPException(503, f"Artifact store error: {exc}") from exc

    # Validate non-empty content
    if not content or not content.strip():
        raise HTTPException(400, f"Artifact '{artifact_id}' has empty content — refusing to export")

    # Get ECS state
    ecs_state = "UNKNOWN"
    if container.ecs_store is not None:
        try:
            ecs_state = await container.ecs_store.current_state(artifact_id) or "UNKNOWN"
        except Exception:
            pass

    # Already APPROVED? Use normal export instead.
    if ecs_state == "APPROVED":
        raise HTTPException(
            400,
            f"Artifact '{artifact_id}' is APPROVED — use the normal export endpoint instead of force-export",
        )

    # Record force-export audit event (SOVEREIGN OVERRIDE)
    if container.event_store is not None:
        try:
            await container.event_store.write_event(
                event_type="force_export",
                actor="definer",
                artifact_id=artifact_id,
                from_state=ecs_state,
                to_state="exported",
                bypassed_state=ecs_state,
                reason=body.reason,
                detail=(
                    f"SOVEREIGN OVERRIDE: Artifact '{artifact_id}' force-exported from "
                    f"{ecs_state} state (not APPROVED). Reason: {body.reason}"
                ),
            )
        except Exception:
            logger.debug("Event recording failed after force-export", exc_info=True)

    logger.warning(
        "Force-export: artifact %s exported from %s state. Reason: %s",
        artifact_id, ecs_state, body.reason,
    )

    now = datetime.now(timezone.utc).isoformat()

    return {
        "artifact_id": artifact_id,
        "ecs_state": ecs_state,
        "exported": True,
        "exported_at": now,
        "force_bypass": True,
        "force_bypass_state": ecs_state,
        "force_reason": body.reason,
        "audit_recorded": True,
        "actor": "definer",
    }


# ── GET /artifacts/{artifact_id}/versions ───────────────────────────────


@router.get("/artifacts/{artifact_id}/versions")
async def get_versions(
    artifact_id: str,
    container: AipContainer = Depends(get_container),
):
    """Get version history for an artifact."""
    if container.artifact_store is None:
        raise HTTPException(503, "Artifact store unavailable")

    try:
        version_list = await container.artifact_store.list_versions(artifact_id)
        return {
            "artifact_id": artifact_id,
            "versions": version_list or [],
        }
    except KeyError:
        raise HTTPException(404, f"Artifact '{artifact_id}' not found")
    except Exception as exc:
        raise HTTPException(503, f"Artifact store error: {exc}") from exc


# ── GET /artifacts/{artifact_id}/evaluation ────────────────────────────


@router.get("/artifacts/{artifact_id}/evaluation")
async def get_evaluation(
    artifact_id: str,
    container: AipContainer = Depends(get_container),
):
    """Get evaluation scores for an artifact.

    Returns honest unavailable if no evaluation backend exists.
    Never returns fake scores.
    """
    if container.artifact_store is None:
        raise HTTPException(503, "Artifact store unavailable")

    # Verify artifact exists
    try:
        await container.artifact_store.read_metadata(artifact_id)
    except KeyError:
        raise HTTPException(404, f"Artifact '{artifact_id}' not found")
    except Exception as exc:
        raise HTTPException(503, f"Artifact store error: {exc}") from exc

    # No automated evaluation backend exists yet — return honest unavailable
    return {
        "artifact_id": artifact_id,
        "status": "unavailable",
        "message": "Automated evaluation not yet available. Use the review actions to assess artifact quality.",
    }
