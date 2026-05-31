"""Tests for vector migration cursor/list-all scan.

Verifies that:
1. Migration scans all records across more than one page/batch.
2. Migration does not miss records beyond the first batch/probe set.
3. Migration handles missing embeddings according to existing policy.
4. Migration is idempotent.
"""

from __future__ import annotations

from aip.adapter.vector.migrate import _source_supports_list_all_ids, migrate_vectors
from aip.foundation.schemas import Chunk


class InMemoryVectorStoreWithCursor:
    """In-memory VectorStore that supports list_all_ids for cursor scanning."""

    def __init__(self):
        self._vectors: dict[str, dict] = {}

    async def upsert(self, id, embedding, content, metadata, domain=None):
        self._vectors[id] = {
            "id": id,
            "embedding": embedding,
            "content": content,
            "metadata": metadata,
            "domain": domain,
        }

    async def retrieve(self, query_vector, domain=None, top_k=10):
        results = []
        for v in self._vectors.values():
            if domain and v["domain"] != domain:
                continue
            results.append(
                Chunk(
                    id=v["id"],
                    content=v["content"],
                    metadata=v["metadata"],
                    domain=v["domain"] or "",
                    score=1.0,
                )
            )
            if len(results) >= top_k:
                break
        return results

    async def delete(self, id):
        self._vectors.pop(id, None)

    async def count(self, domain=None):
        if domain:
            return sum(1 for v in self._vectors.values() if v["domain"] == domain)
        return len(self._vectors)

    async def store(self, chunk):
        self._vectors[chunk.id] = {
            "id": chunk.id,
            "embedding": [],
            "content": chunk.content,
            "metadata": chunk.metadata,
            "domain": chunk.domain,
        }
        return chunk.id

    async def health_check(self):
        return {"connected": True, "pool_size": 1, "latency_ms": 0, "backend_name": "test"}

    async def list_stale_vectors(self, threshold_days=30, domain=None, limit=100):
        return []

    async def list_all_ids(self, offset=0, limit=500, domain=None):
        """Cursor-based ID listing for migration."""
        ids = list(self._vectors.keys())
        if domain:
            ids = [id for id in ids if self._vectors[id].get("domain") == domain]
        return ids[offset : offset + limit]

    async def get_by_id(self, chunk_id):
        v = self._vectors.get(chunk_id)
        if v is None:
            return None
        return Chunk(
            id=v["id"],
            content=v["content"],
            metadata=v["metadata"],
            domain=v["domain"] or "",
            score=1.0,
        )

    async def close(self):
        pass


class InMemoryVectorStoreWithoutCursor:
    """In-memory VectorStore that does NOT support list_all_ids (old-style)."""

    def __init__(self):
        self._vectors: dict[str, dict] = {}

    async def upsert(self, id, embedding, content, metadata, domain=None):
        self._vectors[id] = {
            "id": id,
            "embedding": embedding,
            "content": content,
            "metadata": metadata,
            "domain": domain,
        }

    async def retrieve(self, query_vector, domain=None, top_k=10):
        results = []
        for v in self._vectors.values():
            if domain and v["domain"] != domain:
                continue
            results.append(
                Chunk(
                    id=v["id"],
                    content=v["content"],
                    metadata=v["metadata"],
                    domain=v["domain"] or "",
                    score=1.0,
                )
            )
            if len(results) >= top_k:
                break
        return results

    async def delete(self, id):
        self._vectors.pop(id, None)

    async def count(self, domain=None):
        return len(self._vectors)

    async def store(self, chunk):
        return chunk.id

    async def health_check(self):
        return {"connected": True}

    async def list_stale_vectors(self, **kwargs):
        return []


# --- Test: cursor detection ---


def test_source_supports_list_all_ids_detects_capability():
    """_source_supports_list_all_ids correctly detects the method."""
    store_with = InMemoryVectorStoreWithCursor()
    store_without = InMemoryVectorStoreWithoutCursor()
    assert _source_supports_list_all_ids(store_with) is True
    assert _source_supports_list_all_ids(store_without) is False


# --- Test: cursor migration scans all records across batches ---


async def test_cursor_migration_scans_all_records():
    """Migration with cursor scans all records even when they span multiple batches."""
    source = InMemoryVectorStoreWithCursor()
    target = InMemoryVectorStoreWithCursor()

    # Insert more records than a single batch
    num_records = 25
    batch_size = 10
    for i in range(num_records):
        # Use non-zero embeddings (vec_0 with [0.0] would be rejected as zero vector)
        emb_val = float(i + 1) / 100.0
        await source.upsert(
            id=f"vec_{i}",
            embedding=[emb_val] * 10,
            content=f"Content {i}",
            metadata={"embedding": [emb_val] * 10},
            domain="test",
        )

    status = await migrate_vectors(source, target, batch_size=batch_size)
    assert status.migrated_vectors == num_records
    assert status.failed_vectors == 0
    assert await target.count() == num_records


# --- Test: migration does not miss records beyond first batch ---


async def test_migration_does_not_miss_records_beyond_first_batch():
    """Records beyond the first batch are not missed during cursor migration."""
    source = InMemoryVectorStoreWithCursor()
    target = InMemoryVectorStoreWithCursor()

    for i in range(15):
        emb_val = 0.1 * (i + 1)  # non-zero
        await source.upsert(
            id=f"vec_{i}",
            embedding=[emb_val] * 10,
            content=f"Content {i}",
            metadata={"embedding": [emb_val] * 10},
            domain="test",
        )

    # Use small batch size to force multiple batches
    status = await migrate_vectors(source, target, batch_size=5)
    assert status.migrated_vectors == 15

    # Verify all records made it to target
    for i in range(15):
        chunk = await target.get_by_id(f"vec_{i}")
        assert chunk is not None, f"vec_{i} was not migrated"


# --- Test: migration handles missing embeddings ---


async def test_cursor_migration_skips_missing_embeddings():
    """Chunks without embeddings are skipped (not migrated with zero vectors)."""
    source = InMemoryVectorStoreWithCursor()
    target = InMemoryVectorStoreWithCursor()

    # Insert records with embeddings
    await source.upsert(
        id="vec_with_emb",
        embedding=[0.1] * 10,
        content="Has embedding",
        metadata={"embedding": [0.1] * 10},
        domain="test",
    )
    # Insert records without embeddings and no provider
    await source.upsert(
        id="vec_no_emb",
        embedding=[0.0] * 10,
        content="No embedding",
        metadata={},  # no embedding key
        domain="test",
    )

    status = await migrate_vectors(source, target, embedding_provider=None)
    assert status.migrated_vectors == 1  # Only the one with embedding
    assert await target.get_by_id("vec_with_emb") is not None
    assert await target.get_by_id("vec_no_emb") is None  # Skipped


# --- Test: migration is idempotent ---


async def test_cursor_migration_is_idempotent():
    """Running migration twice produces the same result."""
    source = InMemoryVectorStoreWithCursor()
    target = InMemoryVectorStoreWithCursor()

    for i in range(5):
        emb_val = 0.1 * (i + 1)  # non-zero
        await source.upsert(
            id=f"vec_{i}",
            embedding=[emb_val] * 10,
            content=f"Content {i}",
            metadata={"embedding": [emb_val] * 10},
            domain="test",
        )

    # First migration
    status1 = await migrate_vectors(source, target)
    assert status1.migrated_vectors == 5

    # Second migration — should not duplicate
    await migrate_vectors(source, target)
    assert await target.count() == 5  # Still 5, not 10


# --- Test: fallback to probes when no cursor support ---


async def test_fallback_to_probes_without_cursor():
    """Without list_all_ids, migration falls back to probe-based scanning."""
    source = InMemoryVectorStoreWithoutCursor()
    target = InMemoryVectorStoreWithCursor()

    for i in range(3):
        await source.upsert(
            id=f"vec_{i}",
            embedding=[0.5] * 10,
            content=f"Content {i}",
            metadata={"embedding": [0.5] * 10},
            domain="test",
        )

    # This should still work via probe-based fallback
    status = await migrate_vectors(source, target)
    # May not get all due to probe limitations, but should not error
    assert status.failed_vectors == 0
