"""ECS state graph — declarative valid transitions.

Single source of truth for artifact lifecycle state machine.
No storage, no I/O — pure validation logic in foundation layer.
"""

from __future__ import annotations

# Declarative ECS state graph
VALID_TRANSITIONS: dict[str, set[str]] = {
    "SPECIFIED": {"GENERATED"},
    "GENERATED": {"REVIEWED", "FAILED"},
    "REVIEWED": {"APPROVED", "REJECTED"},
    "REJECTED": {"GENERATED"},  # re-synthesis loop
    "APPROVED": {"SUPERSEDED"},
    "FAILED": {"SPECIFIED"},  # re-specify after failure
    "SUPERSEDED": set(),  # terminal state
}

# All known states
ALL_STATES: set[str] = set(VALID_TRANSITIONS.keys())


class InvalidTransitionError(Exception):
    """Raised when an ECS transition violates the state graph.

    This is a controlled rejection, not a crash.
    No action may bypass DEFINER gates.
    The graph makes it structurally impossible to skip states.
    """

    def __init__(self, from_state: str, to_state: str, message: str = ""):
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(message or f"Invalid ECS transition: {from_state} → {to_state}")


def validate_transition(from_state: str, to_state: str) -> None:
    """Validate that a transition is allowed by the state graph.

    Raises InvalidTransitionError if the transition is not valid.
    """
    if from_state not in VALID_TRANSITIONS:
        raise InvalidTransitionError(
            from_state,
            to_state,
            f"Unknown from_state: {from_state!r}. Known states: {sorted(ALL_STATES)}",
        )
    allowed = VALID_TRANSITIONS[from_state]
    if to_state not in allowed:
        raise InvalidTransitionError(
            from_state,
            to_state,
            f"Transition {from_state} → {to_state} not allowed. Allowed from {from_state}: {sorted(allowed)}",
        )


def is_terminal(state: str) -> bool:
    """Return True if the state has no outgoing transitions."""
    return len(VALID_TRANSITIONS.get(state, set())) == 0
