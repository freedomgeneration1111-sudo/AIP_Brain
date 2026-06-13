"""ECS (Entity Component System) API routes — artifact lifecycle visualization.

Exposes the ECS state graph and artifact lifecycle data via REST endpoints
so the GUI can render the artifact pipeline graph, browse artifacts by
state, and inspect individual artifact histories.

The ECS state machine enforces valid transitions:
  SPECIFIED → GENERATED → REVIEWED → APPROVED → SUPERSEDED
                 ↓            ↓
             REJECTED ←  REJECTED
                 ↓
             GENERATED (re-synthesis loop)
  FAILED → SPECIFIED (re-specify after failure)

"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from aip.adapter.api.dependencies import AipContainer, get_container
from aip.foundation.ecs_graph import ALL_STATES, VALID_TRANSITIONS

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/ecs/graph")
async def get_ecs_graph(container: AipContainer = Depends(get_container)):
    """Return the ECS state graph definition and artifact distribution.

    Returns:
      - transitions: The full VALID_TRANSITIONS graph
      - all_states: Set of all known states
      - distribution: Count of artifacts in each state
    """
    distribution: dict[str, int] = {state: 0 for state in ALL_STATES}

    if container.ecs_store is not None:
        try:
            # Count artifacts in each state
            for state in ALL_STATES:
                if hasattr(container.ecs_store, "list_by_state"):
                    artifact_ids = await container.ecs_store.list_by_state(state)
                    distribution[state] = len(artifact_ids)
        except Exception as exc:
            logger.warning("Failed to compute ECS distribution: %s", exc)

    # Build serializable transitions
    transitions: dict[str, list[str]] = {
        from_state: sorted(list(to_states)) for from_state, to_states in VALID_TRANSITIONS.items()
    }

    return {
        "transitions": transitions,
        "all_states": sorted(list(ALL_STATES)),
        "distribution": distribution,
    }


@router.get("/ecs/artifacts/{artifact_id}")
async def get_ecs_artifact(
    artifact_id: str,
    container: AipContainer = Depends(get_container),
):
    """Get the ECS state and transition history for a specific artifact.

    Returns:
      - artifact_id: The artifact identifier
      - current_state: Current ECS state (or null if not tracked)
      - history: List of transition records (from_state, to_state, actor, reason, timestamp)
    """
    current_state = None
    history: list[dict[str, Any]] = []

    if container.ecs_store is not None:
        try:
            current_state = await container.ecs_store.current_state(artifact_id)
        except Exception as exc:
            logger.warning("Failed to get current state for artifact '%s': %s", artifact_id, exc)

        try:
            if hasattr(container.ecs_store, "get_transition_history"):
                history = await container.ecs_store.get_transition_history(artifact_id)
        except Exception as exc:
            logger.warning("Failed to get history for artifact '%s': %s", artifact_id, exc)

    return {
        "artifact_id": artifact_id,
        "current_state": current_state,
        "history": history,
    }


@router.get("/ecs/artifacts")
async def list_ecs_artifacts(
    state: str | None = None,
    container: AipContainer = Depends(get_container),
):
    """List artifacts tracked by the ECS store, optionally filtered by state.

    When state is provided, returns only artifact IDs currently in that state.
    When no state is given, returns a summary of all states with artifact counts.
    """
    if container.ecs_store is None:
        raise HTTPException(status_code=503, detail="ECS store not available")

    if state is not None:
        if state not in ALL_STATES:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown state '{state}'. Valid states: {sorted(list(ALL_STATES))}",
            )
        try:
            artifact_ids = await container.ecs_store.list_by_state(state)
            return {"state": state, "artifact_ids": artifact_ids, "count": len(artifact_ids)}
        except Exception as exc:
            logger.error("Failed to list artifacts for state '%s': %s", state, exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # No state filter — return summary
    summary: dict[str, Any] = {}
    total = 0
    for s in ALL_STATES:
        try:
            ids = await container.ecs_store.list_by_state(s)
            summary[s] = {"count": len(ids), "sample_ids": ids[:10]}
            total += len(ids)
        except Exception as exc:
            logger.warning("ecs_list_by_state_failed", state=s, error=str(exc))
            summary[s] = {"count": 0, "sample_ids": [], "error": True}

    return {"summary": summary, "total_artifacts": total}
