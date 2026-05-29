"""Re-synthesis loop — REJECTED → GENERATED with failure context injection.

Implements the rejection correction cycle and Appendix E.
Different failure types produce different correction instructions.
Retry budget from config prevents infinite loops.
DEFINER notified when budget exhausted.
"""

from __future__ import annotations

from typing import Callable

from aip.foundation.protocols import ArtifactStore, EcsStore, EventStore, TraceStore
from aip.foundation.schemas import ReviewVerdict

# Correction instructions per failure type (Appendix E)
_CORRECTION_INSTRUCTIONS = {
    "A": "The previous synthesis lacked sufficient context. "
    "Expand retrieval scope and include additional source material. "
    "Ensure all relevant domain knowledge is represented.",
    "B": "A known procedural playbook entry was not applied. "
    "Retrieve and follow the established procedure for this task type. "
    "Do not improvise when a known-good procedure exists.",
    "C": "The output did not conform to the required format. "
    "Follow the template structure exactly. "
    "Ensure all required sections, markers, and schema fields are present.",
    "E": "The model claimed completion but the result was incomplete. "
    "Do NOT report completion until all required deliverables are present. "
    "Include a self-verification step before finalizing.",
}


def build_failure_context(rejection: ReviewVerdict, prior_content: str) -> dict:
    """Build failure context dict from rejection verdict.

    Maps failure types to correction instructions.
    """
    instructions = []
    for ft in rejection.failure_types:
        instruction = _CORRECTION_INSTRUCTIONS.get(ft, f"Address failure type {ft}.")
        instructions.append(instruction)

    return {
        "failure_types": rejection.failure_types,
        "rejection_detail": rejection.detail,
        "prior_content": prior_content,
        "correction_instructions": instructions,
    }


async def re_synthesize(
    artifact_id: str,
    rejection: ReviewVerdict,
    artifact_store: ArtifactStore,
    ecs_store: EcsStore,
    event_store: EventStore,
    trace_store: TraceStore,
    synthesize_fn: Callable,
    config: "AipConfig | dict | None" = None,  # noqa: F821
) -> ReviewVerdict:
    """Re-synthesize a rejected artifact with failure context injection.

    1. Read prior artifact content
    2. Build failure context from rejection verdict
    3. Call synthesis function with failure context
    4. Transition REJECTED → GENERATED
    5. Re-enter review cycle
    6. If retry budget exhausted, transition to FAILED

    Args:
        artifact_id: ID of the rejected artifact.
        rejection: The ReviewVerdict that triggered re-synthesis.
        artifact_store: For reading/writing artifact versions.
        ecs_store: For ECS state transitions.
        event_store: For recording events.
        trace_store: For logging trace events.
        synthesize_fn: The synthesis function (stub in CI).
        config: AipConfig or dict with [review] section.
    """
    cfg = config.model_dump() if hasattr(config, "model_dump") else (config or {})
    review_cfg = cfg.get("review", {})
    max_retries = review_cfg.get("max_rejection_retries", 3)

    # Read prior content
    prior_content = await artifact_store.read(artifact_id)

    # Build failure context
    failure_context = build_failure_context(rejection, prior_content)

    # Check retry budget
    events = await event_store.query(artifact_id=artifact_id, event_type="re_synthesis_attempt")
    retry_count = len(events)

    if retry_count >= max_retries:
        # Budget exhausted — REJECTED can only go to GENERATED first,
        # then GENERATED→FAILED. Two-step transition.
        await ecs_store.transition(
            artifact_id=artifact_id,
            from_state="REJECTED",
            to_state="GENERATED",
            actor="re_synthesize",
            reason="Retry budget exhausted, transitioning to GENERATED before FAILED.",
        )
        await ecs_store.transition(
            artifact_id=artifact_id,
            from_state="GENERATED",
            to_state="FAILED",
            actor="re_synthesize",
            reason="retry_budget_exhausted",
        )
        await trace_store.write_event(
            session_id=artifact_id,
            node_type="L3",
            failure_type=rejection.failure_types[0] if rejection.failure_types else "",
            outcome="retry_budget_exhausted",
            detail=f"Max retries ({max_retries}) reached. Last rejection: {rejection.detail}",
        )
        return ReviewVerdict(
            artifact_id=artifact_id,
            verdict="REJECTED",
            reviewer="re_synthesize",
            failure_types=rejection.failure_types,
            detail=f"Retry budget exhausted after {retry_count} attempts.",
            confidence=0.0,
        )

    # Record re-synthesis attempt
    await event_store.write_event(
        event_type="re_synthesis_attempt",
        actor="re_synthesize",
        artifact_id=artifact_id,
        from_state="REJECTED",
        to_state="GENERATED",
        retry_number=retry_count + 1,
        failure_types=rejection.failure_types,
    )

    # Call synthesis with failure context
    new_content = await synthesize_fn(
        artifact_id=artifact_id,
        failure_context=failure_context,
    )

    # Write new version
    await artifact_store.write(
        artifact_id,
        new_content,
        metadata={
            "version_reason": "re_synthesis",
            "retry_number": retry_count + 1,
            "failure_types": rejection.failure_types,
        },
    )

    # Transition REJECTED → GENERATED
    await ecs_store.transition(
        artifact_id=artifact_id,
        from_state="REJECTED",
        to_state="GENERATED",
        actor="re_synthesize",
        reason=f"Re-synthesis attempt {retry_count + 1} with failure context: {', '.join(rejection.failure_types)}",
    )

    await trace_store.write_event(
        session_id=artifact_id,
        node_type="L3",
        failure_type="",
        outcome="re_synthesis_initiated",
        detail=f"Attempt {retry_count + 1}/{max_retries}. Failure types: {', '.join(rejection.failure_types)}",
    )

    # Return the verdict — the workflow engine will re-enter the review cycle
    return ReviewVerdict(
        artifact_id=artifact_id,
        verdict="NEEDS_REVISION",
        reviewer="re_synthesize",
        failure_types=[],
        detail=f"Re-synthesis attempt {retry_count + 1} initiated.",
        confidence=0.0,
    )
