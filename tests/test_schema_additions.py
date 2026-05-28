"""Verify Phase 1 schema additions do not break Phase 0.
CHUNK-1.0a gate per Rev 1.3.
"""

import pytest

from aip.foundation.schemas import Chunk, ContractRule, EcsState, FailureType, RetrievalResult
from aip.foundation.protocols import ArtifactStore, EventStore, TraceStore, VectorStore


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


def test_phase0_enums_still_work():
    """Phase 0 enums must not be broken by Phase 1 additions."""
    assert EcsState.GENERATED is not None
    assert FailureType is not None


def test_vectorstore_protocol_has_required_methods():
    """VectorStore must have upsert, retrieve, delete per Delta 1.
    Uses hasattr for deterministic Protocol method detection (per Rev 1.3 R1').
    """
    assert hasattr(VectorStore, "upsert"), "VectorStore missing upsert method"
    assert hasattr(VectorStore, "retrieve"), "VectorStore missing retrieve method"
    assert hasattr(VectorStore, "delete"), "VectorStore missing delete method"
    assert hasattr(VectorStore, "count"), "VectorStore missing count method"
    assert hasattr(VectorStore, "store"), "VectorStore missing store method (Phase 0 compat)"


def test_tracestore_protocol_has_write_event():
    """P1: TraceStore must have write_event method matching CHUNK-1.1 call site."""
    assert hasattr(TraceStore, "write_event"), "TraceStore missing write_event method"


def test_eventstore_protocol_has_write_event():
    """P1: EventStore must have write_event method matching CHUNK-1.6 call site."""
    assert hasattr(EventStore, "write_event"), "EventStore missing write_event method"


def test_artifactstore_protocol_has_write_and_read():
    """P1: ArtifactStore must have write and read methods matching CHUNK-1.6 call sites."""
    assert hasattr(ArtifactStore, "write"), "ArtifactStore missing write method"
    assert hasattr(ArtifactStore, "read"), "ArtifactStore missing read method"
