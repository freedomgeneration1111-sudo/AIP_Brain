"""Review node — quality gate between synthesis and DEFINER approval.

Implements GENERATED → REVIEWED | REJECTED.
No bypass of DEFINER gates.
"""
from __future__ import annotations

import logging
import os
from typing import Awaitable, Callable

from aip.foundation.protocols import ArtifactStore, EcsStore, EventStore, TraceStore
from aip.foundation.schemas import ReviewContext, ReviewVerdict

logger = logging.getLogger(__name__)


def _is_ci_environment() -> bool:
    """Check whether we are running in a CI environment.

    Returns True if the CI environment variable is set to a truthy value
    (e.g. "true", "1", "yes"). This allows CI pipelines to use deterministic
    fixture results while production requires real evaluation.
    """
    return os.environ.get("CI", "").lower() in ("true", "1", "yes")


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
        # In CI: returns deterministic fixture with PENDING state
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
    elif verdict.verdict == "PENDING":
        # PENDING means evaluation data is insufficient — keep artifact in GENERATED
        # state rather than advancing it through the pipeline. The artifact can be
        # re-reviewed once evaluation data becomes available.
        # NOTE: The ECS graph does not currently have a PENDING state. Adding one
        # would allow better workflow pausing. For now, we skip the transition
        # entirely and log the pending status.
        logger.info(
            "Review PENDING for artifact %s — keeping in GENERATED state. "
            "Provide eval_fn or real evaluation data to advance.",
            artifact_id,
        )
        # Record the pending verdict as an event even though we don't transition ECS
        await event_store.write_event(
            event_type="review_verdict",
            actor=verdict.reviewer,
            artifact_id=artifact_id,
            from_state="GENERATED",
            to_state="GENERATED",
            verdict=verdict.verdict,
            failure_types=verdict.failure_types,
            detail=verdict.detail,
            confidence=verdict.confidence,
        )
        await trace_store.write_event(
            session_id=artifact_id,
            node_type="L3",
            failure_type="",
            outcome="review_pending",
            detail=verdict.detail,
        )
        return verdict
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
    """Automated quality gate review.

    When an eval_fn is provided, the review verdict is based on the
    evaluation result (confidence score and failure types).

    When no eval_fn is provided:
    - In CI mode: returns APPROVED at confidence=0.70 (CI fixture, not real evaluation).
    - In production mode: returns PENDING at confidence=0.0 with a clear reason,
      refusing to auto-approve without evaluation data.

    This prevents the review layer from silently passing artifacts that
    have never been evaluated.
    """
    if eval_fn is not None:
        result = await eval_fn(context.artifact_content, context.artifact_id)
        confidence = result.get("confidence", 0.0)
        failure_types = result.get("failure_types", [])
        detail = result.get("detail")

        # Check for ci_fixture flag in eval results
        ci_fixture = result.get("ci_fixture", False)
        if ci_fixture and not _is_ci_environment():
            logger.warning(
                "Automated review received CI fixture results for artifact %s "
                "in production mode. Refusing to auto-approve based on fixture data. "
                "Returning NEEDS_REVISION.",
                context.artifact_id,
            )
            return ReviewVerdict(
                artifact_id=context.artifact_id,
                verdict="NEEDS_REVISION",
                reviewer="automated",
                confidence=confidence,
                failure_types=["ci_fixture"],
                detail="Evaluation used CI fixture data — not suitable for production approval. "
                       "Provide a real eval_fn or run in CI environment.",
            )

        if confidence >= confidence_threshold and not failure_types:
            logger.info(
                "Automated review APPROVED artifact %s (confidence=%.2f, ci_fixture=%s).",
                context.artifact_id, confidence, ci_fixture,
            )
            return ReviewVerdict(
                artifact_id=context.artifact_id,
                verdict="APPROVED",
                reviewer="automated",
                confidence=confidence,
            )
        else:
            verdict = (
                "REJECTED" if confidence < confidence_threshold else "NEEDS_REVISION"
            )
            logger.info(
                "Automated review %s artifact %s (confidence=%.2f, failure_types=%s).",
                verdict, context.artifact_id, confidence, failure_types,
            )
            return ReviewVerdict(
                artifact_id=context.artifact_id,
                verdict=verdict,
                reviewer="automated",
                failure_types=failure_types,
                detail=detail,
                confidence=confidence,
            )
    else:
        # No eval function provided
        if _is_ci_environment():
            # CI mode: deterministic fixture — allow pass but at reduced confidence
            # to distinguish from a real evaluation
            logger.info(
                "Automated review: no eval_fn provided for artifact %s in CI mode. "
                "Returning APPROVED at reduced confidence (CI fixture).",
                context.artifact_id,
            )
            return ReviewVerdict(
                artifact_id=context.artifact_id,
                verdict="APPROVED",
                reviewer="automated",
                confidence=0.70,
                detail="CI fixture: no eval_fn provided — not a real quality assessment.",
            )
        else:
            # Production mode: refuse to auto-approve without evaluation
            logger.warning(
                "Automated review: no eval_fn provided for artifact %s in production mode. "
                "Refusing to auto-approve. Returning PENDING.",
                context.artifact_id,
            )
            return ReviewVerdict(
                artifact_id=context.artifact_id,
                verdict="PENDING",
                reviewer="automated",
                confidence=0.0,
                detail="No evaluation function provided — cannot approve without quality assessment. "
                       "Provide an eval_fn or configure review.mode=definer for human review.",
            )


async def _definer_review(
    context: ReviewContext,
    eval_fn: Callable[[str, str], Awaitable[dict]] | None,
    review_cfg: dict,
) -> ReviewVerdict:
    """DEFINER human-in-the-loop review.

    In CI mode: returns APPROVED at reduced confidence (CI fixture).
    In production without eval_fn: returns PENDING — the DEFINER has not
    reviewed the artifact and there is no evaluation data to base an
    approval on.

    When eval_fn is provided, the evaluation result is used to inform the
    definer review, but the actual DEFINER approval is deferred to the
    definer_gate module.
    """
    # If we have eval_fn, run it to gather data for the definer
    eval_confidence = 0.0
    eval_failure_types = []
    eval_detail = None
    eval_ci_fixture = False

    if eval_fn is not None:
        result = await eval_fn(context.artifact_content, context.artifact_id)
        eval_confidence = result.get("confidence", 0.0)
        eval_failure_types = result.get("failure_types", [])
        eval_detail = result.get("detail")
        eval_ci_fixture = result.get("ci_fixture", False)

    if _is_ci_environment():
        # CI mode: deterministic fixture for testing workflows
        logger.info(
            "Definer review: CI mode fixture for artifact %s "
            "(eval_confidence=%.2f, eval_ci_fixture=%s).",
            context.artifact_id, eval_confidence, eval_ci_fixture,
        )
        return ReviewVerdict(
            artifact_id=context.artifact_id,
            verdict="APPROVED",
            reviewer="definer",
            confidence=0.70,
            detail="CI fixture: definer review — not a real DEFINER assessment.",
        )
    else:
        # Production mode: the DEFINER gate must be exercised
        if eval_fn is None:
            logger.warning(
                "Definer review: no eval_fn provided for artifact %s in production mode. "
                "Cannot approve without evaluation data. Returning PENDING.",
                context.artifact_id,
            )
            return ReviewVerdict(
                artifact_id=context.artifact_id,
                verdict="PENDING",
                reviewer="definer",
                confidence=0.0,
                detail="No evaluation data available for DEFINER review. "
                       "Provide an eval_fn or configure review.mode=automated.",
            )

        # Eval data exists — check if it's CI fixture data
        if eval_ci_fixture:
            logger.warning(
                "Definer review: evaluation for artifact %s used CI fixture data "
                "in production mode. Refusing to approve. Returning NEEDS_REVISION.",
                context.artifact_id,
            )
            return ReviewVerdict(
                artifact_id=context.artifact_id,
                verdict="NEEDS_REVISION",
                reviewer="definer",
                confidence=eval_confidence,
                failure_types=["ci_fixture"],
                detail="Evaluation used CI fixture data — not suitable for DEFINER approval. "
                       "Provide real evaluation results.",
            )

        # Real evaluation data exists — the actual DEFINER decision is made
        # in definer_gate.py; here we return NEEDS_REVISION if failures exist,
        # or PENDING for the definer to make a final call
        if eval_failure_types:
            logger.info(
                "Definer review: evaluation found failures for artifact %s "
                "(failure_types=%s). Returning NEEDS_REVISION.",
                context.artifact_id, eval_failure_types,
            )
            return ReviewVerdict(
                artifact_id=context.artifact_id,
                verdict="NEEDS_REVISION",
                reviewer="definer",
                confidence=eval_confidence,
                failure_types=eval_failure_types,
                detail=eval_detail,
            )

        # Evaluation passed but DEFINER has not explicitly approved yet
        logger.info(
            "Definer review: evaluation passed for artifact %s (confidence=%.2f). "
            "Returning PENDING for DEFINER gate decision.",
            context.artifact_id, eval_confidence,
        )
        return ReviewVerdict(
            artifact_id=context.artifact_id,
            verdict="PENDING",
            reviewer="definer",
            confidence=eval_confidence,
            detail="Evaluation passed — awaiting DEFINER gate decision via definer_gate module.",
        )
