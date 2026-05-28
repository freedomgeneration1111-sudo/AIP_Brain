"""
Commit stub (CHUNK-1.6 per Rev 1.3).

Writes artifact, performs ECS transition with correct actor/reason (P2),
and records the transition in the event log (R3).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from aip.foundation.protocols import ArtifactStore, EcsStore, EventStore
from aip.orchestration.nodes.definer_gate import DefinerDecision
from aip.orchestration.nodes.synthesis import SynthesisOutput


@dataclass
class ArtifactRef:
    artifact_id: str
    project_id: str
    work_unit_id: str
    ecs_state: str


class CommitBlockedError(Exception):
    """Raised when the DEFINER decision does not allow commit."""


async def commit_artifact(
    synthesis: SynthesisOutput,
    decision: DefinerDecision,
    project_id: str,
    work_unit_id: str,
    artifact_store: ArtifactStore,
    ecs_store: EcsStore,
    event_store: EventStore,
) -> ArtifactRef:
    """
    Commit stub per CHUNK-1.6.

    - Only proceeds if decision.action == "approve"
    - Generates deterministic artifact_id
    - Writes to ArtifactStore
    - Calls ecs_store.transition with actor="definer_gate" and reason="DEFINER approved" (P2)
    - Records the transition in event_store (R3)
    """
    if decision.action != "approve":
        raise CommitBlockedError(f"DEFINER decision was '{decision.action}'")

    # Deterministic artifact ID (per §1.5 provenance)
    content_for_id = f"{project_id}:{work_unit_id}:{synthesis.content}"
    artifact_id = hashlib.sha256(content_for_id.encode()).hexdigest()[:32]

    # Write the artifact
    metadata = {
        "project_id": project_id,
        "work_unit_id": work_unit_id,
        "model_slot": synthesis.model_slot,
        "model_name": synthesis.model_name,
        "token_count_in": synthesis.token_count_in,
        "token_count_out": synthesis.token_count_out,
    }

    if artifact_store is not None:
        await artifact_store.write(artifact_id, synthesis.content, metadata)

    from_state = "SPECIFIED"   # typical starting state before commit
    to_state = "GENERATED"

    # P2 fix: must pass actor and reason
    if ecs_store is not None:
        await ecs_store.transition(
            artifact_id=artifact_id,
            from_state=from_state,
            to_state=to_state,
            actor="definer_gate",
            reason="DEFINER approved",
        )

    # R3: record the ECS transition in the event log
    if event_store is not None:
        await event_store.write_event(
            event_type="ecs_transition",
            actor="definer_gate",
            artifact_id=artifact_id,
            from_state=from_state,
            to_state=to_state,
            reason="DEFINER approved",
        )

    return ArtifactRef(
        artifact_id=artifact_id,
        project_id=project_id,
        work_unit_id=work_unit_id,
        ecs_state=to_state,
    )
