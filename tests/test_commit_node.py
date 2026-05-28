"""Tests for Commit stub (CHUNK-1.6 per Rev 1.3)."""

import asyncio

import pytest

from aip.foundation.protocols import ArtifactStore, EcsStore, EventStore
from aip.orchestration.nodes.commit import (
    commit_artifact,
    CommitBlockedError,
    ArtifactRef,
)
from aip.orchestration.nodes.definer_gate import DefinerDecision
from aip.orchestration.nodes.synthesis import SynthesisOutput


# --- Simple Fake Stores for testing ---

class FakeArtifactStore(ArtifactStore):
    def __init__(self):
        self.artifacts = {}

    async def write(self, id: str, content: str, metadata: dict):
        self.artifacts[id] = {"content": content, "metadata": metadata}

    async def read(self, id: str) -> str:
        return self.artifacts[id]["content"]


class FakeEcsStore(EcsStore):
    def __init__(self):
        self._states = {}
        self._transitions = []

    async def transition(self, artifact_id, from_state, to_state, actor, reason, superseded_by=None):
        self._states[artifact_id] = to_state
        self._transitions.append({
            "artifact_id": artifact_id,
            "from_state": from_state,
            "to_state": to_state,
            "actor": actor,
            "reason": reason,
        })

    async def get_state(self, artifact_id):
        return self._states.get(artifact_id)


class FakeEventStore(EventStore):
    def __init__(self):
        self.events = []

    async def write_event(self, event_type, actor, artifact_id, from_state=None, to_state=None, **kwargs):
        self.events.append({
            "event_type": event_type,
            "actor": actor,
            "artifact_id": artifact_id,
            "from_state": from_state,
            "to_state": to_state,
            **kwargs,
        })


def _make_synthesis():
    return SynthesisOutput(
        content="Final approved synthesis content for testing.",
        model_slot="synthesis",
        model_name="stub-model",
        token_count_in=180,
        token_count_out=160,
        latency_ms=95,
    )


def _make_approved_decision():
    return DefinerDecision(
        action="approve",
        reason="Auto-approved: structural validation and adversarial eval both passed.",
        approved_by="stub:auto_approve",
    )


def _make_rejected_decision():
    return DefinerDecision(action="reject", reason="Failed evaluation", approved_by=None)


@pytest.mark.asyncio
async def test_commit_on_approval():
    artifact_store = FakeArtifactStore()
    ecs_store = FakeEcsStore()
    event_store = FakeEventStore()

    result = await commit_artifact(
        _make_synthesis(), _make_approved_decision(), "proj1", "wu1",
        artifact_store, ecs_store, event_store,
    )

    assert isinstance(result, ArtifactRef)
    assert result.ecs_state == "GENERATED"
    assert result.project_id == "proj1"


@pytest.mark.asyncio
async def test_commit_writes_to_artifact_store():
    artifact_store = FakeArtifactStore()
    ecs_store = FakeEcsStore()
    event_store = FakeEventStore()

    result = await commit_artifact(
        _make_synthesis(), _make_approved_decision(), "proj1", "wu1",
        artifact_store, ecs_store, event_store,
    )

    stored = await artifact_store.read(result.artifact_id)
    assert stored is not None


@pytest.mark.asyncio
async def test_commit_transitions_ecs_state():
    artifact_store = FakeArtifactStore()
    ecs_store = FakeEcsStore()
    event_store = FakeEventStore()

    result = await commit_artifact(
        _make_synthesis(), _make_approved_decision(), "proj1", "wu1",
        artifact_store, ecs_store, event_store,
    )

    state = await ecs_store.get_state(result.artifact_id)
    assert state == "GENERATED"


@pytest.mark.asyncio
async def test_r3_ecs_transition_recorded_in_event_log():
    """R3: ECS transition must be recorded in event_log with actor='definer_gate'."""
    artifact_store = FakeArtifactStore()
    ecs_store = FakeEcsStore()
    event_store = FakeEventStore()

    result = await commit_artifact(
        _make_synthesis(), _make_approved_decision(), "proj1", "wu1",
        artifact_store, ecs_store, event_store,
    )

    assert len(event_store.events) == 1
    event = event_store.events[0]
    assert event["event_type"] == "ecs_transition"
    assert event["actor"] == "definer_gate"
    assert event["from_state"] == "SPECIFIED"
    assert event["to_state"] == "GENERATED"
    assert event["artifact_id"] == result.artifact_id


@pytest.mark.asyncio
async def test_p2_ecs_transition_includes_actor_and_reason():
    """P2: ecs_store.transition must be called with actor='definer_gate' and reason='DEFINER approved'."""
    artifact_store = FakeArtifactStore()
    ecs_store = FakeEcsStore()
    event_store = FakeEventStore()

    await commit_artifact(
        _make_synthesis(), _make_approved_decision(), "proj1", "wu1",
        artifact_store, ecs_store, event_store,
    )

    assert len(ecs_store._transitions) == 1
    t = ecs_store._transitions[0]
    assert t["actor"] == "definer_gate"
    assert t["reason"] == "DEFINER approved"
    assert t["from_state"] == "SPECIFIED"
    assert t["to_state"] == "GENERATED"


@pytest.mark.asyncio
async def test_commit_blocked_on_reject():
    artifact_store = FakeArtifactStore()
    ecs_store = FakeEcsStore()
    event_store = FakeEventStore()

    with pytest.raises(CommitBlockedError, match="DEFINER decision was 'reject'"):
        await commit_artifact(
            _make_synthesis(), _make_rejected_decision(), "proj1", "wu1",
            artifact_store, ecs_store, event_store,
        )

    # No event logged on blocked commit
    assert len(event_store.events) == 0


@pytest.mark.asyncio
async def test_deterministic_artifact_id():
    """Same content + project + work_unit = same artifact ID (§1.5 provenance)."""
    stores1 = (FakeArtifactStore(), FakeEcsStore(), FakeEventStore())
    stores2 = (FakeArtifactStore(), FakeEcsStore(), FakeEventStore())

    r1 = await commit_artifact(_make_synthesis(), _make_approved_decision(), "proj1", "wu1", *stores1)
    r2 = await commit_artifact(_make_synthesis(), _make_approved_decision(), "proj1", "wu1", *stores2)

    assert r1.artifact_id == r2.artifact_id
