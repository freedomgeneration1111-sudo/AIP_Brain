"""Tests for SqliteVssVectorStore.
Requires sqlite_vss extension to be available.
All tests run locally with no network calls."""

import math
import pytest

try:
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    conn.load_extension("vss0")
    conn.close()
    HAS_VSS = True
except Exception:
    HAS_VSS = False

pytestmark = pytest.mark.skipif(not HAS_VSS, reason="sqlite_vss not available")

from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore
from aip.foundation.schemas import Chunk


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test_vectors.db")
    s = SqliteVssVectorStore(db_path=db_path, dimensions=4)
    yield s
    s.close()


def _unit(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values] if norm > 0 else values


@pytest.mark.asyncio
async def test_upsert_and_retrieve(store):
    emb1 = _unit([1.0, 0.0, 0.0, 0.0])
    emb2 = _unit([0.0, 1.0, 0.0, 0.0])
    await store.upsert("doc1", emb1, content="Document one", domain="test", metadata={"k": "v"})
    await store.upsert("doc2", emb2, content="Document two", domain="test")
    results = await store.retrieve(emb1, domain="test", top_k=2)
    assert len(results) >= 1
    assert isinstance(results[0], Chunk)
    assert results[0].id == "doc1"
    assert results[0].content == "Document one"
    assert results[0].score > 0.5


@pytest.mark.asyncio
async def test_domain_filter(store):
    emb = _unit([1.0, 0.0, 0.0, 0.0])
    await store.upsert("doc_a", emb, content="Alpha", domain="alpha")
    await store.upsert("doc_b", emb, content="Beta", domain="beta")
    results_alpha = await store.retrieve(emb, domain="alpha", top_k=10)
    assert all(r.domain == "alpha" for r in results_alpha)


@pytest.mark.asyncio
async def test_upsert_overwrite(store):
    emb = _unit([1.0, 0.0, 0.0, 0.0])
    await store.upsert("doc1", emb, content="Version 1")
    await store.upsert("doc1", emb, content="Version 2")
    assert await store.count() == 1


@pytest.mark.asyncio
async def test_delete(store):
    emb = _unit([1.0, 0.0, 0.0, 0.0])
    await store.upsert("doc1", emb, content="To delete")
    await store.delete("doc1")
    assert await store.count() == 0


@pytest.mark.asyncio
async def test_count_by_domain(store):
    emb = _unit([1.0, 0.0, 0.0, 0.0])
    await store.upsert("d1", emb, content="X", domain="x")
    await store.upsert("d2", emb, content="Y", domain="x")
    await store.upsert("d3", emb, content="Z", domain="y")
    assert await store.count(domain="x") == 2
    assert await store.count(domain="y") == 1
    assert await store.count() == 3


@pytest.mark.asyncio
async def test_retrieve_returns_chunk_type(store):
    """Delta 1: VectorStore.retrieve must return list[Chunk], not list[RetrievalHit]."""
    emb = _unit([1.0, 0.0, 0.0, 0.0])
    await store.upsert("d1", emb, content="Test", domain="test")
    results = await store.retrieve(emb, domain="test")
    for r in results:
        assert isinstance(r, Chunk)
