"""L3a Evaluation Orchestrator.

Moved from foundation/validation.py for layer discipline.
The full_l3a_evaluation function orchestrates multi-stage evaluation
which requires orchestration-layer imports (faithfulness, domain_coherence).
It does not belong in the foundation layer.

foundation/validation.py retains only structural_validate() and dataclasses.
"""

from __future__ import annotations

from typing import Any

from aip.foundation.validation import structural_validate


async def full_l3a_evaluation(
    artifact_id: str,
    artifact_content: str,
    domain: str,
    retrieved_context: list,
    model_resolver: Any,
    config: Any | None = None,
) -> dict:
    """Orchestrates the complete three-stage L3a evaluation.

    Stage 1: structural_validate (deterministic, zero tokens)
    Stage 2: evaluate_faithfulness (model-based, if Stage 1 passes)
    Stage 3: evaluate_domain_coherence (model-based, if Stage 1 passes)

    Skips expensive model stages when Stage 1 already fails (anti-token-burn).
    Uses thresholds from config [evaluation].
    """
    from aip.orchestration.nodes.faithfulness import evaluate_faithfulness
    from aip.orchestration.nodes.domain_coherence import evaluate_domain_coherence

    # Stage 1 (always run)
    stage1 = structural_validate(artifact_content)

    result: dict[str, Any] = {
        "artifact_id": artifact_id,
        "stage1": {
            "passed": stage1.passed,
            "failure_type": stage1.failure_type,
            "failure_detail": stage1.failure_detail,
        },
        "stage2": None,
        "stage3": None,
        "overall_pass": stage1.passed,
        "failure_types": [stage1.failure_type] if stage1.failure_type else [],
    }

    if not stage1.passed:
        return result

    if config and hasattr(config, "get"):
        eval_cfg = config.get("evaluation", {}) if isinstance(config, dict) else getattr(config, "evaluation", {})
    else:
        eval_cfg = {}

    # Use config thresholds from CanonicalPromotionConfig if available
    if config is not None:
        if hasattr(config, "faithfulness_threshold"):
            faithfulness_threshold = config.faithfulness_threshold
        elif isinstance(eval_cfg, dict):
            faithfulness_threshold = eval_cfg.get("faithfulness_threshold", 0.70)
        else:
            faithfulness_threshold = 0.70

        if hasattr(config, "domain_coherence_threshold"):
            domain_coherence_threshold = config.domain_coherence_threshold
        elif isinstance(eval_cfg, dict):
            domain_coherence_threshold = eval_cfg.get("domain_coherence_threshold", 0.60)
        else:
            domain_coherence_threshold = 0.60
    else:
        faithfulness_threshold = eval_cfg.get("faithfulness_threshold", 0.70) if isinstance(eval_cfg, dict) else 0.70
        domain_coherence_threshold = eval_cfg.get("domain_coherence_threshold", 0.60) if isinstance(eval_cfg, dict) else 0.60

    # Stage 2
    stage2 = await evaluate_faithfulness(
        artifact_id=artifact_id,
        artifact_content=artifact_content,
        retrieved_context=retrieved_context,
        model_resolver=model_resolver,
    )
    result["stage2"] = {
        "faithfulness_score": stage2.faithfulness_score,
        "context_coverage": stage2.context_coverage,
        "hallucination_flags": stage2.hallucination_flags,
    }

    if stage2.faithfulness_score < faithfulness_threshold:
        result["overall_pass"] = False
        if "A" not in result["failure_types"]:
            result["failure_types"].append("A")

    # Stage 3
    stage3 = await evaluate_domain_coherence(
        artifact_id=artifact_id,
        artifact_content=artifact_content,
        domain=domain,
        model_resolver=model_resolver,
    )
    result["stage3"] = {
        "coherence_score": stage3.coherence_score,
        "domain": stage3.domain,
        "violations": stage3.violations,
    }

    if stage3.coherence_score < domain_coherence_threshold:
        result["overall_pass"] = False
        if "A" not in result["failure_types"]:
            result["failure_types"].append("A")

    return result
