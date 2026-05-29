"""Tests for DEFINER gate.

Updated for honest gate behavior:
- CI mode: auto-approve with stub:auto_approve_ci marker
- Production mode: auto-approve with stub:auto_approve marker + warning
- Production mode with ci_fixture=True eval: returns 'revise' (blocks fixture-based approval)
- MANUAL mode: raises ManualReviewRequired with full context for UI integration
"""

import asyncio

import pytest

from aip.foundation.validation import ValidationResult
from aip.orchestration.nodes.adversarial_eval import EvalResult
from aip.orchestration.nodes.definer_gate import (
    DefinerDecision,
    DefinerGateMode,
    ManualReviewRequired,
    definer_gate,
)
from aip.orchestration.nodes.synthesis import SynthesisOutput


def _make_synthesis_output():
    return SynthesisOutput(
        content="Valid synthesis output.",
        model_slot="synthesis",
        model_name="stub-model",
        token_count_in=150,
        token_count_out=140,
        latency_ms=80,
    )


def _make_validation_result(passed=True, detail=None):
    return ValidationResult(
        passed=passed,
        failure_type=None if passed else "C",
        failure_detail=detail,
        checks_run=3,
        checks_failed=[] if passed else ["min_length"],
    )


def _make_eval_result(passed=True, ci_fixture=False):
    return EvalResult(
        passed=passed,
        scores={"grounding": 0.85, "completeness": 0.80},
        requires_deep_eval=not passed,
        critique=None if passed else "Failed adversarial checks",
        ci_fixture=ci_fixture,
    )


# ---------------------------------------------------------------
# AUTO_APPROVE_STUB mode tests (unchanged behavior)
# ---------------------------------------------------------------


def test_auto_approve_when_both_pass_ci_mode(monkeypatch):
    """In CI mode, both pass -> approve with stub:auto_approve_ci marker."""
    monkeypatch.setenv("CI", "true")

    synth = _make_synthesis_output()
    val = _make_validation_result(passed=True)
    ev = _make_eval_result(passed=True)

    result = asyncio.run(definer_gate(synth, val, ev))

    assert isinstance(result, DefinerDecision)
    assert result.action == "approve"
    assert result.approved_by == "stub:auto_approve_ci"
    assert "both passed" in (result.reason or "")


def test_auto_approve_when_both_pass_production_mode(monkeypatch):
    """In production mode, both pass -> approve with stub:auto_approve marker."""
    monkeypatch.delenv("CI", raising=False)

    synth = _make_synthesis_output()
    val = _make_validation_result(passed=True)
    ev = _make_eval_result(passed=True, ci_fixture=False)

    result = asyncio.run(definer_gate(synth, val, ev))

    assert isinstance(result, DefinerDecision)
    assert result.action == "approve"
    assert result.approved_by == "stub:auto_approve"
    assert "both passed" in (result.reason or "")


def test_ci_fixture_eval_blocked_in_production(monkeypatch):
    """In production mode with ci_fixture=True eval -> revise (blocks fixture-based approval)."""
    monkeypatch.delenv("CI", raising=False)

    synth = _make_synthesis_output()
    val = _make_validation_result(passed=True)
    ev = _make_eval_result(passed=True, ci_fixture=True)

    result = asyncio.run(definer_gate(synth, val, ev))

    assert result.action == "revise"
    assert result.approved_by is None
    assert "CI fixture" in (result.reason or "")


def test_ci_fixture_eval_allowed_in_ci(monkeypatch):
    """In CI mode with ci_fixture=True eval -> approve (fixture data allowed in CI)."""
    monkeypatch.setenv("CI", "true")

    synth = _make_synthesis_output()
    val = _make_validation_result(passed=True)
    ev = _make_eval_result(passed=True, ci_fixture=True)

    result = asyncio.run(definer_gate(synth, val, ev))

    assert result.action == "approve"
    assert result.approved_by == "stub:auto_approve_ci"


def test_revise_when_validation_fails():
    synth = _make_synthesis_output()
    val = _make_validation_result(passed=False, detail="min_length")
    ev = _make_eval_result(passed=True)

    result = asyncio.run(definer_gate(synth, val, ev))

    assert result.action == "revise"
    assert "Structural validation failed" in (result.reason or "")
    assert result.approved_by is None


def test_reject_when_validation_passes_but_eval_fails():
    synth = _make_synthesis_output()
    val = _make_validation_result(passed=True)
    ev = _make_eval_result(passed=False)

    result = asyncio.run(definer_gate(synth, val, ev))

    assert result.action == "reject"
    assert "Adversarial evaluation did not pass" in (result.reason or "")
    assert result.approved_by is None


# ---------------------------------------------------------------
# MANUAL mode tests
# ---------------------------------------------------------------


def test_manual_mode_raises_manual_review_required():
    """MANUAL mode raises ManualReviewRequired (not NotImplementedError)."""
    synth = _make_synthesis_output()
    val = _make_validation_result(passed=True)
    ev = _make_eval_result(passed=True)

    with pytest.raises(ManualReviewRequired):
        asyncio.run(definer_gate(synth, val, ev, mode=DefinerGateMode.MANUAL))


def test_manual_mode_exception_carries_context():
    """ManualReviewRequired carries validation/eval context for UI integration."""
    synth = _make_synthesis_output()
    val = _make_validation_result(passed=True)
    ev = _make_eval_result(passed=True, ci_fixture=False)

    with pytest.raises(ManualReviewRequired) as exc_info:
        asyncio.run(definer_gate(synth, val, ev, mode=DefinerGateMode.MANUAL))

    exc = exc_info.value
    assert exc.validation_passed is True
    assert exc.eval_passed is True
    assert exc.eval_is_fixture is False
    assert exc.artifact_summary != ""
    assert "MANUAL" in exc.reason or "human review" in exc.reason.lower()
    assert isinstance(exc.context, dict)
    assert exc.context.get("mode") == "MANUAL"


def test_manual_mode_with_failed_validation():
    """MANUAL mode with failed validation includes that in context."""
    synth = _make_synthesis_output()
    val = _make_validation_result(passed=False, detail="min_length")
    ev = _make_eval_result(passed=True)

    with pytest.raises(ManualReviewRequired) as exc_info:
        asyncio.run(definer_gate(synth, val, ev, mode=DefinerGateMode.MANUAL))

    exc = exc_info.value
    assert exc.validation_passed is False
    assert exc.eval_passed is True
    assert "validation FAILED" in exc.reason or "validation" in exc.reason.lower()
    assert exc.context.get("validation_detail") == "min_length"


def test_manual_mode_with_failed_eval():
    """MANUAL mode with failed evaluation includes that in context."""
    synth = _make_synthesis_output()
    val = _make_validation_result(passed=True)
    ev = _make_eval_result(passed=False, ci_fixture=True)

    with pytest.raises(ManualReviewRequired) as exc_info:
        asyncio.run(definer_gate(synth, val, ev, mode=DefinerGateMode.MANUAL))

    exc = exc_info.value
    assert exc.validation_passed is True
    assert exc.eval_passed is False
    assert "evaluation FAILED" in exc.reason or "evaluation" in exc.reason.lower()
    assert exc.context.get("eval_critique") == "Failed adversarial checks"


def test_manual_mode_with_ci_fixture_in_production(monkeypatch):
    """MANUAL mode with ci_fixture=True in production mentions fixture concern."""
    monkeypatch.delenv("CI", raising=False)

    synth = _make_synthesis_output()
    val = _make_validation_result(passed=True)
    ev = _make_eval_result(passed=True, ci_fixture=True)

    with pytest.raises(ManualReviewRequired) as exc_info:
        asyncio.run(definer_gate(synth, val, ev, mode=DefinerGateMode.MANUAL))

    exc = exc_info.value
    assert exc.validation_passed is True
    assert exc.eval_passed is True
    assert exc.eval_is_fixture is True
    assert "fixture" in exc.reason.lower() or "CI fixture" in exc.reason


def test_manual_mode_artifact_summary():
    """ManualReviewRequired.artifact_summary includes model name and content preview."""
    synth = _make_synthesis_output()
    val = _make_validation_result(passed=True)
    ev = _make_eval_result(passed=True)

    with pytest.raises(ManualReviewRequired) as exc_info:
        asyncio.run(definer_gate(synth, val, ev, mode=DefinerGateMode.MANUAL))

    summary = exc_info.value.artifact_summary
    assert "stub-model" in summary
    assert "synthesis" in summary


def test_manual_review_required_is_exception_subclass():
    """ManualReviewRequired is a proper Exception subclass."""
    assert issubclass(ManualReviewRequired, Exception)


def test_manual_review_required_default_fields():
    """ManualReviewRequired has sensible defaults for all fields."""
    exc = ManualReviewRequired()
    assert exc.artifact_summary == ""
    assert exc.validation_passed is False
    assert exc.eval_passed is False
    assert exc.eval_is_fixture is False
    assert "MANUAL" in exc.reason or "human" in exc.reason.lower()
    assert exc.context == {}


def test_definer_gate_mode_manual_in_enum():
    """DefinerGateMode.MANUAL is a proper enum member."""
    assert DefinerGateMode.MANUAL.value == "manual"
    assert DefinerGateMode.AUTO_APPROVE_STUB.value == "auto_approve_stub"
