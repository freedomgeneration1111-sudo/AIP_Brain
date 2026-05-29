"""DEFINER gate for Workflow 0.1.

No artifact may bypass DEFINER gates (§1.7).

Modes
-----
AUTO_APPROVE_STUB
    Automated approval for CI and development use. Differentiates CI vs
    production behavior:
    - In CI mode: approves with ``stub:auto_approve_ci`` marker.
    - In production mode: approves with ``stub:auto_approve`` marker but
      logs a warning that no real DEFINER reviewed the artifact.
    - CI fixture evaluation results (``ci_fixture=True``) are blocked from
      auto-approval in production — the gate returns "revise" instead.
    - All approval decisions are logged.

MANUAL
    Human-in-the-loop approval. The gate does **not** auto-approve or
    auto-reject. Instead it raises :class:`ManualReviewRequired` so that a
    calling UI or middleware layer can surface the artifact for human review.

    MANUAL mode is *structurally complete* — it carries all the context a
    review queue UI needs — but it is **not fully functional** because the
    following infrastructure does not yet exist:

    1. **Review queue store** — a persistent queue that holds artifacts
       awaiting human approval, with priority ordering and expiration.
    2. **Human approval UI** — a web interface where a DEFINER can inspect
       the artifact, its validation/evaluation results, and approve, reject,
       or request revision.
    3. **Notification system** — alerts DEFINERs that artifacts are pending
       review (email, Slack, in-app, etc.).
    4. **Approval/rejection API endpoints** — REST endpoints that accept a
       DEFINER's decision and feed it back into the workflow.

    Until this infrastructure is built, MANUAL mode is safe: it never
    silently approves or rejects, and the :class:`ManualReviewRequired`
    exception gives the caller everything it needs to build the queue entry.

    How to integrate a UI layer with MANUAL mode:
    - Catch :class:`ManualReviewRequired` in the calling layer.
    - Extract context from the exception (``artifact_summary``,
      ``validation_passed``, ``eval_passed``, ``eval_is_fixture``, etc.).
    - Create a review queue entry from this context.
    - When the DEFINER makes a decision, call :func:`definer_gate` again
      with ``mode=AUTO_APPROVE_STUB`` (or construct a :class:`DefinerDecision`
      directly) and resume the workflow.

CONSERVATIVE (future)
    Would approve only with real (non-fixture) evaluation data. Not yet
    implemented.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from aip.foundation.validation import ValidationResult
from aip.orchestration.nodes.adversarial_eval import EvalResult
from aip.orchestration.nodes.synthesis import SynthesisOutput

logger = logging.getLogger(__name__)


def _is_ci_environment() -> bool:
    """Check whether we are running in a CI environment."""
    return os.environ.get("CI", "").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ManualReviewRequired(Exception):
    """Raised when the DEFINER gate is in MANUAL mode and requires human review.

    This is a **controlled signal**, not a crash.  A calling UI or middleware
    layer should catch this exception, extract its context fields, and create
    a review queue entry for a human DEFINER.

    Attributes:
        artifact_summary: Short description of the artifact under review.
        validation_passed: Whether structural validation passed.
        eval_passed: Whether adversarial evaluation passed.
        eval_is_fixture: Whether the evaluation used CI fixture data.
        reason: Human-readable explanation of why review is required.
        context: Arbitrary additional context for the review queue.
    """

    def __init__(
        self,
        *,
        artifact_summary: str = "",
        validation_passed: bool = False,
        eval_passed: bool = False,
        eval_is_fixture: bool = False,
        reason: str = "MANUAL mode requires human DEFINER review.",
        context: dict[str, Any] | None = None,
    ) -> None:
        self.artifact_summary = artifact_summary
        self.validation_passed = validation_passed
        self.eval_passed = eval_passed
        self.eval_is_fixture = eval_is_fixture
        self.reason = reason
        self.context = context or {}
        super().__init__(self.reason)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


class DefinerGateMode(Enum):
    AUTO_APPROVE_STUB = "auto_approve_stub"
    MANUAL = "manual"
    # CONSERVATIVE = "conservative"  -- Future: approve only with real eval data


@dataclass
class DefinerDecision:
    """Result of the DEFINER gate."""

    action: str  # "approve" | "reject" | "revise"
    reason: str | None = None
    approved_by: str | None = None


@dataclass
class _ManualGateContext:
    """Internal context object that captures the state of a MANUAL gate call.

    Used to build the :class:`ManualReviewRequired` exception with all the
    information a review queue UI would need.
    """

    validation_passed: bool
    validation_detail: str | None
    eval_passed: bool
    eval_is_fixture: bool
    eval_critique: str | None
    artifact_summary: str
    is_ci: bool = field(default_factory=_is_ci_environment)

    def to_exception(self) -> ManualReviewRequired:
        """Build a :class:`ManualReviewRequired` from this context."""
        # Determine a clear reason
        parts: list[str] = ["MANUAL DEFINER gate: human review required."]
        if not self.validation_passed:
            parts.append(
                f"Structural validation FAILED (detail: {self.validation_detail}). "
                "A DEFINER must decide whether to revise or reject.",
            )
        elif not self.eval_passed:
            parts.append("Adversarial evaluation FAILED. A DEFINER must decide whether to reject or request revision.")
        elif self.eval_is_fixture and not self.is_ci:
            parts.append(
                "Evaluation used CI fixture data in production mode. "
                "A DEFINER must verify that real evaluation is performed "
                "before approving.",
            )
        else:
            parts.append(
                "Both validation and evaluation passed. "
                "A DEFINER must explicitly approve before the artifact "
                "can be promoted.",
            )

        return ManualReviewRequired(
            artifact_summary=self.artifact_summary,
            validation_passed=self.validation_passed,
            eval_passed=self.eval_passed,
            eval_is_fixture=self.eval_is_fixture,
            reason=" ".join(parts),
            context={
                "validation_detail": self.validation_detail,
                "eval_critique": self.eval_critique,
                "is_ci_environment": self.is_ci,
                "mode": "MANUAL",
            },
        )


# ---------------------------------------------------------------------------
# Gate implementation
# ---------------------------------------------------------------------------


async def definer_gate(
    synthesis_output: SynthesisOutput,
    validation_result: ValidationResult,
    eval_result: EvalResult,
    mode: DefinerGateMode = DefinerGateMode.AUTO_APPROVE_STUB,
) -> DefinerDecision:
    """DEFINER gate for Workflow 0.1.

    No artifact may bypass DEFINER approval.
    This gate checks validation and evaluation results before
    deciding on approve/reject/revise.

    AUTO_APPROVE_STUB mode:
    - In CI mode: approves with ``stub:auto_approve_ci`` marker.
    - In production mode: approves with ``stub:auto_approve`` marker
      but logs a warning that no real DEFINER reviewed the artifact.
    - If evaluation results are CI fixtures (``ci_fixture=True``) in
      production mode, the gate returns "revise" instead of "approve",
      blocking automatic promotion of fixture-evaluated artifacts.

    MANUAL mode:
    - Does not auto-approve or auto-reject.
    - Raises :class:`ManualReviewRequired` with full context so a
      UI layer can surface the artifact for human DEFINER review.
    - See module docstring for integration guide and remaining
      infrastructure needed for full MANUAL mode.

    Args:
        synthesis_output: The synthesized content.
        validation_result: Structural validation result.
        eval_result: Adversarial evaluation result.
        mode: Gate mode (AUTO_APPROVE_STUB or MANUAL).

    Returns:
        DefinerDecision with action and metadata.

    Raises:
        ManualReviewRequired: When ``mode=MANUAL`` and human review is needed.
    """
    # ------------------------------------------------------------------
    # MANUAL mode — never auto-approve, raise structured exception
    # ------------------------------------------------------------------
    if mode == DefinerGateMode.MANUAL:
        ctx = _ManualGateContext(
            validation_passed=validation_result.passed,
            validation_detail=validation_result.failure_detail,
            eval_passed=eval_result.passed,
            eval_is_fixture=getattr(eval_result, "ci_fixture", False),
            eval_critique=getattr(eval_result, "critique", None),
            artifact_summary=_summarize_artifact(synthesis_output),
        )

        exc = ctx.to_exception()

        logger.info(
            "DEFINER gate: MANUAL mode — raising ManualReviewRequired. "
            "validation_passed=%s, eval_passed=%s, eval_is_fixture=%s. "
            "A human DEFINER must review this artifact.",
            ctx.validation_passed,
            ctx.eval_passed,
            ctx.eval_is_fixture,
        )

        raise exc

    # ------------------------------------------------------------------
    # AUTO_APPROVE_STUB mode — existing behavior unchanged
    # ------------------------------------------------------------------

    # Step 1: Check structural validation
    if not validation_result.passed:
        logger.info(
            "DEFINER gate: validation failed for synthesis output. Returning 'revise'. Detail: %s",
            validation_result.failure_detail,
        )
        return DefinerDecision(
            action="revise",
            reason=f"Structural validation failed: {validation_result.failure_detail}",
            approved_by=None,
        )

    # Step 2: Check adversarial evaluation
    if not eval_result.passed:
        logger.info(
            "DEFINER gate: adversarial evaluation did not pass. Returning 'reject'. Critique: %s",
            getattr(eval_result, "critique", None),
        )
        return DefinerDecision(
            action="reject",
            reason="Adversarial evaluation did not pass.",
            approved_by=None,
        )

    # Step 3: Both validation and evaluation passed — check CI fixture status
    is_ci = _is_ci_environment()
    eval_is_fixture = getattr(eval_result, "ci_fixture", False)

    if eval_is_fixture and not is_ci:
        # Production mode with CI fixture evaluation: block auto-approve
        logger.warning(
            "DEFINER gate: evaluation results are CI fixtures (ci_fixture=True) "
            "in production mode. Refusing to auto-approve. Returning 'revise'. "
            "Real evaluation results are required for DEFINER approval in production.",
        )
        return DefinerDecision(
            action="revise",
            reason="Cannot auto-approve: evaluation used CI fixture data. "
            "Real evaluation results are required for production DEFINER approval.",
            approved_by=None,
        )

    # Step 4: Approve with appropriate marker
    if is_ci:
        logger.info(
            "DEFINER gate: CI mode auto-approve (stub). "
            "Validation and evaluation both passed. approved_by=stub:auto_approve_ci.",
        )
        return DefinerDecision(
            action="approve",
            reason="Auto-approved (CI mode): structural validation and adversarial eval both passed.",
            approved_by="stub:auto_approve_ci",
        )
    else:
        # Production mode: approve but log that this is a stub decision
        logger.warning(
            "DEFINER gate: AUTO_APPROVE_STUB mode in production. "
            "Auto-approving artifact because both validation and evaluation passed, "
            "but no real DEFINER reviewed this artifact. "
            "approved_by=stub:auto_approve. Consider switching to MANUAL mode "
            "for production DEFINER review.",
        )
        return DefinerDecision(
            action="approve",
            reason="Auto-approved (stub): structural validation and adversarial eval both passed. "
            "NOTE: No real DEFINER reviewed this artifact — approval is automated.",
            approved_by="stub:auto_approve",
        )


def _summarize_artifact(synthesis_output: SynthesisOutput) -> str:
    """Create a short human-readable summary of the synthesis output.

    This is used in :class:`ManualReviewRequired` so a reviewer can quickly
    understand what the artifact contains without examining the full content.
    """
    content_preview = synthesis_output.content[:120]
    if len(synthesis_output.content) > 120:
        content_preview += "..."
    return (
        f"model={synthesis_output.model_name}, "
        f"slot={synthesis_output.model_slot}, "
        f"tokens={synthesis_output.token_count_in}in/{synthesis_output.token_count_out}out, "
        f"preview: {content_preview!r}"
    )
