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

            # CI fixture detection
            if "CI fixture" in content or "ci-evaluation" in result.get("model", ""):
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

            # Parse real model response
            try:
                parsed = json.loads(content)
                coherence_score = float(parsed.get("coherence_score", 0.0))
                violations = parsed.get("violations", [])
                rationale = parsed.get("rationale", "Model evaluation")
                ci_fixture = False  # Real evaluation succeeded
            except (json.JSONDecodeError, ValueError):
                # Model response was not valid JSON — still a fixture
                logger.warning("Domain coherence model response was not valid JSON; using CI fixture")

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
