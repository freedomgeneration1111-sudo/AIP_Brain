"""Core schema dataclasses and protocol methods."""

from aip.foundation.protocols import ArtifactStore, EventStore, TraceStore, VectorStore
from aip.foundation.schemas import Chunk, EcsState, FailureType, RetrievalResult


def test_chunk_dataclass():
    c = Chunk(id="x", content="hello", score=0.9, metadata={"k": "v"}, domain="test")
    assert c.id == "x"
    assert c.score == 0.9


def test_chunk_defaults():
    c = Chunk(id="y")
    assert c.content is None
    assert c.metadata == {}
    assert c.domain is None


def test_retrieval_result_dataclass():
    r = RetrievalResult(status="OK", hits=[], max_confidence=0.0)
    assert r.status == "OK"
    assert r.message is None


def test_retrieval_result_insufficient():
    r = RetrievalResult(status="INSUFFICIENT_MEMORY", hits=[], max_confidence=0.1, message="Low")
    assert r.status == "INSUFFICIENT_MEMORY"


def test_prior_enums_compat():
    """Prior enums must remain importable and functional."""
    assert EcsState.GENERATED is not None
    assert FailureType is not None


def test_vectorstore_protocol_has_required_methods():
    """VectorStore must have upsert, retrieve, delete, count, store methods.
    Uses hasattr for deterministic Protocol method detection.
    """
    assert hasattr(VectorStore, "upsert"), "VectorStore missing upsert method"
    assert hasattr(VectorStore, "retrieve"), "VectorStore missing retrieve method"
    assert hasattr(VectorStore, "delete"), "VectorStore missing delete method"
    assert hasattr(VectorStore, "count"), "VectorStore missing count method"
    assert hasattr(VectorStore, "store"), "VectorStore missing store method"


def test_tracestore_protocol_has_write_event():
    """TraceStore must have write_event method."""
    assert hasattr(TraceStore, "write_event"), "TraceStore missing write_event method"


def test_eventstore_protocol_has_write_event():
    """EventStore must have write_event method."""
    assert hasattr(EventStore, "write_event"), "EventStore missing write_event method"


def test_artifactstore_protocol_has_write_and_read():
    """ArtifactStore must have write and read methods."""
    assert hasattr(ArtifactStore, "write"), "ArtifactStore missing write method"
    assert hasattr(ArtifactStore, "read"), "ArtifactStore missing read method"
