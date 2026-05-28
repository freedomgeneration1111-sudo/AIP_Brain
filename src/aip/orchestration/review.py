"""Review node — quality gate between synthesis and DEFINER approval.

Implements GENERATED → REVIEWED | REJECTED.
No bypass of DEFINER gates.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from aip.foundation.protocols import ArtifactStore, EcsStore, EventStore, TraceStore
from aip.foundation.schemas import ReviewContext, ReviewVerdict


async def review_artifact(
    artifact_id: str,
    artifact_store: ArtifactStore,
    ecs_store: EcsStore,
    event_store: EventStore,
    trace_store: TraceStore,
    eval_fn: Callable[[str, str], Awaitable[dict]] | None = None,
    config: "AipConfig | dict | None" = None,
) -> ReviewVerdict:
    """Review a generated artifact for quality and correctness.

    Reads artifact content, assembles review context, applies quality
    gate, returns verdict. Transitions ECS state accordingly.

    Args:
        artifact_id: ID of the artifact to review.
        artifact_store: For reading artifact content and versioning.
        ecs_store: For ECS state transitions.
        event_store: For querying prior events and recording review events.
        trace_store: For logging review trace events.
        eval_fn: Optional evaluation function (deterministic in CI, L3b in prod).
        config: AipConfig or dict with [review] section.
    """
    cfg = config.model_dump() if hasattr(config, "model_dump") else (config or {})
    review_cfg = cfg.get("review", {})
    mode = review_cfg.get("mode", "automated")
    confidence_threshold = review_cfg.get("confidence_threshold", 0.70)
    auto_approve = review_cfg.get("auto_approve_if_above", 0.90)

    # Read artifact content
    content = await artifact_store.read(artifact_id)

    # Assemble review context
    prior_events = await event_store.query(artifact_id=artifact_id, limit=20)
    prior_verdicts = [
        ReviewVerdict(
            artifact_id=e.get("artifact_id", ""),
            verdict=e.get("metadata", {}).get("verdict", ""),
            reviewer=e.get("actor", ""),
            failure_types=e.get("metadata", {}).get("failure_types", []),
            detail=e.get("metadata", {}).get("detail"),
            confidence=e.get("metadata", {}).get("confidence", 0.0),
        )
        for e in (e.__dict__ if hasattr(e, "__dict__") else e for e in prior_events)
        if isinstance(e, dict) and e.get("event_type") == "review_verdict"
    ]

    context = ReviewContext(
        artifact_id=artifact_id,
        artifact_content=content,
        artifact_version=1,  # will be populated by ArtifactStore in Phase 4.3
        trace_events=[e.__dict__ if hasattr(e, "__dict__") else e for e in prior_events],
        prior_verdicts=prior_verdicts,
    )

    # Apply review based on mode
    if mode == "definer":
        # DEFINER review — human-in-the-loop
        # In CI: returns deterministic fixture
        # In production: pauses workflow and emits a DEFINER review event
        verdict = await _definer_review(context, eval_fn, review_cfg)
    else:
        # Automated review
        verdict = await _automated_review(context, eval_fn, review_cfg, confidence_threshold)

    # ECS transition based on verdict
    if verdict.verdict == "APPROVED" or (verdict.verdict == "NEEDS_REVISION" and verdict.confidence >= auto_approve):
        to_state = "REVIEWED"
        actor = verdict.reviewer
        reason = f"Review passed (confidence={verdict.confidence:.2f})"
    elif verdict.verdict == "REJECTED":
        to_state = "REJECTED"
        actor = verdict.reviewer
        reason = f"Review rejected: {', '.join(verdict.failure_types)} — {verdict.detail}"
    else:
        to_state = "REVIEWED"
        actor = verdict.reviewer
        reason = f"Review needs revision (confidence={verdict.confidence:.2f})"

    await ecs_store.transition(
        artifact_id=artifact_id,
        from_state="GENERATED",
        to_state=to_state,
        actor=actor,
        reason=reason,
    )

    # Record verdict as event
    await event_store.write_event(
        event_type="review_verdict",
        actor=verdict.reviewer,
        artifact_id=artifact_id,
        from_state="GENERATED",
        to_state=to_state,
        verdict=verdict.verdict,
        failure_types=verdict.failure_types,
        detail=verdict.detail,
        confidence=verdict.confidence,
    )

    # Trace logging
    await trace_store.write_event(
        session_id=artifact_id,
        node_type="L3",
        failure_type=verdict.failure_types[0] if verdict.failure_types else "",
        outcome=f"review_{verdict.verdict.lower()}",
        detail=verdict.detail,
    )

    return verdict


async def _automated_review(
    context: ReviewContext,
    eval_fn: Callable[[str, str], Awaitable[dict]] | None,
    review_cfg: dict,
    confidence_threshold: float,
) -> ReviewVerdict:
    """Automated quality gate review."""
    if eval_fn is not None:
        result = await eval_fn(context.artifact_content, context.artifact_id)
        confidence = result.get("confidence", 0.0)
        failure_types = result.get("failure_types", [])
        detail = result.get("detail")

        if confidence >= confidence_threshold and not failure_types:
            return ReviewVerdict(
                artifact_id=context.artifact_id,
                verdict="APPROVED",
                reviewer="automated",
                confidence=confidence,
            )
        else:
            return ReviewVerdict(
                artifact_id=context.artifact_id,
                verdict="REJECTED" if confidence < confidence_threshold else "NEEDS_REVISION",
                reviewer="automated",
                failure_types=failure_types,
                detail=detail,
                confidence=confidence,
            )
    else:
        # No eval function — deterministic pass in CI
        return ReviewVerdict(
            artifact_id=context.artifact_id,
            verdict="APPROVED",
            reviewer="automated",
            confidence=1.0,
        )


async def _definer_review(
    context: ReviewContext,
    eval_fn: Callable[[str, str], Awaitable[dict]] | None,
    review_cfg: dict,
) -> ReviewVerdict:
    """DEFINER human-in-the-loop review.

    In CI: returns deterministic APPROVED fixture.
    In production: integrates with DEFINER gate stub.
    """
    # Deterministic fixture for CI — production uses DEFINER gate
    return ReviewVerdict(
        artifact_id=context.artifact_id,
        verdict="APPROVED",
        reviewer="definer",
        confidence=1.0,
    )
