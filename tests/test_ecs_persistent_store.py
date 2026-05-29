"""Tests for ECS persistent store.

Verifies that:
1. Create ECS state.
2. Stop/recreate store instance.
3. Read same ECS state back.
4. Transition history persists.
5. Invalid transition behavior remains unchanged.
6. Existing ECS tests still pass.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from aip.adapter.ecs_store_persistent import PersistentEcsStore
from aip.foundation.ecs_graph import InvalidTransitionError


class FakeEventStore:
    """Minimal EventStore mock for ECS store tests."""

    def __init__(self):
        self.events = []

    async def write_event(self, event_type, actor, artifact_id, from_state=None, to_state=None, **kwargs):
        self.events.append(
            {
                "event_type": event_type,
                "actor": actor,
                "artifact_id": artifact_id,
                "from_state": from_state,
                "to_state": to_state,
            }
        )

    async def query(self, *a, **k):
        return []


@pytest.fixture
def db_path():
    """Provide a temporary database path for each test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


# --- Test: Create ECS state ---


async def test_create_ecs_state(db_path):
    """Can create initial ECS state."""
    store = PersistentEcsStore(db_path)
    await store.initialize()
    await store.transition("art1", None, "SPECIFIED", "definer", "initial specification")
    state = await store.current_state("art1")
    assert state == "SPECIFIED"
    await store.close()


# --- Test: State persists across restart ---


async def test_state_persists_across_restart(db_path):
    """ECS state survives process restart (store close + reopen)."""
    # Create and write
    store1 = PersistentEcsStore(db_path)
    await store1.initialize()
    await store1.transition("art1", None, "SPECIFIED", "definer", "initial specification")
    await store1.transition("art1", "SPECIFIED", "GENERATED", "definer", "generation complete")
    state1 = await store1.current_state("art1")
    assert state1 == "GENERATED"
    await store1.close()

    # Reopen and verify persistence
    store2 = PersistentEcsStore(db_path)
    await store2.initialize()
    state2 = await store2.current_state("art1")
    assert state2 == "GENERATED"
    await store2.close()


# --- Test: Transition history persists ---


async def test_transition_history_persists(db_path):
    """Transition history is preserved across restart."""
    store1 = PersistentEcsStore(db_path)
    await store1.initialize()
    await store1.transition("art1", None, "SPECIFIED", "definer", "spec")
    await store1.transition("art1", "SPECIFIED", "GENERATED", "definer", "gen")
    await store1.close()

    store2 = PersistentEcsStore(db_path)
    await store2.initialize()
    history = await store2.get_transition_history("art1")
    assert len(history) >= 2
    # Most recent first
    assert history[0]["to_state"] == "GENERATED"
    assert history[1]["to_state"] == "SPECIFIED"
    await store2.close()


# --- Test: Invalid transition behavior ---


async def test_invalid_transition_raises(db_path):
    """Invalid transitions still raise InvalidTransitionError."""
    store = PersistentEcsStore(db_path)
    await store.initialize()
    await store.transition("art1", None, "SPECIFIED", "definer", "spec")

    with pytest.raises(InvalidTransitionError):
        await store.transition("art1", "SPECIFIED", "APPROVED", "definer", "skip review")
    await store.close()


async def test_from_state_precondition(db_path):
    """from_state precondition check still works."""
    store = PersistentEcsStore(db_path)
    await store.initialize()
    await store.transition("art1", None, "SPECIFIED", "definer", "spec")

    with pytest.raises(InvalidTransitionError):
        await store.transition("art1", "GENERATED", "REVIEWED", "definer", "wrong precondition")
    await store.close()


# --- Test: Full lifecycle persistence ---


async def test_full_lifecycle_persists(db_path):
    """Full ECS lifecycle: SPECIFIED → GENERATED → REVIEWED → APPROVED."""
    store1 = PersistentEcsStore(db_path)
    await store1.initialize()
    await store1.transition("art1", None, "SPECIFIED", "definer", "spec")
    await store1.transition("art1", "SPECIFIED", "GENERATED", "definer", "gen")
    await store1.transition("art1", "GENERATED", "REVIEWED", "definer", "review")
    await store1.transition("art1", "REVIEWED", "APPROVED", "definer", "approve")
    assert await store1.current_state("art1") == "APPROVED"
    await store1.close()

    store2 = PersistentEcsStore(db_path)
    await store2.initialize()
    assert await store2.current_state("art1") == "APPROVED"
    await store2.close()


# --- Test: Multiple artifacts ---


async def test_multiple_artifacts_persist(db_path):
    """Multiple artifacts all persist across restart."""
    store1 = PersistentEcsStore(db_path)
    await store1.initialize()
    await store1.transition("art1", None, "SPECIFIED", "definer", "spec1")
    await store1.transition("art2", None, "SPECIFIED", "definer", "spec2")
    await store1.transition("art1", "SPECIFIED", "GENERATED", "definer", "gen1")
    await store1.close()

    store2 = PersistentEcsStore(db_path)
    await store2.initialize()
    assert await store2.current_state("art1") == "GENERATED"
    assert await store2.current_state("art2") == "SPECIFIED"
    await store2.close()


# --- Test: EventStore integration ---


async def test_event_store_integration(db_path):
    """ECS store writes transition events to EventStore."""
    events = FakeEventStore()
    store = PersistentEcsStore(db_path, event_store=events)
    await store.initialize()
    await store.transition("art1", None, "SPECIFIED", "definer", "spec")

    assert len(events.events) == 1
    assert events.events[0]["event_type"] == "ecs_transition"
    assert events.events[0]["artifact_id"] == "art1"
    assert events.events[0]["to_state"] == "SPECIFIED"
    await store.close()


# --- Test: EventStore failure does not break transition ---


async def test_event_store_failure_does_not_break_transition(db_path):
    """If EventStore write fails, the transition still succeeds."""

    class FailingEventStore:
        async def write_event(self, **kwargs):
            raise RuntimeError("EventStore unavailable")

        async def query(self, *a, **k):
            return []

    store = PersistentEcsStore(db_path, event_store=FailingEventStore())
    await store.initialize()
    await store.transition("art1", None, "SPECIFIED", "definer", "spec")
    assert await store.current_state("art1") == "SPECIFIED"
    await store.close()


# --- Test: backward compat with GuardrailedEcsStore tests ---


async def test_guardrailed_store_still_works():
    """Ensure the old GuardrailedEcsStore still works for backward compatibility."""
    from aip.adapter.ecs_store_guardrailed import GuardrailedEcsStore
    from aip.foundation.protocols import EcsStore, EventStore

    class FakeEcsStore(EcsStore):
        def __init__(self):
            self._states = {}

        async def transition(self, artifact_id, from_state, to_state, actor, reason, superseded_by=None):
            self._states[artifact_id] = to_state

        async def current_state(self, artifact_id):
            return self._states.get(artifact_id)

    class FakeEventStore(EventStore):
        def __init__(self):
            self.events = []

        async def write_event(self, event_type, actor, artifact_id, from_state=None, to_state=None, **kwargs):
            self.events.append({"event_type": event_type, "to_state": to_state})

        async def query(self, *a, **k):
            return []

    underlying = FakeEcsStore()
    events = FakeEventStore()
    guard = GuardrailedEcsStore(underlying, events)

    await guard.transition("art1", None, "GENERATED", "definer", "gen")
    assert guard._state["art1"] == "GENERATED"
    assert len(events.events) == 1
