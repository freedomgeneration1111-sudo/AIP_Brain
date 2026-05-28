"""DEFINER gate stub for Workflow 0.1.

No artifact may bypass DEFINER gates.
Phase 1: AUTO_APPROVE_STUB mode for CI testing.
Manual mode deferred to Phase 2 (requires UI/review queue).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from aip.foundation.validation import ValidationResult
from aip.orchestration.nodes.adversarial_eval import EvalResult
from aip.orchestration.nodes.synthesis import SynthesisOutput


class DefinerGateMode(Enum):
    AUTO_APPROVE_STUB = "auto_approve_stub"
    # MANUAL = "manual"  -- Phase 2 (requires UI)


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
    and evaluation pass. MANUAL mode deferred to Phase 2.

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
            "MANUAL DEFINER gate mode requires UI integration (Phase 2)."
        )

    # Auto-approve only if BOTH validation and evaluation pass
    if not validation_result.passed:
        return DefinerDecision(
            action="revise",
            reason=f"Structural validation failed: {validation_result.failure_detail}",
            approved_by=None,
        )

    if not eval_result.passed:
        return DefinerDecision(
            action="reject",
            reason="Adversarial evaluation did not pass.",
            approved_by=None,
        )

    return DefinerDecision(
        action="approve",
        reason="Auto-approved: structural validation and adversarial eval both passed.",
        approved_by="stub:auto_approve",
    )
