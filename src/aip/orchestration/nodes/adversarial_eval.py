"""
Adversarial Evaluation Stub (CHUNK-1.4 per Rev 1.3).

L3b interface. Explicit stub — no model calls in Phase 1.
Returns deterministic passing scores by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aip.orchestration.nodes.synthesis import SynthesisOutput
from aip.foundation.validation import ValidationResult


@dataclass
class EvalCriterion:
    criterion_id: str
    name: str
    description: str
    model_gen_assumption: str | None


@dataclass
class EvalResult:
    passed: bool
    scores: dict[str, float]
    requires_deep_eval: bool
    critique: str | None = None


# Default Phase 1 L3b adversarial criteria (from §F.3, tagged per §1.8)
DEFAULT_EVAL_CRITERIA: list[EvalCriterion] = [
    EvalCriterion(
        criterion_id="grounding",
        name="Grounding / Hallucination",
        description="Does the synthesis output stay grounded in the provided retrieval context without introducing unsupported claims?",
        model_gen_assumption="deepseek-v3-0324 or qwen3-4b",
    ),
    EvalCriterion(
        criterion_id="completeness",
        name="Completeness",
        description="Does the output adequately address the original query given the retrieved context?",
        model_gen_assumption="deepseek-v3-0324 or qwen3-4b",
    ),
    EvalCriterion(
        criterion_id="coherence",
        name="Structural Coherence",
        description="Is the output well-structured, clear, and free of internal contradictions?",
        model_gen_assumption="deepseek-v3-0324 or qwen3-4b",
    ),
    EvalCriterion(
        criterion_id="assumption_violation",
        name="Unstated Assumption Violation",
        description="Does the synthesis introduce assumptions not supported by the query or retrieved context?",
        model_gen_assumption="deepseek-v3-0324 or qwen3-4b",
    ),
]


async def adversarial_eval(
    synthesis_output: SynthesisOutput,
    validation_result: ValidationResult,
    eval_criteria: list[EvalCriterion] | None = None,
) -> EvalResult:
    """
    Phase 1 stub for L3b adversarial evaluation.

    In stub mode this returns deterministic, generally passing scores.
    The presence of validation failures from L3a can influence the result
    and set requires_deep_eval=True.
    """
    criteria = eval_criteria or DEFAULT_EVAL_CRITERIA

    scores: dict[str, float] = {}
    base_score = 0.82

    # If L3a validation failed, reduce scores and flag for deeper review
    if not validation_result.passed:
        base_score = 0.65

    for crit in criteria:
        # Very simple deterministic scoring for the stub
        score = base_score
        if crit.criterion_id == "grounding" and not validation_result.passed:
            score = 0.55
        if crit.criterion_id == "completeness":
            score = min(0.95, base_score + 0.08)
        scores[crit.criterion_id] = round(score, 2)

    avg_score = sum(scores.values()) / len(scores)
    passed = avg_score >= 0.70 and validation_result.passed

    critique = None
    if not passed:
        critique = f"L3a validation issues detected: {validation_result.failure_detail}. Recommend deeper model-based review."
    elif not validation_result.passed:
        critique = "Minor L3a issues present but overall scores acceptable in stub mode."

    requires_deep_eval = (not passed) or (not validation_result.passed)

    return EvalResult(
        passed=passed,
        scores=scores,
        requires_deep_eval=requires_deep_eval,
        critique=critique,
    )
