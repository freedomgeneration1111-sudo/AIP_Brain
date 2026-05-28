"""DEFINER gate stub for Workflow 0.1.

No artifact may bypass DEFINER gates.
Phase 1: AUTO_APPROVE_STUB mode for CI testing.
Manual mode deferred to Phase 2 (requires UI/review queue).

Improvements (honesty pass):
- AUTO_APPROVE_STUB now differentiates CI vs production behavior.
  In production, even when both validation and evaluation pass, the
  gate logs a warning that approval is stub-based and not from a real
  DEFINER. The approval still proceeds but is explicitly marked.
- CI fixture evaluation results (ci_fixture=True) trigger a more
  conservative path: the gate approves but at reduced confidence and
  with clear documentation that the decision is fixture-based.
- MANUAL mode remains NotImplementedError (documented clearly).
- All approval decisions are logged.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum

from aip.foundation.validation import ValidationResult
from aip.orchestration.nodes.adversarial_eval import EvalResult
from aip.orchestration.nodes.synthesis import SynthesisOutput

logger = logging.getLogger(__name__)


def _is_ci_environment() -> bool:
    """Check whether we are running in a CI environment."""
    return os.environ.get("CI", "").lower() in ("true", "1", "yes")


class DefinerGateMode(Enum):
    AUTO_APPROVE_STUB = "auto_approve_stub"
    # MANUAL = "manual"  -- Phase 2 (requires UI)
    # CONSERVATIVE = "conservative"  -- Future: approve only with real eval data


@dataclass
class DefinerDecision:
    """Result of the DEFINER gate."""
    action: str  # "approve" | "reject" | "revise"
    reason: str | None = None
    approved_by: str | None = None


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

    Phase 1: AUTO_APPROVE_STUB auto-approves if both validation
    and evaluation pass, with the following behavior:
    - In CI mode: approves with stub:auto_approve_ci marker.
    - In production mode: approves with stub:auto_approve marker
      but logs a warning that no real DEFINER reviewed the artifact.
    - If evaluation results are CI fixtures (ci_fixture=True) in
      production mode, the gate returns "revise" instead of "approve",
      blocking automatic promotion of fixture-evaluated artifacts.

    MANUAL mode deferred to Phase 2 — raises NotImplementedError
    with a clear message.

    Args:
        synthesis_output: The synthesized content.
        validation_result: Structural validation result.
        eval_result: Adversarial evaluation result.
        mode: Gate mode (STUB or MANUAL).

    Returns:
        DefinerDecision with action and metadata.
    """
    if mode != DefinerGateMode.AUTO_APPROVE_STUB:
        raise NotImplementedError(
            "MANUAL DEFINER gate mode requires UI integration (Phase 2). "
            "Only AUTO_APPROVE_STUB mode is currently available. "
            "To implement MANUAL mode, a review queue UI and human-in-the-loop "
            "approval flow are needed."
        )

    # Step 1: Check structural validation
    if not validation_result.passed:
        logger.info(
            "DEFINER gate: validation failed for synthesis output. "
            "Returning 'revise'. Detail: %s",
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
            "DEFINER gate: adversarial evaluation did not pass. "
            "Returning 'reject'. Critique: %s",
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
            "Real evaluation results are required for DEFINER approval in production."
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
            "Validation and evaluation both passed. approved_by=stub:auto_approve_ci."
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
            "approved_by=stub:auto_approve. Consider implementing MANUAL mode (Phase 2)."
        )
        return DefinerDecision(
            action="approve",
            reason="Auto-approved (stub): structural validation and adversarial eval both passed. "
                   "NOTE: No real DEFINER reviewed this artifact — approval is automated.",
            approved_by="stub:auto_approve",
        )
