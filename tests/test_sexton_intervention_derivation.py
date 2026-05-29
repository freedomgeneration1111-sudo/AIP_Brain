"""Tests for Sexton intervention rule derivation.

Verifies that:
1. Known failure condition produces expected intervention.
2. Intervention has severity/reason/action fields.
3. Unsafe action requires approval.
4. Unknown condition returns no recommendation honestly.
5. No placeholder generic intervention remains in active path.
"""

from __future__ import annotations

import pytest

from aip.orchestration.sexton.sexton import Sexton


@pytest.fixture
def sexton():
    return Sexton()


# --- Test: all Appendix E failure types produce interventions ---


def test_type_a_intervention(sexton):
    """Type A (Context Framing Failure) produces correct intervention."""
    result = sexton.derive_intervention_rule("A", {"artifact_id": "art-1", "node_type": "retrieval"})
    assert result is not None
    assert result["failure_type"] == "A"
    assert result["severity"] == "high"
    assert result["proposed_action"] == "strengthen_contract_rule_or_improve_retrieval"
    assert result["requires_approval"] is True
    assert result["reason"] != ""
    assert result["affected_component"] != ""


def test_type_b_intervention(sexton):
    """Type B (Procedural Gap) produces correct intervention."""
    result = sexton.derive_intervention_rule("B", {"artifact_id": "art-2"})
    assert result is not None
    assert result["failure_type"] == "B"
    assert result["severity"] == "medium"
    assert result["requires_approval"] is True


def test_type_c_intervention(sexton):
    """Type C (Output Malformation) produces correct intervention."""
    result = sexton.derive_intervention_rule("C", {"artifact_id": "art-3", "node_type": "synthesis"})
    assert result is not None
    assert result["severity"] == "medium"
    assert result["proposed_action"] == "apply_structural_validation_or_repair"
    assert result["requires_approval"] is False  # Safe to auto-apply


def test_type_d_intervention(sexton):
    """Type D (Session Drift/Loop) produces correct intervention."""
    result = sexton.derive_intervention_rule("D", {"artifact_id": "art-4"})
    assert result is not None
    assert result["severity"] == "high"
    assert result["proposed_action"] == "trigger_context_reset_or_l4_intervention"


def test_type_e_intervention(sexton):
    """Type E (False Success Reporting) produces CRITICAL intervention."""
    result = sexton.derive_intervention_rule("E", {"artifact_id": "art-5"})
    assert result is not None
    assert result["severity"] == "critical"
    assert result["requires_approval"] is True  # Must NOT auto-commit


def test_type_f_intervention(sexton):
    """Type F (Context Anxiety) produces correct intervention."""
    result = sexton.derive_intervention_rule("F", {"artifact_id": "art-6"})
    assert result is not None
    assert result["severity"] == "medium"
    assert result["proposed_action"] == "trigger_context_reset_or_l4_intervention"


# --- Test: special conditions produce interventions ---


def test_repeated_evaluation_failure(sexton):
    """Repeated evaluation failure produces high-severity intervention."""
    result = sexton.derive_intervention_rule(
        "A",
        {"artifact_id": "art-7", "condition": "repeated_evaluation_failure"},
    )
    assert result is not None
    assert result["severity"] == "high"
    assert result["requires_approval"] is True
    assert "repeated" in result["reason"].lower() or "systemic" in result["reason"].lower()


def test_fixture_leakage_attempt(sexton):
    """Fixture leakage attempt produces critical intervention."""
    result = sexton.derive_intervention_rule(
        "E",
        {"artifact_id": "art-8", "condition": "fixture_leakage_attempt"},
    )
    assert result is not None
    assert result["severity"] == "critical"
    assert result["requires_approval"] is True
    assert "fixture" in result["reason"].lower()


def test_canonical_promotion_blocked(sexton):
    """Canonical promotion blocked produces intervention requiring approval."""
    result = sexton.derive_intervention_rule(
        "B",
        {"artifact_id": "art-9", "condition": "canonical_promotion_blocked"},
    )
    assert result is not None
    assert result["requires_approval"] is True


def test_model_slot_drift(sexton):
    """Model slot drift produces high-severity intervention."""
    result = sexton.derive_intervention_rule(
        "A",
        {"artifact_id": "art-10", "condition": "model_slot_drift"},
    )
    assert result is not None
    assert result["severity"] == "high"
    assert result["proposed_action"] == "trigger_vigil_re_evaluation"
    assert result["requires_approval"] is False  # Re-evaluation is safe


def test_budget_breach(sexton):
    """Budget breach produces high-severity intervention requiring approval."""
    result = sexton.derive_intervention_rule(
        "B",
        {"artifact_id": "art-11", "condition": "budget_breach"},
    )
    assert result is not None
    assert result["severity"] == "high"
    assert result["requires_approval"] is True


def test_auth_security_violation(sexton):
    """Auth/security violation produces critical intervention."""
    result = sexton.derive_intervention_rule(
        "B",
        {"artifact_id": "art-12", "condition": "auth_security_violation"},
    )
    assert result is not None
    assert result["severity"] == "critical"
    assert result["requires_approval"] is True
    assert "security" in result["reason"].lower() or "auth" in result["reason"].lower()


def test_workflow_stuck_approval(sexton):
    """Workflow stuck waiting for approval produces medium-severity notification."""
    result = sexton.derive_intervention_rule(
        "B",
        {"artifact_id": "art-13", "condition": "workflow_stuck_approval"},
    )
    assert result is not None
    assert result["severity"] == "medium"
    assert result["proposed_action"] == "notify_definer_of_pending_review"
    assert result["requires_approval"] is False  # Notification is safe


# --- Test: unknown condition returns None honestly ---


def test_unknown_failure_type_returns_none(sexton):
    """Unknown failure type returns None (no fake intervention)."""
    result = sexton.derive_intervention_rule("Z", {"artifact_id": "art-99"})
    assert result is None


def test_unknown_condition_returns_type_based(sexton):
    """Unknown condition with known failure type falls through to type-based rule."""
    result = sexton.derive_intervention_rule("A", {"artifact_id": "art-99", "condition": "unknown_condition"})
    # Should return the Type A intervention (not None), since failure_type is known
    assert result is not None
    assert result["failure_type"] == "A"


def test_completely_unknown_returns_none(sexton):
    """A completely unknown failure type with no matching condition returns None."""
    result = sexton.derive_intervention_rule("X", {"condition": "nonexistent"})
    assert result is None


# --- Test: all interventions have required fields ---


def test_all_type_interventions_have_required_fields(sexton):
    """Every intervention for failure types A-F has all required fields."""
    required_fields = [
        "intervention_id",
        "failure_type",
        "reason",
        "severity",
        "affected_component",
        "proposed_action",
        "requires_approval",
    ]

    for ft in ["A", "B", "C", "D", "E", "F"]:
        result = sexton.derive_intervention_rule(ft, {"artifact_id": "test"})
        assert result is not None, f"Missing intervention for failure type {ft}"
        for field in required_fields:
            assert field in result, f"Missing field '{field}' in intervention for type {ft}"
            assert result[field] is not None, f"Field '{field}' is None in intervention for type {ft}"


# --- Test: no placeholder interventions ---


def test_no_placeholder_interventions(sexton):
    """Interventions should not contain vague/placeholder language."""
    for ft in ["A", "B", "C", "D", "E", "F"]:
        result = sexton.derive_intervention_rule(ft, {"artifact_id": "test"})
        assert result is not None
        assert len(result["reason"]) > 30, f"Type {ft} reason is too short/vague"
        assert result["proposed_action"] not in ("", "none", "TODO", "placeholder")


# --- Test: model_gen_assumption on all interventions ---


def test_all_interventions_have_model_gen_assumption(sexton):
    """Every intervention carries a model_gen_assumption tag."""
    for ft in ["A", "B", "C", "D", "E", "F"]:
        result = sexton.derive_intervention_rule(ft, {"artifact_id": "test"})
        assert result is not None
        assert "model_gen_assumption" in result
        assert result["model_gen_assumption"] is not None
        assert len(result["model_gen_assumption"]) > 20
