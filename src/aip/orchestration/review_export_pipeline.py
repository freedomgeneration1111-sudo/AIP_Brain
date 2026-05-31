"""Review, approve, reject, and export pipeline for generated artifacts.

Composes existing AIP primitives — ECS transitions, ArtifactStore metadata,
EventStore tracing, CanonicalStore, ProjectStore — into the DEFINER-facing
review and export workflow required by the spec.

Reuses the SAME persistent stores that ``aip ingest`` and ``aip ask`` use.
Does NOT create a parallel review database or lifecycle.
Does NOT bypass existing ECS, autonomy, or DEFINER gates.

Lifecycle path for generated artifacts:
    GENERATED → REVIEWED → APPROVED  (full approval, written to canonical store)
    GENERATED → REJECTED             (DEFINER rejects, artifact preserved)
    GENERATED → (stays GENERATED)    (needs-revision: verdict stored as event)

REJECTED → GENERATED is the re-synthesis loop in the ECS graph.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from aip.adapter.artifact_store_versioned import VersionedArtifactStore
from aip.adapter.ecs_store_persistent import PersistentEcsStore
from aip.adapter.event_store_queryable import QueryableEventStore
from aip.adapter.project.sqlite_project_store import SqliteProjectStore
from aip.foundation.ecs_graph import InvalidTransitionError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Store container
# ---------------------------------------------------------------------------


@dataclass
class ReviewExportStores:
    """Container for stores needed by the review/export pipeline.

    Uses the SAME persistent stores as ingestion and ask pipelines.
    """

    artifact_store: VersionedArtifactStore
    ecs_store: PersistentEcsStore
    event_store: QueryableEventStore
    project_store: SqliteProjectStore

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


async def create_review_export_stores(db_path: str) -> ReviewExportStores:
    """Factory: create and initialize all stores for review/export pipeline.

    Uses the SAME database paths as ``create_ask_stores()`` and
    ``create_ingestion_stores()`` to ensure consistency.
    """
    artifact_store = VersionedArtifactStore(db_path)
    await artifact_store.initialize()

    event_store = QueryableEventStore(db_path)
    await event_store.initialize()

    project_store = SqliteProjectStore(db_path)
    await project_store.initialize()

    ecs_store = PersistentEcsStore(db_path, event_store=event_store)
    await ecs_store.initialize()

    return ReviewExportStores(
        artifact_store=artifact_store,
        ecs_store=ecs_store,
        event_store=event_store,
        project_store=project_store,
    )


# ---------------------------------------------------------------------------
# Review list
# ---------------------------------------------------------------------------


async def review_list(
    project_name: str,
    stores: ReviewExportStores,
    states: list[str] | None = None,
) -> dict:
    """List artifacts for review, filtered by project and ECS state.

    Default states: GENERATED (pending DEFINER review).
    Returns dict with 'artifacts' list or 'error'.
    """
    # Resolve project
    project = await _resolve_project(project_name, stores.project_store)
    if project is None:
        return {"error": {"code": "NOT_FOUND", "message": f"Project '{project_name}' not found"}}

    project_id = project.get("project_id", project_name)

    # Default: show GENERATED artifacts (those pending DEFINER review)
    if states is None:
        states = ["GENERATED"]

    # Collect artifact IDs in the requested states
    artifact_ids: set[str] = set()
    for state in states:
        ids = await stores.ecs_store.list_by_state(state)
        artifact_ids.update(ids)

    if not artifact_ids:
        return {"artifacts": [], "project": project_name}

    # Filter by project using artifact metadata
    results = []
    for aid in sorted(artifact_ids):
        try:
            metadata = await stores.artifact_store.read_metadata(aid)
        except KeyError:
            continue

        # Filter: only artifacts belonging to this project
        meta_project_id = metadata.get("project_id", "")
        meta_project_name = metadata.get("project_name", "")
        if meta_project_id != project_id and meta_project_name != project_name:
            continue

        # Get current ECS state
        ecs_state = await stores.ecs_store.current_state(aid) or "UNKNOWN"

        # Extract display fields from metadata
        source_ids = metadata.get("source_ids", [])
        results.append({
            "artifact_id": aid,
            "title": metadata.get("prompt", aid)[:80],
            "project": meta_project_name or meta_project_id,
            "lifecycle_state": ecs_state,
            "created_at": metadata.get("created_at", metadata.get("generated_at", "")),
            "source_count": len(source_ids),
            "session_id": metadata.get("session_id", ""),
            "model_slot": metadata.get("model_slot", ""),
            "model_name": metadata.get("model_name", ""),
            "artifact_type": metadata.get("artifact_type", ""),
        })

    return {"artifacts": results, "project": project_name}


# ---------------------------------------------------------------------------
# Review show
# ---------------------------------------------------------------------------


async def review_show(
    artifact_id: str,
    stores: ReviewExportStores,
) -> dict:
    """Show artifact details for review.

    Returns dict with artifact content, metadata, lifecycle state,
    review history, source count, and export eligibility.
    """
    # Read content + metadata
    try:
        content, metadata = await stores.artifact_store.read_with_metadata(artifact_id)
    except KeyError:
        return {"error": {"code": "NOT_FOUND", "message": f"Artifact '{artifact_id}' not found"}}

    # Get current ECS state
    ecs_state = await stores.ecs_store.current_state(artifact_id) or "UNKNOWN"

    # Get transition history (review notes/history)
    transition_history = await stores.ecs_store.get_transition_history(artifact_id)

    # Get review events
    review_events = await stores.event_store.query(
        artifact_id=artifact_id,
        event_type="review_verdict",
        limit=20,
    )

    # Build review notes from events
    review_notes = []
    for ev in review_events:
        meta = ev.metadata if hasattr(ev, "metadata") else {}
        if isinstance(meta, dict):
            note = meta.get("detail", "")
            verdict = meta.get("verdict", "")
            if note:
                review_notes.append({
                    "verdict": verdict,
                    "detail": note,
                    "actor": ev.actor,
                    "timestamp": ev.timestamp,
                })

    # Source count
    source_ids = metadata.get("source_ids", [])

    # Export eligibility
    export_eligible = ecs_state in ("GENERATED", "REVIEWED", "APPROVED")
    export_warn = ecs_state == "GENERATED"  # Warn for unreviewed
    export_blocked = ecs_state == "REJECTED"

    return {
        "artifact_id": artifact_id,
        "title": metadata.get("prompt", artifact_id)[:120],
        "project": metadata.get("project_name", metadata.get("project_id", "")),
        "lifecycle_state": ecs_state,
        "content": content,
        "prompt": metadata.get("prompt", ""),
        "model_slot": metadata.get("model_slot", ""),
        "model_name": metadata.get("model_name", ""),
        "model_provider": metadata.get("model_provider", ""),
        "generated_at": metadata.get("generated_at", metadata.get("created_at", "")),
        "session_id": metadata.get("session_id", ""),
        "artifact_type": metadata.get("artifact_type", ""),
        "source_count": len(source_ids),
        "review_notes": review_notes,
        "transition_history": transition_history,
        "export_eligible": export_eligible,
        "export_warn": export_warn,
        "export_blocked": export_blocked,
    }


# ---------------------------------------------------------------------------
# Review sources
# ---------------------------------------------------------------------------


async def review_sources(
    artifact_id: str,
    stores: ReviewExportStores,
) -> dict:
    """Show source/provenance links for an artifact.

    Returns the source_ids and source_types stored in the artifact metadata
    (populated by ``aip ask --save-artifact``), along with snippets and
    metadata needed to trace back to the original ingested content.
    """
    try:
        metadata = await stores.artifact_store.read_metadata(artifact_id)
    except KeyError:
        return {"error": {"code": "NOT_FOUND", "message": f"Artifact '{artifact_id}' not found"}}

    source_ids = metadata.get("source_ids", [])
    source_types = metadata.get("source_types", [])

    # Try to look up each source in the artifact store for more detail
    sources = []
    for i, sid in enumerate(source_ids):
        src_type = source_types[i] if i < len(source_types) else "unknown"

        # Try to find source content in the lexical store or artifact store
        snippet = ""
        source_title = sid
        source_score = 0.0
        source_meta: dict[str, Any] = {}

        # Try reading from artifact store (for ingested conversations)
        try:
            src_content = await stores.artifact_store.read(sid)
            snippet = src_content[:200] if src_content else ""
            try:
                src_metadata = await stores.artifact_store.read_metadata(sid)
                source_title = src_metadata.get("source_file", src_metadata.get("title", sid))
                source_meta = {
                    "conversation_id": src_metadata.get("conversation_id", ""),
                    "source_format": src_metadata.get("source_format", ""),
                    "domain": src_metadata.get("domain", ""),
                }
            except KeyError:
                pass
        except KeyError:
            # Source might be a chunk ID, not a top-level artifact
            # Try to find parent artifact
            if ":" in sid:
                parts = sid.split(":")
                if len(parts) >= 2:
                    parent_id = f"{parts[0]}:{parts[1]}"
                    try:
                        src_content = await stores.artifact_store.read(parent_id)
                        snippet = src_content[:200] if src_content else ""
                        src_metadata = await stores.artifact_store.read_metadata(parent_id)
                        source_title = src_metadata.get("source_file", src_metadata.get("title", parent_id))
                        source_meta = {
                            "conversation_id": src_metadata.get("conversation_id", ""),
                            "source_format": src_metadata.get("source_format", ""),
                            "domain": src_metadata.get("domain", ""),
                        }
                    except KeyError:
                        snippet = f"(source chunk: {sid})"
                        source_title = sid

        sources.append({
            "source_id": sid,
            "source_type": src_type,
            "title": source_title,
            "snippet": snippet[:200],
            "score": source_score,
            "metadata": source_meta,
        })

    return {
        "artifact_id": artifact_id,
        "source_count": len(sources),
        "sources": sources,
    }


# ---------------------------------------------------------------------------
# Review approve
# ---------------------------------------------------------------------------


async def review_approve(
    artifact_id: str,
    stores: ReviewExportStores,
    actor: str = "definer",
) -> dict:
    """Approve a generated artifact through the existing ECS lifecycle.

    Validates:
    1. Artifact exists.
    2. Artifact has non-empty content.
    3. Source links exist (warns if absent but allows approval for certain types).
    4. Uses existing ECS transition mechanism.

    Transition path:
    - GENERATED → REVIEWED → APPROVED (two transitions through existing gates)
    - If artifact is already REVIEWED: REVIEWED → APPROVED

    Writes to CanonicalStore on APPROVED transition (DEFINER sovereignty).
    Records event in EventStore.
    """
    # 1. Validate artifact exists
    try:
        content, metadata = await stores.artifact_store.read_with_metadata(artifact_id)
    except KeyError:
        return {"error": {"code": "NOT_FOUND", "message": f"Artifact '{artifact_id}' not found"}}

    # 2. Validate non-empty content
    if not content or not content.strip():
        return {"error": {"code": "EMPTY_CONTENT", "message": f"Artifact '{artifact_id}' has empty content — cannot approve"}}

    # 3. Check source links
    source_ids = metadata.get("source_ids", [])
    if not source_ids:
        artifact_type = metadata.get("artifact_type", "")
        # Allow source-less approval for certain artifact types
        # (e.g., manually created artifacts), but warn for ask_answer types
        if artifact_type == "ask_answer":
            return {
                "error": {
                    "code": "NO_SOURCES",
                    "message": (
                        f"Artifact '{artifact_id}' has no source links. "
                        "Generated answers must have source grounding for approval. "
                        "Reject or request revision instead."
                    ),
                },
            }

    # 4. Get current state and transition
    current_state = await stores.ecs_store.current_state(artifact_id)

    if current_state is None:
        return {"error": {"code": "NO_ECS_STATE", "message": f"Artifact '{artifact_id}' has no ECS state recorded"}}

    try:
        if current_state == "GENERATED":
            # GENERATED → REVIEWED (DEFINER manual quality gate pass)
            await stores.ecs_store.transition(
                artifact_id=artifact_id,
                from_state="GENERATED",
                to_state="REVIEWED",
                actor=actor,
                reason="DEFINER manual approval — quality gate passed via CLI review",
            )

            # REVIEWED → APPROVED (DEFINER canonical promotion)
            await stores.ecs_store.transition(
                artifact_id=artifact_id,
                from_state="REVIEWED",
                to_state="APPROVED",
                actor=actor,
                reason="DEFINER approved — promoted to canonical",
            )

        elif current_state == "REVIEWED":
            # Already reviewed, just promote to APPROVED
            await stores.ecs_store.transition(
                artifact_id=artifact_id,
                from_state="REVIEWED",
                to_state="APPROVED",
                actor=actor,
                reason="DEFINER approved — promoted to canonical",
            )
        elif current_state == "APPROVED":
            return {"error": {"code": "ALREADY_APPROVED", "message": f"Artifact '{artifact_id}' is already APPROVED"}}
        elif current_state == "REJECTED":
            return {"error": {"code": "INVALID_TRANSITION", "message": f"Artifact '{artifact_id}' is REJECTED — re-generate before approving"}}
        else:
            return {"error": {"code": "INVALID_STATE", "message": f"Artifact '{artifact_id}' is in {current_state} state — cannot approve from this state"}}

    except InvalidTransitionError as exc:
        return {"error": {"code": "INVALID_TRANSITION", "message": str(exc)}}

    # 5. Write to CanonicalStore (DEFINER sovereignty)
    from aip.adapter.canonical.sqlite_canonical_store import SqliteCanonicalStore
    canonical_store = SqliteCanonicalStore(stores.artifact_store._db_path)
    await canonical_store.initialize()
    try:
        await canonical_store.write_canonical(
            artifact_id=artifact_id,
            content={"text": content, "metadata": metadata},
            approved_by="definer",
        )
    finally:
        await canonical_store.close()

    # 6. Record review event
    await stores.event_store.write_event(
        event_type="review_verdict",
        actor=actor,
        artifact_id=artifact_id,
        from_state=current_state,
        to_state="APPROVED",
        verdict="APPROVED",
        detail=f"DEFINER approved via CLI review — promoted to canonical",
        source_count=len(source_ids),
    )

    new_state = await stores.ecs_store.current_state(artifact_id)

    return {
        "artifact_id": artifact_id,
        "lifecycle_state": new_state,
        "previous_state": current_state,
        "actor": actor,
        "canonical_written": True,
    }


# ---------------------------------------------------------------------------
# Review reject
# ---------------------------------------------------------------------------


async def review_reject(
    artifact_id: str,
    stores: ReviewExportStores,
    note: str = "",
    actor: str = "definer",
) -> dict:
    """Reject a generated artifact. Preserves artifact and source links.

    Transition: GENERATED → REJECTED or REVIEWED → REJECTED.
    Records reviewer note in EventStore.
    Does NOT delete the artifact.
    """
    # Validate artifact exists
    try:
        content, metadata = await stores.artifact_store.read_with_metadata(artifact_id)
    except KeyError:
        return {"error": {"code": "NOT_FOUND", "message": f"Artifact '{artifact_id}' not found"}}

    current_state = await stores.ecs_store.current_state(artifact_id)

    if current_state is None:
        return {"error": {"code": "NO_ECS_STATE", "message": f"Artifact '{artifact_id}' has no ECS state recorded"}}

    if current_state == "REJECTED":
        return {"error": {"code": "ALREADY_REJECTED", "message": f"Artifact '{artifact_id}' is already REJECTED"}}

    try:
        await stores.ecs_store.transition(
            artifact_id=artifact_id,
            from_state=current_state,
            to_state="REJECTED",
            actor=actor,
            reason=f"DEFINER rejected via CLI review: {note}" if note else "DEFINER rejected via CLI review",
        )
    except InvalidTransitionError as exc:
        return {"error": {"code": "INVALID_TRANSITION", "message": str(exc)}}

    # Record rejection event with note
    await stores.event_store.write_event(
        event_type="review_verdict",
        actor=actor,
        artifact_id=artifact_id,
        from_state=current_state,
        to_state="REJECTED",
        verdict="REJECTED",
        detail=note or "DEFINER rejected via CLI review",
        rejection_note=note,
    )

    new_state = await stores.ecs_store.current_state(artifact_id)

    return {
        "artifact_id": artifact_id,
        "lifecycle_state": new_state,
        "previous_state": current_state,
        "actor": actor,
        "note": note,
        "artifact_preserved": True,
    }


# ---------------------------------------------------------------------------
# Review needs-revision
# ---------------------------------------------------------------------------


async def review_needs_revision(
    artifact_id: str,
    stores: ReviewExportStores,
    instruction: str = "",
    actor: str = "definer",
) -> dict:
    """Mark artifact as needing revision. Preserves artifact and source links.

    The artifact stays in its current ECS state (typically GENERATED).
    The revision instruction is stored as a review event.
    No ECS transition occurs — NEEDS_REVISION is a verdict, not a state.
    The artifact can be re-generated or re-asked later.
    """
    # Validate artifact exists
    try:
        content, metadata = await stores.artifact_store.read_with_metadata(artifact_id)
    except KeyError:
        return {"error": {"code": "NOT_FOUND", "message": f"Artifact '{artifact_id}' not found"}}

    current_state = await stores.ecs_store.current_state(artifact_id)

    if current_state is None:
        return {"error": {"code": "NO_ECS_STATE", "message": f"Artifact '{artifact_id}' has no ECS state recorded"}}

    # Store the revision instruction as a review event (no ECS transition)
    await stores.event_store.write_event(
        event_type="review_verdict",
        actor=actor,
        artifact_id=artifact_id,
        from_state=current_state,
        to_state=current_state,  # State doesn't change
        verdict="NEEDS_REVISION",
        detail=instruction or "DEFINER requests revision via CLI review",
        revision_instruction=instruction,
    )

    return {
        "artifact_id": artifact_id,
        "lifecycle_state": current_state,  # Unchanged
        "actor": actor,
        "instruction": instruction,
        "artifact_preserved": True,
    }


# ---------------------------------------------------------------------------
# Export artifact
# ---------------------------------------------------------------------------


async def export_artifact(
    artifact_id: str,
    out_path: str,
    stores: ReviewExportStores,
    format: str = "markdown",
    force: bool = False,
) -> dict:
    """Export an artifact to markdown with metadata and provenance footer.

    Refuses to export REJECTED artifacts by default.
    Warns for unapproved (GENERATED/REVIEWED) artifacts unless force=True.
    Creates parent directories if needed.
    Never silently writes an empty file.
    """
    # Validate artifact exists
    try:
        content, metadata = await stores.artifact_store.read_with_metadata(artifact_id)
    except KeyError:
        return {"error": {"code": "NOT_FOUND", "message": f"Artifact '{artifact_id}' not found"}}

    # Validate non-empty content
    if not content or not content.strip():
        return {"error": {"code": "EMPTY_CONTENT", "message": f"Artifact '{artifact_id}' has empty content — refusing to export empty file"}}

    # Check lifecycle state
    ecs_state = await stores.ecs_store.current_state(artifact_id) or "UNKNOWN"

    if ecs_state == "REJECTED" and not force:
        return {
            "error": {
                "code": "REJECTED_ARTIFACT",
                "message": (
                    f"Artifact '{artifact_id}' is REJECTED. "
                    "Refusing to export rejected artifact by default. "
                    "Use --force to override."
                ),
            },
        }

    if ecs_state in ("GENERATED", "REVIEWED") and not force:
        return {
            "error": {
                "code": "UNREVIEWED_ARTIFACT",
                "message": (
                    f"Artifact '{artifact_id}' is in {ecs_state} state (not yet approved). "
                    "Use --force to export unapproved artifacts."
                ),
            },
        }

    # Build markdown output
    md = _build_artifact_markdown(artifact_id, content, metadata, ecs_state)

    # Create parent directories
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # Write file
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)
    except OSError as exc:
        return {"error": {"code": "WRITE_FAILED", "message": f"Failed to write to '{out_path}': {exc}"}}

    # Verify the file was written
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        return {"error": {"code": "WRITE_FAILED", "message": f"File '{out_path}' was not written successfully"}}

    return {
        "artifact_id": artifact_id,
        "out_path": out_path,
        "format": format,
        "lifecycle_state": ecs_state,
        "bytes_written": os.path.getsize(out_path),
    }


def _build_artifact_markdown(
    artifact_id: str,
    content: str,
    metadata: dict,
    ecs_state: str,
) -> str:
    """Build a markdown document from artifact content and metadata."""
    lines = []

    # Frontmatter
    lines.append("---")
    lines.append(f"artifact_id: {artifact_id}")
    lines.append(f"project: {metadata.get('project_name', metadata.get('project_id', ''))}")
    lines.append(f"lifecycle_state: {ecs_state}")
    lines.append(f"created_at: {metadata.get('generated_at', metadata.get('created_at', ''))}")
    if metadata.get("prompt"):
        lines.append(f"originating_prompt: {metadata['prompt'][:200]}")
    if metadata.get("session_id"):
        lines.append(f"session_id: {metadata['session_id']}")
    if metadata.get("model_slot"):
        lines.append(f"model_slot: {metadata['model_slot']}")
    if metadata.get("model_name"):
        lines.append(f"model_provider: {metadata['model_name']}")
    source_ids = metadata.get("source_ids", [])
    lines.append(f"source_count: {len(source_ids)}")
    lines.append("---")
    lines.append("")

    # Content
    lines.append(content)
    lines.append("")

    # Provenance footer
    lines.append("---")
    lines.append("**Provenance**")
    lines.append("")
    if source_ids:
        source_types = metadata.get("source_types", [])
        for i, sid in enumerate(source_ids):
            stype = source_types[i] if i < len(source_types) else "unknown"
            lines.append(f"- Source {i + 1}: `{sid}` (type: {stype})")
    else:
        lines.append("- No source links recorded")
    lines.append("")
    lines.append(f"Exported from AIP at {datetime.now(timezone.utc).isoformat()}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Export project
# ---------------------------------------------------------------------------


async def export_project(
    project_name: str,
    out_path: str,
    stores: ReviewExportStores,
    format: str = "markdown",
    include_unreviewed: bool = False,
) -> dict:
    """Export approved/canonical artifacts for a project.

    By default:
    - Includes APPROVED artifacts only.
    - Excludes REJECTED artifacts.
    - Excludes GENERATED/unreviewed artifacts unless explicitly requested.

    Produces a single markdown bundle with an index section.
    Creates parent directories if needed.
    """
    # Resolve project
    project = await _resolve_project(project_name, stores.project_store)
    if project is None:
        return {"error": {"code": "NOT_FOUND", "message": f"Project '{project_name}' not found"}}

    project_id = project.get("project_id", project_name)

    # Collect eligible states
    eligible_states = ["APPROVED"]
    if include_unreviewed:
        eligible_states.extend(["GENERATED", "REVIEWED"])

    # Find artifacts in eligible states
    artifact_ids: set[str] = set()
    for state in eligible_states:
        ids = await stores.ecs_store.list_by_state(state)
        artifact_ids.update(ids)

    # Filter by project
    artifacts = []
    for aid in sorted(artifact_ids):
        try:
            metadata = await stores.artifact_store.read_metadata(aid)
        except KeyError:
            continue

        meta_project_id = metadata.get("project_id", "")
        meta_project_name = metadata.get("project_name", "")
        if meta_project_id != project_id and meta_project_name != project_name:
            continue

        # Skip REJECTED artifacts always
        ecs_state = await stores.ecs_store.current_state(aid) or "UNKNOWN"
        if ecs_state == "REJECTED":
            continue

        try:
            content = await stores.artifact_store.read(aid)
        except KeyError:
            continue

        if not content or not content.strip():
            continue

        artifacts.append({
            "artifact_id": aid,
            "content": content,
            "metadata": metadata,
            "ecs_state": ecs_state,
        })

    if not artifacts:
        return {
            "project": project_name,
            "artifacts_exported": 0,
            "out_path": out_path,
            "message": f"No approved artifacts found for project '{project_name}'",
        }

    # Build markdown bundle
    lines = []
    lines.append(f"# Project: {project_name}")
    lines.append("")
    lines.append(f"Exported: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Total artifacts: {len(artifacts)}")
    lines.append("")

    # Index section
    lines.append("## Artifact Index")
    lines.append("")
    for i, art in enumerate(artifacts, 1):
        title = art["metadata"].get("prompt", art["artifact_id"])[:80]
        lines.append(f"{i}. `{art['artifact_id']}` — {title} [{art['ecs_state']}]")
    lines.append("")

    # Each artifact
    for art in artifacts:
        lines.append("---")
        lines.append("")
        lines.append(f"## Artifact: {art['artifact_id']}")
        lines.append("")
        lines.append(_build_artifact_markdown(
            art["artifact_id"],
            art["content"],
            art["metadata"],
            art["ecs_state"],
        ))
        lines.append("")

    # Create parent directories
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # Write file
    md = "\n".join(lines)
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)
    except OSError as exc:
        return {"error": {"code": "WRITE_FAILED", "message": f"Failed to write to '{out_path}': {exc}"}}

    # Verify
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        return {"error": {"code": "WRITE_FAILED", "message": f"File '{out_path}' was not written successfully"}}

    return {
        "project": project_name,
        "artifacts_exported": len(artifacts),
        "out_path": out_path,
        "format": format,
        "bytes_written": os.path.getsize(out_path),
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
