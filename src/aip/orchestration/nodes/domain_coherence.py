"""L3a Stage 3 — Domain coherence evaluation.

Domain-specific coherence checks.
Evaluation carries model_gen_assumption.

Fallback behavior:
    When no model_resolver is provided (or when evaluation fails), returns a
    CI fixture result with score 0.90 and the ``ci_fixture`` flag set to True.
    Callers MUST check the ``ci_fixture`` flag to distinguish real evaluation
    from fixture results. The canonical pipeline blocks promotion when
    evaluation fails entirely.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aip.foundation.schemas import (
    DomainCoherenceResult,
    EvaluationScore,
)

logger = logging.getLogger(__name__)

# CI fixture values — used when model_resolver is None or on error
_CI_COHERENCE_SCORE = 0.90


def _is_ci_fixture_response(content: str, result: dict[str, Any]) -> bool:
    """Recognize explicit CI fixtures before validating coherence JSON."""
    if result.get("ci_fixture") is True:
        return True

    if "ci-evaluation" in str(result.get("model", "")).casefold():
        return True

    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return "[ci-fixture" in content.casefold() or "[ci fixture" in content.casefold()

    if not isinstance(parsed, dict):
        return False

    feedback = parsed.get("feedback")
    is_aristotle_fixture = {"score", "mastery_achieved", "diagnosis"}.issubset(parsed) and isinstance(feedback, str)
    return is_aristotle_fixture and "[ci-fixture" in feedback.casefold()


async def evaluate_domain_coherence(
    artifact_id: str,
    artifact_content: str,
    domain: str,
    model_resolver: Any,
) -> DomainCoherenceResult:
    """Evaluate domain coherence of artifact content.

    Checks whether the artifact meets domain-specific quality standards.
    Returns DomainCoherenceResult with score and violations.

    Attempts real model evaluation when model_resolver is available,
    falling back to CI fixtures when not or on error.

    The returned DomainCoherenceResult includes a ``ci_fixture`` flag in
    evaluation_scores rationale when using fixture values. Callers should
    check this flag to avoid treating fixture scores as real evaluations.
    """
    # Default CI fixture values
    coherence_score = _CI_COHERENCE_SCORE
    violations: list[str] = []
    tokens_consumed = 0
    model_slot_used = "evaluation"
    rationale = "CI fixture — automatic pass"
    ci_fixture = True  # Assume fixture unless real evaluation succeeds

    # Try real model evaluation
    if model_resolver is not None:
        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a domain coherence evaluator. Given a generated artifact "
                        "and its target domain, evaluate whether it meets the quality standards "
                        "of that domain. Score coherence 0.0-1.0. List any violations. "
                        'Return JSON: {"coherence_score": float, "violations": [str], '
                        '"rationale": str}'
                    ),
                },
                {
                    "role": "user",
                    "content": f"Domain: {domain}\n\nArtifact:\n{artifact_content}",
                },
            ]

            result = await model_resolver.call("evaluation", messages, temperature=0.2)
            content = result.get("content", "")
            tokens_consumed = result.get("usage", {}).get("total_tokens", 0)

            # The shared evaluation slot's ARISTOTLE fixture is valid JSON,
            # but it is not a domain coherence response.
            if _is_ci_fixture_response(content, result):
                return DomainCoherenceResult(
                    artifact_id=artifact_id,
                    coherence_score=_CI_COHERENCE_SCORE,
                    domain=domain,
                    violations=[],
                    evaluation_scores=[
                        EvaluationScore(
                            dimension="domain_coherence",
                            score=_CI_COHERENCE_SCORE,
                            rationale="CI fixture — automatic pass (model returned fixture response)",
                            model_slot_used="evaluation",
                            tokens_consumed=tokens_consumed,
                            model_gen_assumption=(
                                "Models may produce structurally valid but "
                                "domain-incoherent output without explicit domain constraints"
                            ),
                        ),
                    ],
                    ci_fixture=True,
                )

            # Parse and validate a real coherence response. Missing fields
            # must not be accepted as a successful production evaluation.
            try:
                parsed = json.loads(content)
                required_fields = {"coherence_score", "violations", "rationale"}
                if not isinstance(parsed, dict) or not required_fields.issubset(parsed):
                    raise ValueError("coherence response is missing required fields")
                if not isinstance(parsed["violations"], list):
                    raise ValueError("violations must be a list")

                coherence_score = float(parsed["coherence_score"])
                violations = parsed["violations"]
                rationale = str(parsed["rationale"])
                ci_fixture = False  # Real evaluation succeeded
            except (json.JSONDecodeError, TypeError, ValueError):
                logger.warning("Domain coherence response was not a valid schema; using CI fixture")

        except Exception:
            # Model call failed entirely — use CI fixture
            pass  # Use CI fixture defaults

    return DomainCoherenceResult(
        artifact_id=artifact_id,
        coherence_score=coherence_score,
        domain=domain,
        violations=violations,
        evaluation_scores=[
            EvaluationScore(
                dimension="domain_coherence",
                score=coherence_score,
                rationale=rationale,
                model_slot_used=model_slot_used,
                tokens_consumed=tokens_consumed,
                model_gen_assumption=(
                    "Models may produce structurally valid but domain-incoherent "
                    "output without explicit domain constraints"
                ),
            ),
        ],
        ci_fixture=ci_fixture,
    )
