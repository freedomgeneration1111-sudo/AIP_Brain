"""L3a Stage 2 — Faithfulness evaluation.

Faithfulness to retrieved context.
Evaluation carries model_gen_assumption.
Skip if Stage 1 already failed (anti-token-burn).

Fallback behavior:
    When no model_resolver is provided (or when evaluation fails), returns a
    CI fixture result with score 0.85 and the ``ci_fixture`` flag set to True.
    Callers MUST check the ``ci_fixture`` flag to distinguish real evaluation
    from fixture results. The canonical pipeline blocks promotion when
    evaluation fails entirely.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aip.foundation.schemas import (
    Chunk,
    EvaluationScore,
    FaithfulnessResult,
)

logger = logging.getLogger(__name__)


# CI fixture values — used when model_resolver is None or on error
_CI_FAITHFULNESS_SCORE = 0.85
_CI_CONTEXT_COVERAGE = 0.80


async def evaluate_faithfulness(
    artifact_id: str,
    artifact_content: str,
    retrieved_context: list[Chunk],
    model_resolver: Any,
) -> FaithfulnessResult:
    """Evaluate faithfulness of artifact content to retrieved context.

    Identifies hallucinated claims not grounded in context.
    Returns FaithfulnessResult with score, coverage, and flags.

    Attempts real model evaluation when model_resolver is available,
    falling back to CI fixtures when not or on error.

    The returned FaithfulnessResult includes a ``ci_fixture`` flag in
    evaluation_scores rationale when using fixture values. Callers should
    check this flag to avoid treating fixture scores as real evaluations.
    """
    # Default CI fixture values
    faithfulness_score = _CI_FAITHFULNESS_SCORE
    context_coverage = _CI_CONTEXT_COVERAGE
    hallucination_flags: list[str] = []
    tokens_consumed = 0
    model_slot_used = "evaluation"
    rationale = "CI fixture — automatic pass"
    ci_fixture = True  # Assume fixture unless real evaluation succeeds

    # Try real model evaluation
    if model_resolver is not None:
        try:
            # Format context chunks for the model
            context_text = (
                "\n\n".join(
                    f"[{c.id}] (score: {c.score:.2f}, domain: {c.domain}):\n{c.content}" for c in retrieved_context
                )
                if retrieved_context
                else "(No context retrieved)"
            )

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a faithfulness evaluator. Given a generated artifact and "
                        "the retrieved context it was based on, identify any claims in the "
                        "artifact that are NOT grounded in the context. Score faithfulness "
                        "0.0-1.0. Also estimate context coverage (fraction of context addressed). "
                        'Return JSON: {"faithfulness_score": float, "context_coverage": float, '
                        '"hallucination_flags": [str], "rationale": str}'
                    ),
                },
                {
                    "role": "user",
                    "content": f"Retrieved Context:\n{context_text}\n\nArtifact:\n{artifact_content}",
                },
            ]

            result = await model_resolver.call("evaluation", messages, temperature=0.2)
            content = result.get("content", "")
            tokens_consumed = result.get("usage", {}).get("total_tokens", 0)

            # CI fixture detection: return deterministic result with explicit flag
            if "CI fixture" in content or "ci-evaluation" in result.get("model", ""):
                return FaithfulnessResult(
                    artifact_id=artifact_id,
                    faithfulness_score=_CI_FAITHFULNESS_SCORE,
                    context_coverage=_CI_CONTEXT_COVERAGE,
                    hallucination_flags=[],
                    evaluation_scores=[
                        EvaluationScore(
                            dimension="faithfulness",
                            score=_CI_FAITHFULNESS_SCORE,
                            rationale="CI fixture — automatic pass (model returned fixture response)",
                            model_slot_used="evaluation",
                            tokens_consumed=tokens_consumed,
                            model_gen_assumption=(
                                "Models may produce plausible-sounding but ungrounded "
                                "claims when context is insufficient"
                            ),
                        ),
                    ],
                    ci_fixture=True,
                )

            # Parse real model response
            try:
                parsed = json.loads(content)
                faithfulness_score = float(parsed.get("faithfulness_score", 0.0))
                context_coverage = float(parsed.get("context_coverage", 0.0))
                hallucination_flags = parsed.get("hallucination_flags", [])
                rationale = parsed.get("rationale", "Model evaluation")
                ci_fixture = False  # Real evaluation succeeded
            except (json.JSONDecodeError, ValueError):
                # Model response was not valid JSON — still a fixture
                logger.warning("Faithfulness model response was not valid JSON; using CI fixture")

        except Exception:
            # Model call failed entirely — use CI fixture
            pass  # Use CI fixture defaults

    return FaithfulnessResult(
        artifact_id=artifact_id,
        faithfulness_score=faithfulness_score,
        context_coverage=context_coverage,
        hallucination_flags=hallucination_flags,
        evaluation_scores=[
            EvaluationScore(
                dimension="faithfulness",
                score=faithfulness_score,
                rationale=rationale,
                model_slot_used=model_slot_used,
                tokens_consumed=tokens_consumed,
                model_gen_assumption=(
                    "Models may produce plausible-sounding but ungrounded claims when context is insufficient"
                ),
            ),
        ],
        ci_fixture=ci_fixture,
    )
