"""Tests for Adversarial Eval Stub (CHUNK-1.4 per Rev 1.3)."""

import asyncio

from aip.foundation.validation import ValidationResult
from aip.orchestration.nodes.adversarial_eval import (
    DEFAULT_EVAL_CRITERIA,
    EvalResult,
    adversarial_eval,
)
from aip.orchestration.nodes.synthesis import SynthesisOutput


def _make_synthesis_output(content="Stub synthesis output with sections."):
    return SynthesisOutput(
        content=content,
        model_slot="synthesis",
        model_name="stub-model",
        token_count_in=200,
        token_count_out=180,
        latency_ms=120,
    )


def _make_validation_result(passed=True, failure_detail=None):
    return ValidationResult(
        passed=passed,
        failure_type=None if passed else "C",
        failure_detail=failure_detail,
        checks_run=3,
        checks_failed=[] if passed else ["min_length"],
    )


def test_adversarial_eval_returns_correct_structure():
    synth = _make_synthesis_output()
    val = _make_validation_result(passed=True)
    result = asyncio.run(adversarial_eval(synth, val))

    assert isinstance(result, EvalResult)
    assert isinstance(result.passed, bool)
    assert isinstance(result.scores, dict)
    assert isinstance(result.requires_deep_eval, bool)
    assert len(result.scores) == 4


def test_default_criteria_have_model_gen_assumption():
    """Required by §1.8 and green gate checklist."""
    for crit in DEFAULT_EVAL_CRITERIA:
        assert crit.model_gen_assumption is not None
        assert "§1.8" in crit.model_gen_assumption


def test_stub_mode_returns_ci_fixture_flag():
    """Without model_resolver, returns ci_fixture=True and honest 0.0 scores."""
    synth = _make_synthesis_output()
    val = _make_validation_result(passed=True)
    result = asyncio.run(adversarial_eval(synth, val))
    assert isinstance(result, EvalResult)
    assert result.ci_fixture is True
    assert result.passed is False  # Cannot pass without real evaluation
    assert all(score == 0.0 for score in result.scores.values())


def test_stub_mode_failed_validation_flags_deep_eval():
    """Failed L3a validation still flagged for deep eval in stub mode."""
    synth = _make_synthesis_output()
    val = _make_validation_result(passed=False, failure_detail="min_length")
    result = asyncio.run(adversarial_eval(synth, val))
    assert result.requires_deep_eval is True
    assert result.passed is False
    assert result.ci_fixture is True


def test_accepts_custom_criteria():
    synth = _make_synthesis_output()
    val = _make_validation_result(passed=True)
    custom = [DEFAULT_EVAL_CRITERIA[0]]  # only one criterion
    result = asyncio.run(adversarial_eval(synth, val, eval_criteria=custom))
    assert len(result.scores) == 1
    assert result.ci_fixture is True


def test_eval_result_has_ci_fixture_field():
    """EvalResult dataclass includes ci_fixture field."""
    result = EvalResult(passed=False, scores={"grounding": 0.0}, requires_deep_eval=True)
    assert hasattr(result, "ci_fixture")
    assert result.ci_fixture is True  # Default is True (fixture until proven real)
