"""Turn artifact save endpoint.

Provides ``POST /api/v1/turns/save-artifact`` for persisting chat turn
content as a versioned artifact with ECS state management.  Artifacts
are saved in GENERATED state (NOT APPROVED) — they require DEFINER
review before approval.

Layer discipline: This module imports ONLY from adapter and foundation.
Store access is through the container, not via direct orchestration imports.
"""

from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, Depends, HTTPException

from aip.adapter.api.dependencies import AipContainer, get_container

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/turns/save-artifact")
async def save_turn_artifact(
    payload: dict,
    container: AipContainer = Depends(get_container),
):
    """Save a chat turn as a versioned artifact.

    Creates an artifact using the VersionedArtifactStore, transitions
    its ECS state to GENERATED (NOT APPROVED — no auto-approve), and
    indexes it in LexicalStore if available.

    Accepts:
      - session_id (str, required): The chat session ID
      - content (str, required): The content to save as artifact
      - title (str, optional): Artifact title
      - domain (str, optional): Artifact domain (default: "chat")

    Returns the artifact ID, ECS state, and a message noting that
    DEFINER review is required before approval.

    Returns 503 if the required stores (artifact_store, ecs_store) are
    not available.
    """
    session_id = payload.get("session_id", "").strip()
    content = payload.get("content", "").strip()
    title = payload.get("title", "").strip() or None
    domain = payload.get("domain", "chat").strip() or "chat"

    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    if not content:
        raise HTTPException(status_code=400, detail="content is required")

    # Validate required stores
    if container.artifact_store is None:
        raise HTTPException(
            status_code=503,
            detail="Artifact store not available — cannot save artifact.",
        )
    if container.ecs_store is None:
        raise HTTPException(
            status_code=503,
            detail="ECS store not available — cannot manage artifact lifecycle.",
        )

    # Generate deterministic artifact ID
    artifact_id = f"turn:{hashlib.sha256((session_id + content).encode()).hexdigest()[:24]}"

    try:
        # Create the artifact via VersionedArtifactStore
        artifact_metadata = {
            "artifact_type": "chat_turn",
            "session_id": session_id,
            "domain": domain,
        }
        if title:
            artifact_metadata["title"] = title

        await container.artifact_store.put(
            artifact_id=artifact_id,
            content=content,
            metadata=artifact_metadata,
        )
        logger.info(
            "turn_artifact_created",
            artifact_id=artifact_id,
            session_id=session_id,
            domain=domain,
        )
    except Exception as exc:
        logger.error("Failed to create turn artifact: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create artifact: {exc}",
        ) from exc

    # Transition ECS state to GENERATED (NOT APPROVED)
    try:
        await container.ecs_store.transition(
            artifact_id=artifact_id,
            new_state="GENERATED",
            actor="system:turn_save",
            reason="Chat turn saved as artifact — requires DEFINER review",
        )
        logger.info(
            "turn_artifact_ecs_transition",
            artifact_id=artifact_id,
            state="GENERATED",
        )
    except Exception as exc:
        logger.warning(
            "turn_artifact_ecs_transition_failed",
            artifact_id=artifact_id,
            error=str(exc),
        )
        # Artifact was created but ECS transition failed — still return success
        # since the artifact exists; just note the ECS issue.

    # Index in LexicalStore if available
    if container.lexical_store is not None:
        try:
            await container.lexical_store.index_content(
                artifact_id=artifact_id,
                content=content,
                metadata={
                    "artifact_type": "chat_turn",
                    "session_id": session_id,
                    "domain": domain,
                },
            )
            logger.debug(
                "turn_artifact_indexed",
                artifact_id=artifact_id,
            )
        except Exception as exc:
            logger.debug(
                "turn_artifact_indexing_failed",
                artifact_id=artifact_id,
                error=str(exc),
            )
            # Non-critical — indexing is advisory

    return {
        "artifact_id": artifact_id,
        "ecs_state": "GENERATED",
        "message": "Artifact saved — requires DEFINER review before approval",
    }
