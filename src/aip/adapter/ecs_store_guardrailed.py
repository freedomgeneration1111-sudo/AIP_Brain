"""GuardrailedEcsStore — ECS store with graph validation.

Adapter layer: composes EcsStore protocol with EventStore protocol
and the validate_transition function from foundation.
"""
from __future__ import annotations

from datetime import datetime, timezone

from aip.foundation.ecs_graph import InvalidTransitionError, validate_transition
from aip.foundation.protocols import EventStore, EcsStore


class GuardrailedEcsStore(EcsStore):
    """ECS store that validates every transition against the state graph.

    Wraps an underlying EcsStore (dict-backed fake in CI, SQLite in prod).
    Records every transition as an EventStore event for provenance.
    """

    def __init__(
        self,
        underlying: EcsStore,
        event_store: EventStore,
    ) -> None:
        self._underlying = underlying
        self._event_store = event_store
        # In-memory state cache for CI; production uses SQLite
        self._state: dict[str, str] = {}

    async def transition(
        self,
        artifact_id: str,
        from_state: str | None,
        to_state: str,
        actor: str,
        reason: str,
        superseded_by: str | None = None,
    ) -> None:
        """Transition artifact between ECS states with guardrail validation.

        S1 fix: superseded_by param preserved from Phase 1 signature.

        1. Query current state
        2. Assert from_state precondition if provided
        3. Validate transition against state graph
        4. Write transition (pass superseded_by through)
        5. Record event in EventStore
        """
        current = self._state.get(artifact_id)

        # from_state precondition check
        if from_state is not None and current != from_state:
            raise InvalidTransitionError(
                current or "NONE",
                to_state,
                f"Precondition failed: expected {from_state!r}, "
                f"but artifact {artifact_id!r} is in {current!r}",
            )

        # Guardrail validation
        if current is not None:
            validate_transition(current, to_state)

        # Write transition to underlying store (superseded_by forwarded)
        await self._underlying.transition(
            artifact_id=artifact_id,
            from_state=current,
            to_state=to_state,
            actor=actor,
            reason=reason,
            superseded_by=superseded_by,
        )

        # Update state cache
        self._state[artifact_id] = to_state

        # Record event for provenance
        await self._event_store.write_event(
            event_type="ecs_transition",
            actor=actor,
            artifact_id=artifact_id,
            from_state=current,
            to_state=to_state,
            reason=reason,
        )

    async def current_state(self, artifact_id: str) -> str | None:
        """Return current ECS state for an artifact."""
        return self._state.get(artifact_id)
