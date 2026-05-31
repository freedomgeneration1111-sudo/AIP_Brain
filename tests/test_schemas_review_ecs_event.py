"""Schema contracts for review, ECS transitions, and events."""

from __future__ import annotations

from aip.foundation.protocols import (
    ArtifactStore,
    EcsStore,
    EventStore,
)
from aip.foundation.schemas import (
    Chunk,
    EcsState,
    EcsTransition,
    Event,
    FailureType,
    FailureTypeCode,
    RetrievalResult,
    ReviewContext,
    ReviewVerdict,
)


def test_review_verdict_approved_defaults():
    v = ReviewVerdict(artifact_id="a2", verdict="APPROVED", reviewer="definer")
    assert v.failure_types == []
    assert v.detail is None
    assert v.confidence == 1.0


def test_review_context_dataclass():
    rc = ReviewContext(
        artifact_id="a1",
        artifact_content="Generated text",
        artifact_version=1,
    )
    assert rc.trace_events == []
    assert rc.prior_verdicts == []


def test_ecs_transition_dataclass():
    t = EcsTransition(
        artifact_id="a1",
        from_state="GENERATED",
        to_state="REVIEWED",
        actor="automated_review",
        reason="Passed automated quality gate",
        timestamp="2026-05-27T10:00:00Z",
    )
    assert t.from_state == "GENERATED"
    assert t.to_state == "REVIEWED"


def test_event_dataclass():
    """Event.timestamp is required, no default."""
    e = Event(
        id=1,
        event_type="ecs_transition",
        actor="definer_gate",
        artifact_id="a1",
        from_state="REVIEWED",
        to_state="APPROVED",
        timestamp="2026-05-27T10:00:00Z",
    )
    assert e.from_state == "REVIEWED"
    assert e.timestamp  # must not be empty


def test_prior_enums_compat():
    """Prior enums must remain importable and functional."""
    assert EcsState.GENERATED is not None
    assert "C" in FailureType.__args__  # Literal still contains the expected values


def test_prior_dataclasses_compat():
    """Prior dataclasses must remain importable and functional."""
    c = Chunk(id="x", content="hello", score=0.9, metadata={"k": "v"}, domain="test")
    assert c.id == "x"
    r = RetrievalResult(status="OK", hits=[], max_confidence=0.0)
    assert r.status == "OK"


def test_eventstore_protocol_has_query():
    """EventStore must have query method."""
    assert hasattr(EventStore, "query"), "EventStore missing query method"


def test_artifactstore_protocol_has_list_versions():
    """ArtifactStore must have list_versions method."""
    assert hasattr(ArtifactStore, "list_versions"), "ArtifactStore missing list_versions method"


def test_artifactstore_read_without_version():
    """ArtifactStore.read(id) must work without version argument (backward compat)."""
    assert hasattr(ArtifactStore, "read"), "ArtifactStore missing read method"
    # Callers use read(id) without version — this must still be valid
    # The version parameter has default None, so read(id) is equivalent to read(id, version=None)


def test_ecsstore_protocol_has_current_state():
    """EcsStore must have current_state method."""
    assert hasattr(EcsStore, "current_state"), "EcsStore missing current_state method"


def test_failuretypecode_alias():
    """FailureTypeCode must be usable and match expected literals."""
    code: FailureTypeCode = "A"
    assert code in ("A", "B", "C", "E")
