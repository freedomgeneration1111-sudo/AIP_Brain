"""
Adversarial Evaluation (L3b) — promoted in Phase 4.

Phase 1: deterministic stub.
Phase 4: real ModelSlotResolver integration with skeptic prompt.

Adversarial evaluation applies to canonical-bound outputs and marginal L3a passes; requires separate skeptic prompt.

Issue 21: Remove duplicate adversarial_evaluate() function. Promote adversarial_eval()
to optionally use ModelSlotResolver when provided.

Fallback behavior:
    When no model_resolver is provided (or when evaluation fails), returns honest
    low/zero scores with ``ci_fixture=True`` in the result. This is consistent
    with the FaithfulnessResult and DomainCoherenceResult patterns — callers
    MUST check the ``ci_fixture`` flag to distinguish real evaluation from
    fixture/fallback results.

    CI fixture path: When the model resolver returns a CI fixture response
    (detected by "CI fixture" in content or "ci-evaluation" in model name),
    scores are set to 0.0 with ``ci_fixture=True`` so that promotion gates
    correctly block artifacts evaluated without real model assessment.

    Production path: When model_resolver is provided and returns a real
    response, attempts to parse JSON scores from the model output. If parsing
    fails, falls back to 0.0 scores with ``ci_fixture=True`` and a warning log.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aip.orchestration.nodes.synthesis import SynthesisOutput
from aip.foundation.validation import ValidationResult

logger = logging.getLogger(__name__)


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
    ci_fixture: bool = True  # True when scores are fixtures/fallbacks, not real evaluation


# Default Phase 1 L3b adversarial criteria
DEFAULT_EVAL_CRITERIA: list[EvalCriterion] = [
    EvalCriterion(
        criterion_id="grounding",
        name="Grounding / Hallucination",
        description="Does the synthesis output stay grounded in the provided retrieval context without introducing unsupported claims?",
        model_gen_assumption="Models may hallucinate specific claims; grounding check compensates per §1.8",
    ),
    EvalCriterion(
        criterion_id="completeness",
        name="Completeness",
        description="Does the output adequately address the original query given the retrieved context?",
        model_gen_assumption="Models may omit key domain requirements; completeness check compensates per §1.8",
    ),
    EvalCriterion(
        criterion_id="coherence",
        name="Structural Coherence",
        description="Is the output well-structured, clear, and free of internal contradictions?",
        model_gen_assumption="Models may produce internally contradictory output; coherence check compensates per §1.8",
    ),
    EvalCriterion(
        criterion_id="assumption_violation",
        name="Unstated Assumption Violation",
        description="Does the synthesis introduce assumptions not supported by the query or retrieved context?",
        model_gen_assumption="Models may produce vague output; specificity check compensates per §1.8",
    ),
]


# CI fixture values — used when model_resolver is None or returns fixture response.
# These are set to 0.0 to ensure that CI fixtures cannot pass promotion thresholds
# without explicit opt-in. This is consistent with FaithfulnessResult and
# DomainCoherenceResult patterns.
_CI_FIXTURE_SCORES = {
    "framework_integrity": 0.0,
    "logic": 0.0,
    "honesty": 0.0,
    "completeness": 0.0,
}
_CI_FIXTURE_OVERALL = 0.0


async def adversarial_eval(
    synthesis_output: SynthesisOutput | None = None,
    validation_result: ValidationResult | None = None,
    eval_criteria: list[EvalCriterion] | None = None,
    # Phase 4 promoted parameters
    artifact_content: str | None = None,
    context: str | None = None,
    model_resolver: Any = None,
    config: Any | None = None,
) -> EvalResult | dict:
    """
    L3b adversarial evaluation.

    Phase 1 backward compat: accepts SynthesisOutput + ValidationResult for stub mode.
    Phase 4 promoted: accepts artifact_content, context, model_resolver for real eval.

    When model_resolver is provided, uses it for model-based evaluation (skeptic prompt).
    Otherwise falls back to honest zero-score stub with ``ci_fixture=True``.

    Fallback behavior:
        - No model_resolver: returns 0.0 scores, ci_fixture=True, passed=False
        - CI fixture response: returns 0.0 scores, ci_fixture=True, passed=False
        - Real response with parseable JSON: returns parsed scores, ci_fixture=False
        - Real response with unparseable JSON: returns 0.0 scores, ci_fixture=True, logs warning
    """
    # Phase 4 path: model_resolver provided
    if model_resolver is not None:
        prompt_path = Path(__file__).parent.parent.parent / "prompts" / "adversarial_eval.md"
        system_prompt = ""
        if prompt_path.exists():
            system_prompt = prompt_path.read_text()

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        _content = artifact_content or ""
        _context = context or ""
        messages.append({
            "role": "user",
            "content": f"Artifact:\n{_content}\n\nContext (for review):\n{_context}",
        })

        try:
            result = await model_resolver.call(
                "evaluation",
                messages,
                temperature=0.3,
            )
        except Exception:
            logger.error(
                "Adversarial eval model call FAILED; returning ci_fixture scores (0.0). "
                "Promotion will be blocked by ci_fixture=True flag.",
                exc_info=True,
            )
            return _make_fixture_dict("Model call failed — adversarial evaluation unavailable")

        result_content = result.get("content", "")
        result_model = result.get("model", "unknown")

        # CI fixture path: model returned a fixture response
        if "CI fixture" in result_content or "ci-evaluation" in result_model:
            logger.info(
                "Adversarial eval detected CI fixture response (model=%s). "
                "Returning ci_fixture=True with 0.0 scores.",
                result_model,
            )
            return {
                "scores": dict(_CI_FIXTURE_SCORES),
                "overall": _CI_FIXTURE_OVERALL,
                "critique": "CI fixture — adversarial evaluation not performed; scores are 0.0",
                "model": result_model,
                "usage": result.get("usage", {}),
                "latency_ms": result.get("latency_ms", 0),
                "ci_fixture": True,
                "passed": False,
            }

        # Production path: attempt to parse real model output for scores
        try:
            parsed = json.loads(result_content)
            scores = {}
            # Try to extract structured scores from model JSON
            raw_scores = parsed.get("scores", {})
            if isinstance(raw_scores, dict) and raw_scores:
                for key in ("framework_integrity", "logic", "honesty", "completeness"):
                    if key in raw_scores:
                        scores[key] = float(raw_scores[key])
            overall = float(parsed.get("overall", 0.0)) if scores else 0.0

            if not scores:
                # Model didn't return structured scores — fall back to 0.0
                logger.warning(
                    "Adversarial eval model response contained no structured scores; "
                    "falling back to ci_fixture with 0.0 scores. Response preview: %s",
                    result_content[:200],
                )
                return {
                    "scores": dict(_CI_FIXTURE_SCORES),
                    "overall": _CI_FIXTURE_OVERALL,
                    "critique": result_content[:300] if result_content else "Model response received but no scores parsed",
                    "model": result_model,
                    "usage": result.get("usage", {}),
                    "latency_ms": result.get("latency_ms", 0),
                    "ci_fixture": True,
                    "passed": False,
                }

            # Real evaluation succeeded
            critique = parsed.get("critique", result_content[:300] if result_content else "Model evaluation")
            passed = overall >= 0.70
            logger.info(
                "Adversarial eval completed with real scores (overall=%.2f, ci_fixture=False)",
                overall,
            )
            return {
                "scores": scores,
                "overall": overall,
                "critique": critique,
                "model": result_model,
                "usage": result.get("usage", {}),
                "latency_ms": result.get("latency_ms", 0),
                "ci_fixture": False,
                "passed": passed,
            }

        except (json.JSONDecodeError, ValueError, TypeError):
            # Model response was not valid JSON — fall back to fixture
            logger.warning(
                "Adversarial eval model response was not valid JSON; returning ci_fixture "
                "scores (0.0). Promotion will be blocked. Response preview: %s",
                result_content[:200],
            )
            return {
                "scores": dict(_CI_FIXTURE_SCORES),
                "overall": _CI_FIXTURE_OVERALL,
                "critique": result_content[:300] if result_content else "Unparseable model response",
                "model": result_model,
                "usage": result.get("usage", {}),
                "latency_ms": result.get("latency_ms", 0),
                "ci_fixture": True,
                "passed": False,
            }

    # Phase 1 stub path (backward compat — no model_resolver)
    # Return honest 0.0 scores with ci_fixture=True so promotion is blocked
    logger.info("Adversarial eval called without model_resolver; returning ci_fixture scores (0.0)")

    if validation_result is None:
        validation_result = ValidationResult(passed=True, failure_type=None, failure_detail=None, checks_run=0, checks_failed=[])

    criteria = eval_criteria or DEFAULT_EVAL_CRITERIA

    # Honest stub: all scores are 0.0 since no real evaluation was performed
    scores: dict[str, float] = {crit.criterion_id: 0.0 for crit in criteria}
    avg_score = 0.0
    passed = False  # Cannot pass without real evaluation

    critique = (
        f"Adversarial evaluation stub — no model_resolver provided. "
        f"L3a validation: {'passed' if validation_result.passed else 'failed'}. "
        f"Requires real model evaluation before promotion."
    )

    requires_deep_eval = True  # Always requires deep eval when in stub mode

    return EvalResult(
        passed=passed,
        scores=scores,
        requires_deep_eval=requires_deep_eval,
        critique=critique,
        ci_fixture=True,
    )


def _make_fixture_dict(reason: str) -> dict:
    """Build a CI fixture dict result for adversarial eval failure paths."""
    return {
        "scores": dict(_CI_FIXTURE_SCORES),
        "overall": _CI_FIXTURE_OVERALL,
        "critique": f"Adversarial evaluation unavailable: {reason}",
        "model": "none",
        "usage": {},
        "latency_ms": 0,
        "ci_fixture": True,
        "passed": False,
    }
