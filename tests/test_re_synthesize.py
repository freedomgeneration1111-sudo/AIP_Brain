"""Tests for the re-synthesis loop."""

import pytest

from aip.foundation.schemas import ReviewVerdict
from aip.orchestration.re_synthesize import build_failure_context, re_synthesize


# Self-contained fakes for the test
class FakeArtifactStore:
    def __init__(self):
        self._versions = {}

    async def write(self, id, content, metadata):
        if id not in self._versions:
            self._versions[id] = []
        self._versions[id].append(content)

    async def read(self, id, version=None):
        versions = self._versions.get(id, [])
        if not versions:
            return ""
        if version is None:
            return versions[-1]
        return versions[version - 1] if version <= len(versions) else ""

    async def list_versions(self, id):
        return list(range(1, len(self._versions.get(id, [])) + 1))


class FakeEcsStore:
    def __init__(self):
        self._state = {}
        self.transitions = []

    async def transition(self, artifact_id, from_state, to_state, actor, reason, superseded_by=None):
        self._state[artifact_id] = to_state
        self.transitions.append({"artifact_id": artifact_id, "to_state": to_state})

    async def current_state(self, artifact_id):
        return self._state.get(artifact_id)


class FakeEventStore:
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
                **kwargs,
            },
        )

    async def query(self, artifact_id=None, event_type=None, limit=100):
        filtered = [
            e
            for e in self.events
            if (not artifact_id or e.get("artifact_id") == artifact_id)
            and (not event_type or e.get("event_type") == event_type)
        ]
        return filtered[-limit:]


class FakeTraceStore:
    def __init__(self):
        self.events = []

    async def write_event(self, session_id, node_type, failure_type, outcome, detail=None):
        self.events.append(
            {
                "session_id": session_id,
                "node_type": node_type,
                "failure_type": failure_type,
                "outcome": outcome,
                "detail": detail,
            },
        )


@pytest.fixture
def stores():
    artifact = FakeArtifactStore()
    ecs = FakeEcsStore()
    events = FakeEventStore()
    trace = FakeTraceStore()
    return artifact, ecs, events, trace


def test_build_failure_context():
    rejection = ReviewVerdict(
        artifact_id="a1",
        verdict="REJECTED",
        reviewer="test",
        failure_types=["A", "C"],
        detail="Bad",
    )
    ctx = build_failure_context(rejection, "old content")
    assert any("lacked sufficient context" in instr for instr in ctx["correction_instructions"])
    assert any("conform to the required format" in instr for instr in ctx["correction_instructions"])
    assert ctx["prior_content"] == "old content"


@pytest.mark.asyncio
async def test_re_synthesize_basic_flow(stores):
    artifact, ecs, events, trace = stores
    rejection = ReviewVerdict(
        artifact_id="a1",
        verdict="REJECTED",
        reviewer="review",
        failure_types=["A"],
        detail="missing context",
    )

    async def fake_synth(artifact_id, failure_context):
        return "Re-synthesized content with more context"

    await artifact.write("a1", "bad version", {})
    await ecs.transition("a1", None, "REJECTED", "test", "rejected")

    verdict = await re_synthesize(
        "a1",
        rejection,
        artifact,
        ecs,
        events,
        trace,
        fake_synth,
        {"review": {"max_rejection_retries": 3}},
    )

    assert verdict.verdict == "NEEDS_REVISION"
    assert len(events.events) >= 1  # attempt recorded
    assert "GENERATED" in [t["to_state"] for t in ecs.transitions]
