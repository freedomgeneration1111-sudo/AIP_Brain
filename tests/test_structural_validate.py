"""Tests for structural_validate (CHUNK-1.2 per Rev 1.3)."""

from aip.foundation.validation import (
    DEFAULT_RULES,
    ValidationRule,
    structural_validate,
)


def test_passes_good_output():
    good = (
        "This is a substantial output with multiple sections.\n\n"
        "## Step 1\nDetails here with enough text to pass the minimum length "
        "requirement of 100 characters easily."
    )
    result = structural_validate(good)
    assert result.passed is True
    assert result.checks_failed == []


def test_fails_min_length():
    short = "Too short."
    result = structural_validate(short)
    assert result.passed is False
    assert "min_length" in result.checks_failed


def test_rules_tagged_with_model_assumption():
    for rule in DEFAULT_RULES:
        assert rule.model_gen_assumption is not None
        assert "§1.8" in rule.model_gen_assumption
