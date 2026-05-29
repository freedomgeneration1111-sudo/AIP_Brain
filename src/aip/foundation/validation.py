"""
L3a Stage 1 deterministic structural validation (pure, zero tokens).
Per Rev 1.3 (unchanged from Rev 1.0).
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


# Default validation rules
DEFAULT_RULES: list[ValidationRule] = [
    ValidationRule(
        rule_id="min_length",
        check=lambda s: len(s) >= 100,
        failure_type="C",
        message="Output must be at least 100 characters.",
        model_gen_assumption=(
            "Models can produce insufficiently detailed output; the length minimum guards against this"
        ),
    ),
    ValidationRule(
        rule_id="no_false_success_patterns",
        check=lambda s: (
            not any(p in s.lower() for p in ["task complete", "all done", "finished successfully"]) or len(s) > 200
        ),
        failure_type="E",
        message="Claims completion without sufficient substance.",
        model_gen_assumption=(
            "Models sometimes claim completion prematurely; the false-success check catches these premature claims"
        ),
    ),
    ValidationRule(
        rule_id="required_section_markers",
        check=lambda s: any(marker in s for marker in ["##", "```", "1.", "Step"]),
        failure_type="C",
        message="Output lacks clear section markers.",
        model_gen_assumption="Malformed output is a common model failure mode; structural markers enforce organization",
    ),
]


def structural_validate(output: str, rules: list[ValidationRule] | None = None) -> ValidationResult:
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


# Backward-compatible alias — new code should import from
# aip.orchestration.l3a_orchestrator  instead.
# This alias preserves backward compatibility for existing callers.
async def full_l3a_evaluation(*args, **kwargs):
    """Moved to orchestration.l3a_orchestrator. This alias preserves backward compat."""
    import importlib

    _mod = importlib.import_module("aip.orchestration.l3a_orchestrator")
    _real = _mod.full_l3a_evaluation
    return await _real(*args, **kwargs)
