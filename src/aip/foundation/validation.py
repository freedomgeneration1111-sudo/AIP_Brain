"""
L3a Stage 1 deterministic structural validation (pure, zero tokens).
Per Rev 1.3 CHUNK-1.2 (unchanged from Rev 1.0).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class ValidationRule:
    rule_id: str
    check: Callable[[str], bool]
    failure_type: str  # "C" | "E"
    message: str
    model_gen_assumption: str | None


@dataclass
class ValidationResult:
    passed: bool
    failure_type: str | None
    failure_detail: str | None
    checks_run: int
    checks_failed: list[str]


# Default Phase 1 rules (tagged per §1.8)
DEFAULT_RULES: list[ValidationRule] = [
    ValidationRule(
        rule_id="min_length",
        check=lambda s: len(s) >= 100,
        failure_type="C",
        message="Output must be at least 100 characters.",
        model_gen_assumption="Models may produce outputs that appear complete without sufficient substance",
    ),
    ValidationRule(
        rule_id="no_false_success_patterns",
        check=lambda s: not any(p in s.lower() for p in ["task complete", "all done", "finished successfully"]) or len(s) > 200,
        failure_type="E",
        message="Claims completion without sufficient substance.",
        model_gen_assumption="Models may produce outputs that appear complete without sufficient substance",
    ),
    ValidationRule(
        rule_id="required_section_markers",
        check=lambda s: any(marker in s for marker in ["##", "```", "1.", "Step"]),
        failure_type="C",
        message="Output lacks clear section markers.",
        model_gen_assumption="Models may produce outputs that appear complete without sufficient substance",
    ),
]


def structural_validate(
    output: str, rules: list[ValidationRule] | None = None
) -> ValidationResult:
    """Pure L3a validation. No model calls. Zero tokens."""
    rules = rules or DEFAULT_RULES
    failed = []
    for rule in rules:
        if not rule.check(output):
            failed.append(rule.rule_id)

    passed = len(failed) == 0
    return ValidationResult(
        passed=passed,
        failure_type=failed[0] if failed else None,  # simplified
        failure_detail=", ".join(failed) if failed else None,
        checks_run=len(rules),
        checks_failed=failed,
    )


# --- Phase 4: Full L3a orchestration (Stage 1 + conditional Stages 2/3) ---

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

    Skips expensive model stages when Stage 1 already fails (anti-token-burn §7.3).
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
        # Skip model-based stages (anti-token-burn)
        return result

    # Get thresholds from config
    if config and hasattr(config, "get"):
        eval_cfg = config.get("evaluation", {}) if isinstance(config, dict) else getattr(config, "evaluation", {})
    else:
        eval_cfg = {}

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
