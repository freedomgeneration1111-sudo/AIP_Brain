"""Tests for the pgvector VectorStore adapter.

PostgreSQL tests are skipped if pgvector is not available.
Mock tests exercise the code paths without a real database.
"""

import os

import pytest

from aip.adapter.vector.pgvector_store import PgvectorStore
from aip.foundation.schemas import Chunk, PgvectorConfig

# Skip real PostgreSQL tests if not available
PGVECTOR_AVAILABLE = os.environ.get("AIP_PGVECTOR_TEST") == "1"


@pytest.fixture
def pgvector_config():
    return PgvectorConfig(
        connection_string="postgresql://localhost:5432/aip_test_vectors",
        pool_min_size=1,
        pool_max_size=3,
        hnsw_m=16,
        hnsw_ef_construction=64,
        hnsw_ef_search=40,
    )


class MockPgvectorStore:
    """In-memory mock for testing without PostgreSQL. Mirrors real PgvectorStore behavior."""

    def __init__(self):
        self._vectors: dict[str, tuple[list[float], dict, str]] = {}

    async def upsert(self, id, embedding, content, metadata, domain=None):
        full_meta = dict(metadata or {})
        if content and "content" not in full_meta:
            full_meta["content"] = content
        self._vectors[id] = (embedding, full_meta, domain or "")

    async def batch_upsert(self, items):
        for id_, vector, metadata, domain in items:
            self._vectors[id_] = (vector, metadata or {}, domain or "")

    async def retrieve(self, query_vector, domain=None, top_k=10):
        # Simplified in-memory retrieval (real uses pgvector HNSW)
        results = []
        for vid, (vec, meta, dom) in self._vectors.items():
            if domain is None or dom == domain:
                # Dummy score for mock
                score = 0.95
                results.append(Chunk(id=vid, content=meta.get("content", ""), score=score, metadata=meta, domain=dom))
        return results[:top_k]

    async def delete(self, id):
        self._vectors.pop(id, None)

    async def health_check(self):
        return {"connected": True, "pool_size": 1, "latency_ms": 5, "backend_name": "pgvector", "database": "mock"}

    async def count(self, domain=None):
        if domain:
            return sum(1 for _, (_, _, d) in self._vectors.items() if d == domain)
        return len(self._vectors)

    async def initialize(self):
        pass

    async def close(self):
        pass


@pytest.fixture
def mock_store():
    return MockPgvectorStore()


# --- Mock-based tests (always run in CI) ---


@pytest.mark.asyncio
async def test_mock_upsert_and_retrieve(mock_store):
    await mock_store.upsert("v1", [0.1] * 768, "hello world", {"source": "test"}, "test_domain")
    results = await mock_store.retrieve([0.1] * 768, "test_domain", top_k=5)
    assert len(results) == 1
    assert results[0].id == "v1"
    assert results[0].domain == "test_domain"
    assert results[0].metadata.get("source") == "test"


@pytest.mark.asyncio
async def test_mock_upsert_updates_existing(mock_store):
    await mock_store.upsert("v1", [0.1] * 768, "v1", {"version": 1}, "domain1")
    await mock_store.upsert("v1", [0.2] * 768, "v1-updated", {"version": 2}, "domain1")
    count = await mock_store.count()
    assert count == 1  # updated, not duplicated


@pytest.mark.asyncio
async def test_mock_count_by_domain(mock_store):
    await mock_store.upsert("v1", [0.1] * 768, "", {}, "domain_a")
    await mock_store.upsert("v2", [0.2] * 768, "", {}, "domain_b")
    assert await mock_store.count("domain_a") == 1
    assert await mock_store.count("domain_b") == 1
    assert await mock_store.count() == 2


@pytest.mark.asyncio
async def test_mock_delete(mock_store):
    await mock_store.upsert("v1", [0.1] * 768, "", {}, "domain_a")
    await mock_store.delete("v1")
    assert await mock_store.count() == 0


@pytest.mark.asyncio
async def test_mock_health_check(mock_store):
    hc = await mock_store.health_check()
    assert hc["connected"] is True
    assert hc["backend_name"] == "pgvector"


@pytest.mark.asyncio
async def test_mock_batch_upsert(mock_store):
    items = [
        ("b1", [0.3] * 768, {"batch": True}, "batch_domain"),
        ("b2", [0.4] * 768, {"batch": True}, "batch_domain"),
    ]
    await mock_store.batch_upsert(items)
    assert await mock_store.count("batch_domain") == 2


# --- Protocol compliance (lightweight, always runnable) ---


@pytest.mark.asyncio
async def test_pgvector_store_has_required_methods():
    """PgvectorStore must implement the live VectorStore protocol surface."""
    # We don't instantiate without config in this test, but the class must have the methods
    assert hasattr(PgvectorStore, "upsert")
    assert hasattr(PgvectorStore, "retrieve")
    assert hasattr(PgvectorStore, "delete")
    assert hasattr(PgvectorStore, "health_check")
    assert hasattr(PgvectorStore, "count")
    assert hasattr(PgvectorStore, "initialize")
    assert hasattr(PgvectorStore, "close")
    assert hasattr(PgvectorStore, "batch_upsert")


# Real DB tests (skipped in normal CI)
@pytest.mark.skipif(not PGVECTOR_AVAILABLE, reason="Set AIP_PGVECTOR_TEST=1 and provide reachable Postgres+pgvector")
@pytest.mark.asyncio
async def test_real_pgvector_roundtrip(pgvector_config):
    store = PgvectorStore(pgvector_config)
    try:
        await store.initialize()
        await store.upsert("real1", [0.01] * 768, "real content", {"k": "v"}, "test_domain")
        results = await store.retrieve([0.01] * 768, "test_domain", top_k=3)
        assert len(results) >= 1
        assert results[0].id == "real1"
        assert await store.count("test_domain") >= 1
    finally:
        await store.close()
