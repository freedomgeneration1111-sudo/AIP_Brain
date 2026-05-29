"""Tests for SqliteVssVectorStore.
Requires sqlite_vss extension to be available.
All tests run locally with no network calls."""

import hashlib
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


class FakeEmbeddingProvider:
    """Deterministic embedding provider for testing."""

    def __init__(self, dimensions: int = 4):
        self.dimensions = dimensions
        self.embed_calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.embed_calls.append(text)
        h = hashlib.sha256(text.encode("utf-8")).digest()
        vec = []
        for i in range(self.dimensions):
            val = (h[i % len(h)] / 255.0) - 0.5
            vec.append(val)
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test_vectors.db")
    s = SqliteVssVectorStore(db_path=db_path, dimensions=4)
    yield s
    s.close()


@pytest.fixture
def store_with_embeddings(tmp_path):
    db_path = str(tmp_path / "test_vectors_embed.db")
    embed_provider = FakeEmbeddingProvider(dimensions=4)
    s = SqliteVssVectorStore(
        db_path=db_path, dimensions=4, embedding_provider=embed_provider
    )
    yield s, embed_provider
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


# --- NEW: store() compat method tests ---


@pytest.mark.asyncio
async def test_store_with_embedding_provider_generates_real_embedding(store_with_embeddings):
    """store() must generate a real (non-zero) embedding when EmbeddingProvider is available."""
    store, embed_provider = store_with_embeddings

    chunk = Chunk(id="compat-1", content="Machine learning document", metadata={"source": "test"}, domain="test")
    result_id = await store.store(chunk)

    assert result_id == "compat-1"
    assert len(embed_provider.embed_calls) > 0, "EmbeddingProvider.embed() should have been called"

    # Verify the item is stored and retrievable
    assert await store.count() == 1

    # Verify the stored vector is NOT a zero vector by doing a retrieval
    # The real embedding should make the document findable
    results = await store.retrieve(
        await embed_provider.embed("Machine learning document"),
        domain="test",
        top_k=1,
    )
    assert len(results) >= 1
    assert results[0].id == "compat-1"


@pytest.mark.asyncio
async def test_store_without_embedding_provider_metadata_only(store):
    """store() without EmbeddingProvider stores metadata-only (no zero vector)."""
    chunk = Chunk(id="no-embed-1", content="Document without embedding", metadata={"k": "v"}, domain="test")
    result_id = await store.store(chunk)

    assert result_id == "no-embed-1"
    # Metadata is stored (count reflects it)
    assert await store.count() == 1


@pytest.mark.asyncio
async def test_store_with_failing_embedding_provider_metadata_only(tmp_path):
    """store() with a failing EmbeddingProvider falls back to metadata-only."""
    db_path = str(tmp_path / "test_fail_embed.db")

    class FailingEmbeddingProvider:
        async def embed(self, text: str) -> list[float]:
            raise ConnectionError("Ollama not running")

    s = SqliteVssVectorStore(
        db_path=db_path, dimensions=4,
        embedding_provider=FailingEmbeddingProvider(),
    )

    chunk = Chunk(id="fail-embed-1", content="Document with failing embed", metadata={}, domain="test")
    # Should NOT raise — degrades gracefully to metadata-only
    result_id = await s.store(chunk)

    assert result_id == "fail-embed-1"
    assert await s.count() == 1
    s.close()


@pytest.mark.asyncio
async def test_store_no_zero_vectors_inserted(store_with_embeddings):
    """Verify that store() never inserts zero vectors into the vss index."""
    store, embed_provider = store_with_embeddings

    chunk = Chunk(id="nonzero-1", content="Content for non-zero test", metadata={}, domain="test")
    await store.store(chunk)

    # The embedding provider generated a non-zero vector
    assert len(embed_provider.embed_calls) == 1
    generated = await embed_provider.embed(embed_provider.embed_calls[0])
    assert any(v != 0.0 for v in generated), "Generated embedding should not be zero vector"


@pytest.mark.asyncio
async def test_store_existing_entry_preserves_vector(store_with_embeddings):
    """store() on an existing entry updates metadata without destroying the vector."""
    store, embed_provider = store_with_embeddings

    # First, store with a real embedding via upsert
    emb = _unit([1.0, 0.0, 0.0, 0.0])
    await store.upsert("existing-1", emb, content="Original content", domain="test")

    # Then call store() on the same ID with updated metadata
    chunk = Chunk(id="existing-1", content="Updated content", metadata={"updated": True}, domain="test")
    await store.store(chunk)

    # Count should still be 1 (upsert semantics)
    assert await store.count() == 1
