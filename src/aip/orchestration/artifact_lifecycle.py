"""Artifact lifecycle orchestration — creation, ledger, review notes, dashboard.

Composes existing AIP primitives (VersionedArtifactStore, PersistentEcsStore,
QueryableEventStore, SqliteProjectStore) into the DEFINER-facing artifact
lifecycle workflow.  Every artifact transition is recorded; every action
preserves sovereignty.

Sprint 11 — Artifact Lifecycle and Review Sprint:

    1. artifact_create:  Turn content into an artifact with metadata, sources,
                         state, and review history.
    2. review_add_note:  Add reviewer notes without changing ECS state.
    3. artifact_ledger:  Full lifecycle inspection — every transition and event
                         for an artifact, showing who/what moved it and why.
    4. review_dashboard: Review queue summary — counts by state, recent
                         activity, force-export exceptions.

Uses the SAME persistent stores that ``aip ask``, ``aip review``, and
``aip export`` use.  Does NOT create a parallel database or lifecycle.
Does NOT bypass existing ECS, autonomy, or DEFINER gates.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from aip.adapter.artifact_store_versioned import VersionedArtifactStore
from aip.adapter.ecs_store_persistent import PersistentEcsStore
from aip.adapter.event_store_queryable import QueryableEventStore
from aip.adapter.project.sqlite_project_store import SqliteProjectStore
from aip.foundation.schemas.artifact import (
    ArtifactLedgerEntry,
    ArtifactMetadata,
    ReviewQueueSummary,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Store container (reuses ReviewExportStores pattern)
# ---------------------------------------------------------------------------


class ArtifactLifecycleStores:
    """Container for stores needed by the artifact lifecycle pipeline.

    Uses the SAME persistent stores as all other AIP pipelines.
    """

    artifact_store: VersionedArtifactStore
    ecs_store: PersistentEcsStore
    event_store: QueryableEventStore
    project_store: SqliteProjectStore

    def __init__(
        self,
        artifact_store: VersionedArtifactStore,
        ecs_store: PersistentEcsStore,
        event_store: QueryableEventStore,
        project_store: SqliteProjectStore,
    ) -> None:
        self.artifact_store = artifact_store
        self.ecs_store = ecs_store
        self.event_store = event_store
        self.project_store = project_store

    async def close(self) -> None:
        """Close all stores that have a close method."""
        for store in (
            self.artifact_store,
            self.ecs_store,
            self.event_store,
            self.project_store,
        ):
            if store is not None and hasattr(store, "close"):
                try:
                    await store.close()
                except Exception:
                    pass


async def create_artifact_lifecycle_stores(db_path: str) -> ArtifactLifecycleStores:
    """Factory: create and initialize all stores for artifact lifecycle pipeline."""
    artifact_store = VersionedArtifactStore(db_path)
    await artifact_store.initialize()

    event_store = QueryableEventStore(db_path)
    await event_store.initialize()

    project_store = SqliteProjectStore(db_path)
    await project_store.initialize()

    ecs_store = PersistentEcsStore(db_path, event_store=event_store)
    await ecs_store.initialize()

    return ArtifactLifecycleStores(
        artifact_store=artifact_store,
        ecs_store=ecs_store,
        event_store=event_store,
        project_store=project_store,
    )


# ---------------------------------------------------------------------------
# Chunk 1: Artifact creation
# ---------------------------------------------------------------------------


async def artifact_create(
    content: str,
    stores: ArtifactLifecycleStores,
    *,
    title: str = "",
    description: str = "",
    tags: list[str] | None = None,
    artifact_type: str = "manual_document",
    project_name: str = "",
    prompt: str = "",
    source_ids: list[str] | None = None,
    source_types: list[str] | None = None,
    actor: str = "definer",
) -> dict:
    """Create a new artifact from content.

    The artifact enters the ECS lifecycle at GENERATED state,
    ready for review.  It carries full metadata, source links,
    and review history.

    This is the standalone creation path — the ask pipeline has
    its own artifact creation via ``--save-artifact``.

    Returns dict with artifact_id and lifecycle info, or error.
    """
    # Validate content is non-empty
    if not content or not content.strip():
        return {"error": {"code": "EMPTY_CONTENT", "message": "Cannot create artifact with empty content"}}

    # Resolve project if specified
    project_id = ""
    domain = ""
    if project_name:
        project = await _resolve_project(project_name, stores.project_store)
        if project is None:
            return {"error": {"code": "NOT_FOUND", "message": f"Project '{project_name}' not found"}}
        project_id = project.get("project_id", project_name)
        domain = project.get("domain", project_name)

    # Generate artifact ID
    # For manual documents: artifact:uuid
    # For ask answers (when called from ask pipeline): ask:hash
    if artifact_type == "ask_answer":
        artifact_id = f"ask:{hashlib.sha256(f'{project_id}:{prompt}'.encode()).hexdigest()[:24]}"
    else:
        artifact_id = f"artifact:{uuid.uuid4().hex[:20]}"

    # Build metadata
    metadata = ArtifactMetadata(
        artifact_type=artifact_type,
        title=title or content[:80],
        description=description,
        tags=tags or [],
        project_id=project_id,
        project_name=project_name,
        domain=domain,
        prompt=prompt,
        source_ids=source_ids or [],
        source_types=source_types or [],
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    # Write artifact to VersionedArtifactStore
    await stores.artifact_store.write(artifact_id, content, metadata.to_dict())

    # ECS transition: None → GENERATED
    try:
        await stores.ecs_store.transition(
            artifact_id=artifact_id,
            from_state=None,
            to_state="GENERATED",
            actor=actor,
            reason=f"Artifact created ({artifact_type}) — pending DEFINER review",
        )
    except Exception as exc:
        logger.warning("ECS transition failed for artifact '%s': %s", artifact_id, exc)
        return {"error": {"code": "ECS_TRANSITION_FAILED", "message": f"ECS transition failed: {exc}"}}

    # Record creation event
    await stores.event_store.write_event(
        event_type="artifact_created",
        actor=actor,
        artifact_id=artifact_id,
        from_state=None,
        to_state="GENERATED",
        artifact_type=artifact_type,
        title=metadata.title,
        source_count=len(metadata.source_ids),
    )

    return {
        "artifact_id": artifact_id,
        "lifecycle_state": "GENERATED",
        "artifact_type": artifact_type,
        "title": metadata.title,
        "source_count": len(metadata.source_ids),
    }


# ---------------------------------------------------------------------------
# Chunk 2: Review add note
# ---------------------------------------------------------------------------


async def review_add_note(
    artifact_id: str,
    stores: ArtifactLifecycleStores,
    note: str,
    actor: str = "definer",
) -> dict:
    """Add a reviewer note to an artifact without changing its state.

    The note is recorded as a ``reviewer_note`` event in EventStore.
    No ECS transition occurs — this is purely informational, for the
    DEFINER's own reference and for other reviewers.

    This is distinct from:
    - ``review_verdict`` events (which carry APPROVED/REJECTED/NEEDS_REVISION)
    - ``needs-revision`` command (which stores a revision instruction)
    """
    # Validate artifact exists
    try:
        await stores.artifact_store.read(artifact_id)
    except KeyError:
        return {"error": {"code": "NOT_FOUND", "message": f"Artifact '{artifact_id}' not found"}}

    # Get current ECS state
    current_state = await stores.ecs_store.current_state(artifact_id)
    if current_state is None:
        return {"error": {"code": "NO_ECS_STATE", "message": f"Artifact '{artifact_id}' has no ECS state recorded"}}

    # Record the note as a reviewer_note event (no ECS transition)
    await stores.event_store.write_event(
        event_type="reviewer_note",
        actor=actor,
        artifact_id=artifact_id,
        from_state=current_state,
        to_state=current_state,  # State doesn't change
        note=note,
    )

    return {
        "artifact_id": artifact_id,
        "lifecycle_state": current_state,
        "note": note,
        "actor": actor,
    }


# ---------------------------------------------------------------------------
# Chunk 3: Normal export records artifact_exported event
# ---------------------------------------------------------------------------


async def record_artifact_exported_event(
    artifact_id: str,
    ecs_state: str,
    stores: ArtifactLifecycleStores,
    format: str = "markdown",
    out_path: str = "",
    actor: str = "export_pipeline",
) -> None:
    """Record an artifact_exported event when an artifact is exported normally.

    This is called for APPROVED artifact exports (the normal path).
    Force-exports use the existing _record_force_export_audit() which
    writes a ``force_export`` event type.

    This ensures that EVERY export — normal or force — has a traceable
    event in the audit trail.
    """
    await stores.event_store.write_event(
        event_type="artifact_exported",
        actor=actor,
        artifact_id=artifact_id,
        from_state=ecs_state,
        to_state=ecs_state,  # State doesn't change on export
        format=format,
        out_path=out_path,
        detail=f"Artifact exported from {ecs_state} state in {format} format",
    )


# ---------------------------------------------------------------------------
# Chunk 5: Artifact ledger
# ---------------------------------------------------------------------------


async def artifact_ledger(
    artifact_id: str,
    stores: ArtifactLifecycleStores,
    limit: int = 100,
) -> dict:
    """Full lifecycle inspection for an artifact.

    Returns every event in the artifact's lifecycle, showing who/what
    moved it and why.  This is the DEFINER's primary tool for
    understanding how an artifact reached its current state.

    Combines:
    - ECS transition history (from PersistentEcsStore)
    - All EventStore events (ecs_transition, review_verdict,
      reviewer_note, artifact_created, artifact_exported, force_export)

    The result is a unified timeline in reverse chronological order.
    """
    # Validate artifact exists
    try:
        content, metadata = await stores.artifact_store.read_with_metadata(artifact_id)
    except KeyError:
        return {"error": {"code": "NOT_FOUND", "message": f"Artifact '{artifact_id}' not found"}}

    # Get current ECS state
    current_state = await stores.ecs_store.current_state(artifact_id) or "UNKNOWN"

    # Get ECS transition history
    transitions = await stores.ecs_store.get_transition_history(artifact_id, limit=limit)

    # Get ALL events for this artifact
    events = await stores.event_store.query(artifact_id=artifact_id, limit=limit)

    # Build unified ledger entries
    ledger_entries: list[ArtifactLedgerEntry] = []

    # Add events (these include ecs_transition, review_verdict, reviewer_note,
    # artifact_created, artifact_exported, force_export)
    for ev in events:
        detail = ""
        meta = ev.metadata if hasattr(ev, "metadata") and isinstance(ev.metadata, dict) else {}

        if ev.event_type == "review_verdict":
            verdict = meta.get("verdict", "")
            note = meta.get("detail", meta.get("rejection_note", meta.get("revision_instruction", "")))
            detail = f"Verdict: {verdict}" + (f" — {note}" if note else "")
        elif ev.event_type == "reviewer_note":
            detail = meta.get("note", "")
        elif ev.event_type == "artifact_created":
            detail = meta.get("title", "Artifact created")
        elif ev.event_type == "artifact_exported":
            fmt = meta.get("format", "unknown")
            detail = f"Exported as {fmt}"
        elif ev.event_type == "force_export":
            bypassed = meta.get("bypassed_state", "")
            reason = meta.get("reason", "")
            detail = f"SOVEREIGN OVERRIDE: exported from {bypassed}" + (f" — {reason}" if reason else "")
        elif ev.event_type == "ecs_transition":
            reason = meta.get("reason", "")
            detail = f"State transition" + (f" — {reason}" if reason else "")
        elif ev.event_type == "ask_query":
            prompt = meta.get("prompt", "")
            detail = f"Ask query" + (f": {prompt[:80]}" if prompt else "")
        else:
            detail = meta.get("detail", ev.event_type)

        ledger_entries.append(ArtifactLedgerEntry(
            event_type=ev.event_type,
            actor=ev.actor,
            artifact_id=artifact_id,
            from_state=ev.from_state,
            to_state=ev.to_state,
            timestamp=ev.timestamp,
            detail=detail,
            metadata=meta,
        ))

    # Build summary
    creation_event = next(
        (e for e in ledger_entries if e.event_type in ("artifact_created", "ecs_transition") and e.from_state is None),
        None,
    )

    return {
        "artifact_id": artifact_id,
        "current_state": current_state,
        "title": metadata.get("title", metadata.get("prompt", artifact_id))[:120],
        "artifact_type": metadata.get("artifact_type", ""),
        "project": metadata.get("project_name", metadata.get("project_id", "")),
        "source_count": len(metadata.get("source_ids", [])),
        "created_at": metadata.get("generated_at", metadata.get("created_at", "")),
        "transition_count": len(transitions),
        "event_count": len(events),
        "ledger": [
            {
                "event_type": entry.event_type,
                "actor": entry.actor,
                "from_state": entry.from_state,
                "to_state": entry.to_state,
                "timestamp": entry.timestamp,
                "detail": entry.detail,
            }
            for entry in ledger_entries
        ],
    }


# ---------------------------------------------------------------------------
# Chunk 6: Review queue dashboard
# ---------------------------------------------------------------------------


async def review_dashboard(
    stores: ArtifactLifecycleStores,
    recent_limit: int = 20,
) -> dict:
    """Review queue dashboard — snapshot of artifact states and recent activity.

    Returns a ReviewQueueSummary with:
    - Counts of artifacts in each ECS state
    - Count of artifacts with NEEDS_REVISION verdict (still in GENERATED state)
    - Recent force-export events (sovereign override exceptions)
    - Recent review activity
    """
    # Count artifacts in each ECS state
    generated_ids = await stores.ecs_store.list_by_state("GENERATED")
    reviewed_ids = await stores.ecs_store.list_by_state("REVIEWED")
    approved_ids = await stores.ecs_store.list_by_state("APPROVED")
    rejected_ids = await stores.ecs_store.list_by_state("REJECTED")
    superseded_ids = await stores.ecs_store.list_by_state("SUPERSEDED")
    failed_ids = await stores.ecs_store.list_by_state("FAILED")

    # Count NEEDS_REVISION verdicts (still in GENERATED state but flagged)
    needs_revision_count = 0
    if generated_ids:
        # Check recent review_verdict events for NEEDS_REVISION
        recent_verdicts = await stores.event_store.query(
            event_type="review_verdict",
            limit=500,
        )
        needs_revision_artifact_ids: set[str] = set()
        for ev in recent_verdicts:
            meta = ev.metadata if hasattr(ev, "metadata") and isinstance(ev.metadata, dict) else {}
            if meta.get("verdict") == "NEEDS_REVISION" and ev.artifact_id in generated_ids:
                needs_revision_artifact_ids.add(ev.artifact_id)
        needs_revision_count = len(needs_revision_artifact_ids)

    # Get recent force-export events
    force_export_events = await stores.event_store.query(
        event_type="force_export",
        limit=20,
    )
    force_export_summaries = []
    for ev in force_export_events:
        meta = ev.metadata if hasattr(ev, "metadata") and isinstance(ev.metadata, dict) else {}
        force_export_summaries.append({
            "artifact_id": ev.artifact_id,
            "bypassed_state": meta.get("bypassed_state", ""),
            "reason": meta.get("reason", ""),
            "timestamp": ev.timestamp,
            "actor": ev.actor,
        })

    # Get recent events (all types)
    recent_events_raw = await stores.event_store.query(limit=recent_limit)
    recent_events = []
    for ev in recent_events_raw:
        meta = ev.metadata if hasattr(ev, "metadata") and isinstance(ev.metadata, dict) else {}
        recent_events.append({
            "event_type": ev.event_type,
            "artifact_id": ev.artifact_id,
            "actor": ev.actor,
            "timestamp": ev.timestamp,
            "from_state": ev.from_state,
            "to_state": ev.to_state,
            "detail": meta.get("detail", meta.get("note", meta.get("verdict", ""))),
        })

    summary = ReviewQueueSummary(
        generated_count=len(generated_ids),
        reviewed_count=len(reviewed_ids),
        approved_count=len(approved_ids),
        rejected_count=len(rejected_ids),
        superseded_count=len(superseded_ids),
        failed_count=len(failed_ids),
        needs_revision_count=needs_revision_count,
        force_export_count=len(force_export_summaries),
        force_export_events=force_export_summaries,
        recent_events=recent_events,
    )

    return {
        "states": {
            "GENERATED": summary.generated_count,
            "REVIEWED": summary.reviewed_count,
            "APPROVED": summary.approved_count,
            "REJECTED": summary.rejected_count,
            "SUPERSEDED": summary.superseded_count,
            "FAILED": summary.failed_count,
        },
        "needs_revision_count": summary.needs_revision_count,
        "total_active": summary.total_active,
        "total_pending_review": summary.total_pending_review,
        "force_export_count": summary.force_export_count,
        "force_export_events": summary.force_export_events[:10],
        "recent_events": summary.recent_events,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _resolve_project(
    project_name: str,
    project_store: SqliteProjectStore,
) -> dict | None:
    """Resolve a project by name or ID from ProjectStore."""
    projects = await project_store.list_projects()
    for p in projects:
        if p.get("name") == project_name:
            return p
    for p in projects:
        if p.get("project_id") == project_name:
            return p
    return None
