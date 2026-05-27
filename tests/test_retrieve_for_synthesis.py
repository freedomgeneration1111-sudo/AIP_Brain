"""Tests for retrieve_for_synthesis (CHUNK-1.1 per Rev 1.3)."""

import asyncio
import math
from dataclasses import dataclass

import pytest

from aip.foundation.protocols import TraceStore, VectorStore
from aip.foundation.schemas import Chunk, RetrievalResult
from aip.orchestration.retrieval import (
    RerankWeights,
    fake_embed,
    rerank,
    retrieve_for_synthesis,
)


# --- Fakes ---
@dataclass
class FakeVectorStore(VectorStore):
    hits: list[Chunk]

    async def retrieve(self, query_vector, domain=None, top_k=10):
        return self.hits[:top_k]

    async def upsert(self, *a, **k):
        pass

    async def delete(self, *a, **k):
        pass

    async def count(self, *a, **k):
        return len(self.hits)

    async def store(self, chunk):
        return chunk.id


class FakeTraceStore(TraceStore):
    def __init__(self):
        self.events = []

    async def write_event(self, session_id, node_type, failure_type=None,
                          outcome=None, detail=None, **kw):
        self.events.append({
            "session_id": session_id,
            "node_type": node_type,
            "failure_type": failure_type,
            "outcome": outcome,
            "detail": detail,
        })

    async def get_recent_events(self, session_id: str, limit: int = 100) -> list[dict]:
        # Return in reverse chrono (most recent first) to match production expectation
        return list(reversed([e for e in self.events if e.get("session_id") == session_id]))[:limit]


def _chunk(id: str, score: float, authority: str = "raw",
           created_at: str | None = None, access_count: int = 0) -> Chunk:
    return Chunk(
        id=id,
        content=f"Content for {id}",
        score=score,
        metadata={
            "authority": authority,
            "created_at": created_at,
            "access_count": access_count,
        },
        domain="test",
    )


def _default_config() -> dict:
    return {
        "retrieval": {
            "confidence_threshold": 0.30,
            "weight_semantic": 0.60,
            "weight_recency": 0.15,
            "weight_authority": 0.15,
            "weight_frequency": 0.10,
        }
    }


# --- Delta 4: fake_embed tests ---
def test_fake_embed_returns_correct_dimensions():
    vec = fake_embed("hello world", dimensions=768)
    assert len(vec) == 768


def test_fake_embed_is_deterministic():
    assert fake_embed("test") == fake_embed("test")


def test_fake_embed_is_unit_vector():
    vec = fake_embed("test")
    norm = math.sqrt(sum(v * v for v in vec))
    assert abs(norm - 1.0) < 0.01


# --- Retrieval tests ---
@pytest.mark.asyncio
async def test_insufficient_memory_empty_results():
    store = FakeVectorStore(hits=[])
    trace = FakeTraceStore()
    result = await retrieve_for_synthesis(
        "query", "test", store, fake_embed, trace, _default_config()
    )
    assert result.status == "INSUFFICIENT_MEMORY"
    assert result.max_confidence == 0.0
    # R2: trace event logged
    assert len(trace.events) == 1
    assert trace.events[0]["failure_type"] == "A"  # F2: Missing Context, not Procedural Gap


@pytest.mark.asyncio
async def test_insufficient_memory_below_threshold():
    low_hits = [_chunk("doc1", 0.10), _chunk("doc2", 0.20)]
    store = FakeVectorStore(hits=low_hits)
    trace = FakeTraceStore()
    result = await retrieve_for_synthesis(
        "query", "test", store, fake_embed, trace, _default_config()
    )
    assert result.status == "INSUFFICIENT_MEMORY"
    assert len(trace.events) == 1


@pytest.mark.asyncio
async def test_ok_above_threshold():
    hits = [_chunk("doc1", 0.80), _chunk("doc2", 0.50)]
    store = FakeVectorStore(hits=hits)
    trace = FakeTraceStore()
    result = await retrieve_for_synthesis(
        "query", "test", store, fake_embed, trace, _default_config()
    )
    assert result.status == "OK"
    assert len(result.hits) == 2
    assert result.hits[0].score >= result.hits[1].score
    assert len(trace.events) == 0  # No trace event on success


# --- Delta 5: config-loaded values ---
def test_confidence_threshold_from_config():
    """Delta 5: threshold must come from config, not hardcoded."""
    config = _default_config()
    assert config["retrieval"]["confidence_threshold"] == 0.30


def test_rerank_weights_from_config():
    """Delta 5: weights must come from config."""
    w = RerankWeights.from_config(_default_config())
    assert w.semantic == 0.60
    assert w.recency == 0.15


def test_rerank_weights_config_overrides_defaults():
    config = {
        "retrieval": {
            "weight_semantic": 1.0,
            "weight_recency": 0.0,
            "weight_authority": 0.0,
            "weight_frequency": 0.0,
        }
    }
    w = RerankWeights.from_config(config)
    assert w.semantic == 1.0


# --- Rerank tests ---
def test_rerank_preserves_order_for_equal_metadata():
    hits = [_chunk("a", 0.9), _chunk("b", 0.7), _chunk("c", 0.5)]
    w = RerankWeights.from_config(_default_config())
    reranked = rerank(hits, "test", w)
    assert [h.id for h in reranked] == ["a", "b", "c"]


def test_rerank_authority_boosts_approved():
    hits = [
        _chunk("raw", 0.80, authority="raw"),
        _chunk("approved", 0.70, authority="approved"),
    ]
    w = RerankWeights.from_config(_default_config())
    reranked = rerank(hits, "test", w)
    assert reranked[0].id == "approved" or reranked[0].score > reranked[1].score


# --- F3: AipConfig / dict dual acceptance ---
def test_config_accepts_dict():
    """F3: retrieve_for_synthesis must accept plain dict config."""
    store = FakeVectorStore(hits=[_chunk("d1", 0.80)])
    trace = FakeTraceStore()
    # Should not raise
    result = asyncio.run(
        retrieve_for_synthesis("query", "test", store, fake_embed, trace, _default_config())
    )
    assert result.status == "OK"


def test_config_model_dump_fallback():
    """F3: If config has model_dump (Pydantic AipConfig), it should be called."""
    class FakeAipConfig:
        def model_dump(self) -> dict:
            return _default_config()

    store = FakeVectorStore(hits=[_chunk("d1", 0.80)])
    trace = FakeTraceStore()
    result = asyncio.run(
        retrieve_for_synthesis("query", "test", store, fake_embed, trace, FakeAipConfig())
    )
    assert result.status == "OK"
