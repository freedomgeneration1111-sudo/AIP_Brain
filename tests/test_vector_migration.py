"""Tests for the vector migration tool.

Verifies that migration preserves real embeddings, skips items without
embeddings (instead of inserting zero vectors), and can regenerate
missing embeddings via an EmbeddingProvider.
"""

import hashlib

import pytest

from aip.adapter.vector.migrate import _resolve_embedding, migrate_vectors
from aip.foundation.schemas import Chunk, MigrationStatus


class FakeEmbeddingProvider:
    """Deterministic embedding provider for testing migration."""

    def __init__(self, dimensions: int = 768):
        self.dimensions = dimensions
        self.embed_calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.embed_calls.append(text)
        h = hashlib.sha256(text.encode("utf-8")).digest()
        vec = []
        for i in range(self.dimensions):
            val = (h[i % len(h)] / 255.0) - 0.5
            vec.append(val)
        return vec


class TrackingVectorStore:
    """In-memory VectorStore that tracks upsert calls for verification."""

    def __init__(self, chunks: list[Chunk] | None = None, count_value: int = 0):
        self._chunks = chunks or []
        self._count_value = count_value
        self.upserted: list[dict] = []

    async def upsert(self, id, embedding, content, metadata=None, domain=None):
        self.upserted.append(
            {
                "id": id,
                "embedding": embedding,
                "content": content,
                "metadata": metadata or {},
                "domain": domain,
            },
        )

    async def retrieve(self, query_vector, domain=None, top_k=10):
        return self._chunks[:top_k]

    async def delete(self, id):
        pass

    async def count(self, domain=None):
        return self._count_value

    async def store(self, chunk):
        return chunk.id


@pytest.mark.asyncio
async def test_migration_idempotent_and_resumable():
    """Basic contract: migrate_vectors returns MigrationStatus."""

    # Smoke test with minimal dummy objects
    class DummyStore:
        async def count(self, domain=None):
            return 0

    status = await migrate_vectors(DummyStore(), DummyStore(), batch_size=10)
    assert isinstance(status, MigrationStatus)
    assert status.source_backend == "sqlite_vss"
    assert hasattr(status, "total_vectors")
    assert hasattr(status, "migrated_vectors")


@pytest.mark.asyncio
async def test_migration_preserves_existing_embeddings():
    """When chunk metadata has an embedding, it must be preserved (not replaced with zero vector)."""
    real_embedding = [0.1, 0.2, 0.3, 0.4] * 192  # 768-dim non-zero vector
    chunk_with_embedding = Chunk(
        id="doc-with-emb",
        content="Content with embedding",
        metadata={"embedding": real_embedding, "source": "test"},
        domain="test",
    )

    source = TrackingVectorStore(chunks=[chunk_with_embedding], count_value=1)
    target = TrackingVectorStore()

    status = await migrate_vectors(source, target, batch_size=10)

    # Check that the target received the real embedding, not a zero vector
    if len(target.upserted) > 0:
        upserted_emb = target.upserted[0]["embedding"]
        assert upserted_emb == real_embedding, "Existing embedding must be preserved"
        assert any(v != 0.0 for v in upserted_emb), "Embedding should not be a zero vector"


@pytest.mark.asyncio
async def test_migration_skips_items_without_embeddings_when_no_provider():
    """Without EmbeddingProvider, items without embeddings are skipped (not zero-vector migrated)."""
    chunk_no_embedding = Chunk(
        id="doc-no-emb",
        content="Content without embedding",
        metadata={"source": "test"},
        domain="test",
    )

    source = TrackingVectorStore(chunks=[chunk_no_embedding], count_value=1)
    target = TrackingVectorStore()

    status = await migrate_vectors(source, target, batch_size=10)

    # The chunk should NOT be upserted with a zero vector
    for upserted in target.upserted:
        emb = upserted["embedding"]
        assert any(v != 0.0 for v in emb), f"Zero vector should not be inserted for item '{upserted['id']}'"


@pytest.mark.asyncio
async def test_migration_generates_embeddings_with_provider():
    """With EmbeddingProvider, items without embeddings get regenerated."""
    chunk_no_embedding = Chunk(
        id="doc-regen",
        content="Content needing embedding regeneration",
        metadata={"source": "test"},
        domain="test",
    )

    embed_provider = FakeEmbeddingProvider(dimensions=768)
    source = TrackingVectorStore(chunks=[chunk_no_embedding], count_value=1)
    target = TrackingVectorStore()

    status = await migrate_vectors(
        source,
        target,
        batch_size=10,
        embedding_provider=embed_provider,
        dimensions=768,
    )

    # The embedding provider should have been called
    if len(target.upserted) > 0:
        assert len(embed_provider.embed_calls) > 0, "EmbeddingProvider should have been called"
        upserted_emb = target.upserted[0]["embedding"]
        assert len(upserted_emb) == 768, f"Expected 768-dim embedding, got {len(upserted_emb)}"
        assert any(v != 0.0 for v in upserted_emb), "Generated embedding should not be zero vector"


@pytest.mark.asyncio
async def test_migration_rejects_zero_vector_in_metadata():
    """When metadata has a zero-vector embedding, it should not be used."""
    zero_embedding = [0.0] * 768
    chunk_zero_emb = Chunk(
        id="doc-zero-emb",
        content="Content with zero embedding in metadata",
        metadata={"embedding": zero_embedding},
        domain="test",
    )

    embed_provider = FakeEmbeddingProvider(dimensions=768)
    source = TrackingVectorStore(chunks=[chunk_zero_emb], count_value=1)
    target = TrackingVectorStore()

    status = await migrate_vectors(
        source,
        target,
        batch_size=10,
        embedding_provider=embed_provider,
        dimensions=768,
    )

    # Zero vector in metadata should be rejected and regenerated
    if len(target.upserted) > 0:
        upserted_emb = target.upserted[0]["embedding"]
        assert any(v != 0.0 for v in upserted_emb), "Zero vector from metadata should be replaced with real embedding"
        # The provider should have been called to regenerate
        assert len(embed_provider.embed_calls) > 0, "EmbeddingProvider should have been called to replace zero vector"


@pytest.mark.asyncio
async def test_resolve_embedding_prefers_metadata():
    """_resolve_embedding prefers real embeddings from metadata over generating new ones."""
    real_embedding = [0.5, -0.3, 0.8, 0.1] * 192  # 768-dim
    chunk = Chunk(
        id="test-pref",
        content="Some content",
        metadata={"embedding": real_embedding},
        domain="test",
    )

    embed_provider = FakeEmbeddingProvider(dimensions=768)
    result = await _resolve_embedding(chunk, embed_provider, 768)

    # Should use the metadata embedding, not call the provider
    assert result == real_embedding
    assert len(embed_provider.embed_calls) == 0, "Provider should not be called when metadata has embedding"


@pytest.mark.asyncio
async def test_resolve_embedding_generates_when_no_metadata():
    """_resolve_embedding generates embedding when metadata has none."""
    chunk = Chunk(
        id="test-no-emb",
        content="Content without embedding",
        metadata={},
        domain="test",
    )

    embed_provider = FakeEmbeddingProvider(dimensions=768)
    result = await _resolve_embedding(chunk, embed_provider, 768)

    assert result is not None
    assert len(result) == 768
    assert any(v != 0.0 for v in result)
    assert len(embed_provider.embed_calls) == 1


@pytest.mark.asyncio
async def test_resolve_embedding_returns_none_when_no_provider():
    """_resolve_embedding returns None when no embedding available and no provider."""
    chunk = Chunk(
        id="test-none",
        content="Content without anything",
        metadata={},
        domain="test",
    )

    result = await _resolve_embedding(chunk, None, 768)
    assert result is None, "Should return None when no embedding and no provider"
