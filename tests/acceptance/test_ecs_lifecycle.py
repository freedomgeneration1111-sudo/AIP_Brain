"""ECS lifecycle acceptance tests.

Tests the full ECS lifecycle: SPECIFIED → GENERATED → REVIEWED → APPROVED
per §9.3 state machine. Verifies that:
- Each transition is valid
- Invalid transitions are rejected
- Terminal states are correctly identified
- The state graph is structurally sound
"""

import pytest


def test_ecs_graph_importable():
    """ECS graph module is importable."""
    from aip.foundation.ecs_graph import ALL_STATES, VALID_TRANSITIONS

    assert len(VALID_TRANSITIONS) > 0
    assert "SPECIFIED" in ALL_STATES
    assert "SUPERSEDED" in ALL_STATES


def test_full_happy_path_lifecycle():
    """The full SPECIFIED → GENERATED → REVIEWED → APPROVED path is valid."""
    from aip.foundation.ecs_graph import validate_transition

    validate_transition("SPECIFIED", "GENERATED")
    validate_transition("GENERATED", "REVIEWED")
    validate_transition("REVIEWED", "APPROVED")


def test_rejection_loop():
    """REVIEWED → REJECTED → GENERATED loop is valid."""
    from aip.foundation.ecs_graph import validate_transition

    validate_transition("REVIEWED", "REJECTED")
    validate_transition("REJECTED", "GENERATED")


def test_failure_loop():
    """GENERATED → FAILED → SPECIFIED loop is valid."""
    from aip.foundation.ecs_graph import validate_transition

    validate_transition("GENERATED", "FAILED")
    validate_transition("FAILED", "SPECIFIED")


def test_supersession_is_terminal():
    """SUPERSEDED is a terminal state with no outgoing transitions."""
    from aip.foundation.ecs_graph import is_terminal

    assert is_terminal("SUPERSEDED") is True


def test_no_skip_states():
    """Cannot skip states in the lifecycle (e.g., SPECIFIED → APPROVED)."""
    from aip.foundation.ecs_graph import InvalidTransitionError, validate_transition

    with pytest.raises(InvalidTransitionError):
        validate_transition("SPECIFIED", "APPROVED")

    with pytest.raises(InvalidTransitionError):
        validate_transition("SPECIFIED", "REVIEWED")

    with pytest.raises(InvalidTransitionError):
        validate_transition("GENERATED", "APPROVED")


def test_ecs_state_enum_covers_core_states():
    """EcsState enum covers the core lifecycle states."""
    from aip.foundation.schemas import EcsState

    enum_values = {s.value for s in EcsState}
    # Core states that must be present in the enum
    core_states = {"SPECIFIED", "GENERATED", "REVIEWED", "APPROVED", "SUPERSEDED", "FAILED"}
    assert core_states.issubset(enum_values)


def test_ecs_transition_record_has_required_fields():
    """EcsTransition dataclass has all required fields per §1.5."""
    from aip.foundation.schemas import EcsTransition

    t = EcsTransition(
        artifact_id="test-artifact",
        from_state="SPECIFIED",
        to_state="GENERATED",
        actor="synthesis_node",
        reason="Initial generation",
        timestamp="2025-01-01T00:00:00Z",
    )
    assert t.artifact_id == "test-artifact"
    assert t.from_state == "SPECIFIED"
    assert t.to_state == "GENERATED"
    assert t.actor == "synthesis_node"
    assert t.reason == "Initial generation"


def test_approved_to_superseded():
    """APPROVED → SUPERSEDED is valid (canonical supersession)."""
    from aip.foundation.ecs_graph import validate_transition

    validate_transition("APPROVED", "SUPERSEDED")


def test_no_backward_from_approved():
    """Cannot go backwards from APPROVED (only SUPERSEDED is valid)."""
    from aip.foundation.ecs_graph import InvalidTransitionError, validate_transition

    with pytest.raises(InvalidTransitionError):
        validate_transition("APPROVED", "REVIEWED")

    with pytest.raises(InvalidTransitionError):
        validate_transition("APPROVED", "GENERATED")

    with pytest.raises(InvalidTransitionError):
        validate_transition("APPROVED", "SPECIFIED")
