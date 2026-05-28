"""Tests for the review node (CHUNK-4.1).

Updated for honest review behavior:
- Without eval_fn in CI mode: APPROVED at reduced confidence (0.70)
- Without eval_fn in production mode: PENDING at confidence=0.0
- With eval_fn returning ci_fixture=True in production: NEEDS_REVISION
- With eval_fn returning ci_fixture=True in CI: proceeds normally
- With real eval_fn: APPROVED/REJECTED/NEEDS_REVISION based on results
"""
import os

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
async def test_automated_review_ci_mode_approves_without_eval_fn(stores, monkeypatch):
    """In CI mode without eval_fn, review returns APPROVED at reduced confidence (fixture)."""
    monkeypatch.setenv("CI", "true")

    artifact, ecs, events, trace = stores
    await artifact.write("a1", "Good content", {})
    await ecs.transition("a1", None, "GENERATED", "test", "test")

    verdict = await review_artifact("a1", artifact, ecs, events, trace)
    assert verdict.verdict == "APPROVED"
    assert verdict.reviewer == "automated"
    assert verdict.confidence == 0.70  # reduced from 1.0
    assert verdict.detail is not None
    assert "CI fixture" in verdict.detail


@pytest.mark.asyncio
async def test_automated_review_production_mode_pending_without_eval_fn(stores, monkeypatch):
    """In production mode without eval_fn, review returns PENDING (not APPROVED)."""
    monkeypatch.delenv("CI", raising=False)

    artifact, ecs, events, trace = stores
    await artifact.write("a_prod", "Some content", {})
    await ecs.transition("a_prod", None, "GENERATED", "test", "test")

    verdict = await review_artifact("a_prod", artifact, ecs, events, trace)
    assert verdict.verdict == "PENDING"
    assert verdict.reviewer == "automated"
    assert verdict.confidence == 0.0
    assert verdict.detail is not None
    assert "No evaluation function" in verdict.detail


@pytest.mark.asyncio
async def test_automated_review_rejects_with_eval(stores, monkeypatch):
    """With eval_fn returning low confidence, review returns REJECTED."""
    monkeypatch.delenv("CI", raising=False)

    artifact, ecs, events, trace = stores
    await artifact.write("a2", "Bad content", {})
    await ecs.transition("a2", None, "GENERATED", "test", "test")

    async def bad_eval(content, artifact_id):
        return {"confidence": 0.3, "failure_types": ["C", "E"], "detail": "Malformed"}

    verdict = await review_artifact("a2", artifact, ecs, events, trace, eval_fn=bad_eval)
    assert verdict.verdict == "REJECTED"
    assert "C" in verdict.failure_types


@pytest.mark.asyncio
async def test_automated_review_approves_with_good_eval(stores, monkeypatch):
    """With eval_fn returning high confidence and no failures, review returns APPROVED."""
    monkeypatch.delenv("CI", raising=False)

    artifact, ecs, events, trace = stores
    await artifact.write("a3", "Good content", {})
    await ecs.transition("a3", None, "GENERATED", "test", "test")

    async def good_eval(content, artifact_id):
        return {"confidence": 0.85, "failure_types": [], "detail": None}

    verdict = await review_artifact("a3", artifact, ecs, events, trace, eval_fn=good_eval)
    assert verdict.verdict == "APPROVED"
    assert verdict.confidence == 0.85


@pytest.mark.asyncio
async def test_automated_review_ci_fixture_blocked_in_production(stores, monkeypatch):
    """With eval_fn returning ci_fixture=True in production, review returns NEEDS_REVISION."""
    monkeypatch.delenv("CI", raising=False)

    artifact, ecs, events, trace = stores
    await artifact.write("a4", "CI fixture content", {})
    await ecs.transition("a4", None, "GENERATED", "test", "test")

    async def ci_fixture_eval(content, artifact_id):
        return {"confidence": 0.85, "failure_types": [], "detail": None, "ci_fixture": True}

    verdict = await review_artifact("a4", artifact, ecs, events, trace, eval_fn=ci_fixture_eval)
    assert verdict.verdict == "NEEDS_REVISION"
    assert "ci_fixture" in verdict.failure_types


@pytest.mark.asyncio
async def test_automated_review_ci_fixture_allowed_in_ci(stores, monkeypatch):
    """With eval_fn returning ci_fixture=True in CI mode, review proceeds normally."""
    monkeypatch.setenv("CI", "true")

    artifact, ecs, events, trace = stores
    await artifact.write("a5", "CI fixture content", {})
    await ecs.transition("a5", None, "GENERATED", "test", "test")

    async def ci_fixture_eval(content, artifact_id):
        return {"confidence": 0.85, "failure_types": [], "detail": None, "ci_fixture": True}

    verdict = await review_artifact("a5", artifact, ecs, events, trace, eval_fn=ci_fixture_eval)
    assert verdict.verdict == "APPROVED"


@pytest.mark.asyncio
async def test_definer_review_ci_mode_approves(stores, monkeypatch):
    """In CI mode, definer review returns APPROVED at reduced confidence."""
    monkeypatch.setenv("CI", "true")

    artifact, ecs, events, trace = stores
    await artifact.write("d1", "Content for definer review", {})
    await ecs.transition("d1", None, "GENERATED", "test", "test")

    config = {"review": {"mode": "definer"}}
    verdict = await review_artifact("d1", artifact, ecs, events, trace, config=config)
    assert verdict.verdict == "APPROVED"
    assert verdict.reviewer == "definer"
    assert verdict.confidence == 0.70  # reduced from 1.0


@pytest.mark.asyncio
async def test_definer_review_production_mode_pending_without_eval(stores, monkeypatch):
    """In production mode without eval_fn, definer review returns PENDING."""
    monkeypatch.delenv("CI", raising=False)

    artifact, ecs, events, trace = stores
    await artifact.write("d2", "Content for definer review", {})
    await ecs.transition("d2", None, "GENERATED", "test", "test")

    config = {"review": {"mode": "definer"}}
    verdict = await review_artifact("d2", artifact, ecs, events, trace, config=config)
    assert verdict.verdict == "PENDING"
    assert verdict.reviewer == "definer"
    assert verdict.confidence == 0.0


@pytest.mark.asyncio
async def test_definer_review_production_ci_fixture_blocked(stores, monkeypatch):
    """In production mode, definer review with CI fixture eval returns NEEDS_REVISION."""
    monkeypatch.delenv("CI", raising=False)

    artifact, ecs, events, trace = stores
    await artifact.write("d3", "Content for definer review", {})
    await ecs.transition("d3", None, "GENERATED", "test", "test")

    async def ci_fixture_eval(content, artifact_id):
        return {"confidence": 0.80, "failure_types": [], "detail": None, "ci_fixture": True}

    config = {"review": {"mode": "definer"}}
    verdict = await review_artifact(
        "d3", artifact, ecs, events, trace,
        eval_fn=ci_fixture_eval, config=config,
    )
    assert verdict.verdict == "NEEDS_REVISION"
    assert "ci_fixture" in verdict.failure_types


@pytest.mark.asyncio
async def test_definer_review_production_with_real_eval_pending(stores, monkeypatch):
    """In production with real eval data (no failures), definer review returns PENDING
    (awaiting DEFINER gate decision)."""
    monkeypatch.delenv("CI", raising=False)

    artifact, ecs, events, trace = stores
    await artifact.write("d4", "Content for definer review", {})
    await ecs.transition("d4", None, "GENERATED", "test", "test")

    async def real_eval(content, artifact_id):
        return {"confidence": 0.85, "failure_types": [], "detail": None, "ci_fixture": False}

    config = {"review": {"mode": "definer"}}
    verdict = await review_artifact(
        "d4", artifact, ecs, events, trace,
        eval_fn=real_eval, config=config,
    )
    assert verdict.verdict == "PENDING"
    assert verdict.reviewer == "definer"
    assert verdict.confidence == 0.85


@pytest.mark.asyncio
async def test_definer_review_production_with_eval_failures(stores, monkeypatch):
    """In production with eval failures, definer review returns NEEDS_REVISION."""
    monkeypatch.delenv("CI", raising=False)

    artifact, ecs, events, trace = stores
    await artifact.write("d5", "Content for definer review", {})
    await ecs.transition("d5", None, "GENERATED", "test", "test")

    async def failing_eval(content, artifact_id):
        return {"confidence": 0.40, "failure_types": ["B"], "detail": "Insufficient", "ci_fixture": False}

    config = {"review": {"mode": "definer"}}
    verdict = await review_artifact(
        "d5", artifact, ecs, events, trace,
        eval_fn=failing_eval, config=config,
    )
    assert verdict.verdict == "NEEDS_REVISION"
    assert "B" in verdict.failure_types
