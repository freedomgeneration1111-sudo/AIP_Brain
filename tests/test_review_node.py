"""Tests for the review node (CHUNK-4.1)."""
import pytest

from aip.foundation.schemas import ReviewVerdict
from aip.orchestration.review import review_artifact


class FakeArtifactStore:
    def __init__(self):
        self._data = {}

    async def write(self, id, content, metadata):
        self._data[id] = {"content": content, "metadata": metadata, "version": 1}

    async def read(self, id, version=None):
        return self._data.get(id, {}).get("content", "")

    async def list_versions(self, id):
        return [1]


class FakeEcsStore:
    def __init__(self):
        self._state = {}
        self.transitions = []

    async def transition(self, artifact_id, from_state, to_state, actor, reason, superseded_by=None):
        self._state[artifact_id] = to_state
        self.transitions.append({"artifact_id": artifact_id, "from_state": from_state, "to_state": to_state, "actor": actor, "reason": reason, "superseded_by": superseded_by})

    async def current_state(self, artifact_id):
        return self._state.get(artifact_id)


class FakeEventStore:
    def __init__(self):
        self.events = []

    async def write_event(self, event_type, actor, artifact_id, from_state=None, to_state=None, **kwargs):
        self.events.append({"event_type": event_type, "actor": actor, "artifact_id": artifact_id, "from_state": from_state, "to_state": to_state, **kwargs})

    async def query(self, artifact_id=None, event_type=None, limit=100):
        return []


class FakeTraceStore:
    def __init__(self):
        self.events = []

    async def write_event(self, session_id, node_type, failure_type, outcome, detail=None):
        self.events.append({"session_id": session_id, "node_type": node_type, "failure_type": failure_type, "outcome": outcome, "detail": detail})


@pytest.fixture
def stores():
    artifact = FakeArtifactStore()
    ecs = FakeEcsStore()
    events = FakeEventStore()
    trace = FakeTraceStore()
    return artifact, ecs, events, trace


@pytest.mark.asyncio
async def test_automated_review_approves(stores):
    artifact, ecs, events, trace = stores
    await artifact.write("a1", "Good content", {})
    await ecs.transition("a1", None, "GENERATED", "test", "test")

    verdict = await review_artifact("a1", artifact, ecs, events, trace)
    assert verdict.verdict == "APPROVED"
    assert verdict.reviewer == "automated"


@pytest.mark.asyncio
async def test_automated_review_rejects_with_eval(stores):
    artifact, ecs, events, trace = stores
    await artifact.write("a2", "Bad content", {})
    await ecs.transition("a2", None, "GENERATED", "test", "test")

    async def bad_eval(content, artifact_id):
        return {"confidence": 0.3, "failure_types": ["C", "E"], "detail": "Malformed"}

    verdict = await review_artifact("a2", artifact, ecs, events, trace, eval_fn=bad_eval)
    assert verdict.verdict == "REJECTED"
    assert "C" in verdict.failure_types
