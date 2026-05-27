"""Tests for DEFINER gate stub (CHUNK-1.5 per Rev 1.3)."""

import asyncio

import pytest

from aip.foundation.validation import ValidationResult
from aip.orchestration.nodes.adversarial_eval import EvalResult
from aip.orchestration.nodes.definer_gate import (
    definer_gate,
    DefinerDecision,
    DefinerGateMode,
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


def _make_eval_result(passed=True):
    return EvalResult(
        passed=passed,
        scores={"grounding": 0.85, "completeness": 0.80},
        requires_deep_eval=not passed,
        critique=None if passed else "Failed adversarial checks",
    )


def test_auto_approve_when_both_pass():
    synth = _make_synthesis_output()
    val = _make_validation_result(passed=True)
    ev = _make_eval_result(passed=True)

    result = asyncio.run(definer_gate(synth, val, ev))

    assert isinstance(result, DefinerDecision)
    assert result.action == "approve"
    assert result.approved_by == "stub:auto_approve"
    assert "both passed" in (result.reason or "")


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


def test_manual_mode_raises_not_implemented():
    synth = _make_synthesis_output()
    val = _make_validation_result(passed=True)
    ev = _make_eval_result(passed=True)

    # We only have AUTO_APPROVE_STUB in Phase 1, so we test the guard logic
    # by temporarily using a different mode value
    class FakeMode:
        value = "manual"

    with pytest.raises(NotImplementedError, match="MANUAL"):
        asyncio.run(definer_gate(synth, val, ev, mode=FakeMode()))  # type: ignore

    assert DefinerGateMode.AUTO_APPROVE_STUB.value == "auto_approve_stub"
