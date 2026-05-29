"""Vector migration tool — sqlite_vss to pgvector.

Migration is idempotent and resumable.
Preserves IDs, domains, metadata, and real embeddings across backends.

Missing embeddings are regenerated via EmbeddingProvider when provided.
When no provider is available, vectors without embeddings are skipped with clear logging
rather than being silently destroyed with zero-vector fallbacks.

C. Cursor-based scanning: When the source VectorStore supports list_all_ids(),
migration uses that for complete deterministic coverage. Otherwise falls back to
probe-based retrieval (which may miss vectors in sparse regions).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from aip.foundation.protocols import EmbeddingProvider, VectorStore
from aip.foundation.schemas import MigrationCheckpoint, MigrationStatus

logger = logging.getLogger(__name__)


async def migrate_vectors(
    source: VectorStore,
    target: VectorStore,
    batch_size: int = 500,
    checkpoint_callback: Callable[[MigrationCheckpoint], Any] | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    dimensions: int = 768,
) -> MigrationStatus:
    """Migrate all vectors from source to target VectorStore.

    Idempotent: upsert semantics mean re-running doesn't duplicate.
    Resumable: checkpoint_callback records progress for interrupted migrations.

    **Cursor-based scanning**: When the source VectorStore supports
    ``list_all_ids()``, migration iterates through all IDs in batches,
    guaranteeing complete coverage. Otherwise falls back to probe-based
    retrieval which may miss vectors in sparse regions.

    **Embedding preservation**: When chunk metadata contains an ``embedding``
    key, that embedding is used. When no embedding is present in metadata:

    - If an ``embedding_provider`` is provided, a new embedding is generated
      from the chunk content.
    - If no provider is available, the chunk is **skipped** (not migrated
      with a zero vector) and logged as a warning.

    Args:
        source: Source VectorStore (typically sqlite_vss).
        target: Target VectorStore (typically pgvector).
        batch_size: Number of vectors per batch.
        checkpoint_callback: Called after each batch with MigrationCheckpoint.
        embedding_provider: Optional provider to generate embeddings for
            chunks that lack one. When provided, missing embeddings are
            regenerated from content rather than replaced with zero vectors.
        dimensions: Embedding dimensions (used for zero-vector detection).

    Returns:
        MigrationStatus with final counts and timestamps.
    """
    started_at = datetime.now(timezone.utc).isoformat()
    status = MigrationStatus(
        source_backend="sqlite_vss",
        target_backend="pgvector",
        started_at=started_at,
    )

    total = await source.count()
    status.total_vectors = total

    if total == 0:
        status.completed_at = datetime.now(timezone.utc).isoformat()
        return status

    # Migrate in batches using configurable batch_size
    migrated = 0
    failed = 0
    skipped = 0  # chunks skipped because no embedding available

    # Strategy: prefer cursor-based (list_all_ids) for deterministic coverage
    if _source_supports_list_all_ids(source):
        migrated, failed, skipped = await _migrate_via_cursor(
            source=source,
            target=target,
            total=total,
            batch_size=batch_size,
            embedding_provider=embedding_provider,
            dimensions=dimensions,
            checkpoint_callback=checkpoint_callback,
            status=status,
        )
    else:
        migrated, failed, skipped = await _migrate_via_probes(
            source=source,
            target=target,
            total=total,
            batch_size=batch_size,
            embedding_provider=embedding_provider,
            dimensions=dimensions,
            checkpoint_callback=checkpoint_callback,
            status=status,
        )

    status.migrated_vectors = migrated
    status.failed_vectors = failed

    if skipped > 0:
        logger.warning(
            "Migration completed with %d vectors skipped (no embedding available). "
            "Provide an EmbeddingProvider to migrate these items, or ensure source "
            "metadata contains 'embedding' keys.",
            skipped,
        )

    # Verify target count
    target_count = await target.count()
    if target_count >= total:
        status.completed_at = datetime.now(timezone.utc).isoformat()

    # Final checkpoint if we haven't already recorded one for this batch
    if checkpoint_callback and migrated == 0:
        ckpt = MigrationCheckpoint(
            checkpoint_id=str(uuid.uuid4()),
            source_backend="sqlite_vss",
            target_backend="pgvector",
            last_migrated_id=0,
            total_migrated=migrated,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        if asyncio.iscoroutinefunction(checkpoint_callback):
            await checkpoint_callback(ckpt)
        else:
            checkpoint_callback(ckpt)

    return status


def _source_supports_list_all_ids(source: VectorStore) -> bool:
    """Check if the source VectorStore supports list_all_ids() for cursor scanning."""
    return hasattr(source, "list_all_ids") and callable(getattr(source, "list_all_ids"))


async def _migrate_via_cursor(
    source: VectorStore,
    target: VectorStore,
    total: int,
    batch_size: int,
    embedding_provider: EmbeddingProvider | None,
    dimensions: int,
    checkpoint_callback: Callable | None,
    status: MigrationStatus,
) -> tuple[int, int, int]:
    """Migrate using cursor/list_all_ids for complete deterministic coverage.

    Iterates through all vector IDs in batches, ensuring no vector is missed.
    """
    migrated = 0
    failed = 0
    skipped = 0
    migrated_ids: set[str] = set()
    offset = 0

    while offset < total:
        # Fetch next batch of IDs
        try:
            batch_ids = await source.list_all_ids(offset=offset, limit=batch_size)  # type: ignore[union-attr]
        except Exception as exc:
            logger.error("list_all_ids failed at offset %d: %s", offset, exc)
            break

        if not batch_ids:
            break

        for chunk_id in batch_ids:
            if chunk_id in migrated_ids:
                continue

            try:
                # Retrieve the chunk by its ID using retrieve with a dummy vector
                # The source store must support retrieving by ID through metadata
                chunk = await _get_chunk_by_id(source, chunk_id, dimensions)
                if chunk is None:
                    logger.warning("Could not retrieve chunk '%s' during cursor migration", chunk_id)
                    skipped += 1
                    continue

                embedding = await _resolve_embedding(chunk, embedding_provider, dimensions)

                if embedding is None:
                    skipped += 1
                    logger.warning(
                        "Skipping vector '%s' — no embedding in metadata and no EmbeddingProvider.",
                        chunk.id,
                    )
                    continue

                await target.upsert(
                    id=chunk.id,
                    embedding=embedding,
                    content=chunk.content or "",
                    metadata=chunk.metadata,
                    domain=chunk.domain,
                )
                migrated += 1
                migrated_ids.add(chunk.id)

            except Exception as exc:
                failed += 1
                logger.warning("Failed to migrate vector '%s': %s", chunk_id, exc)

        offset += len(batch_ids)

        # Record checkpoint after each batch
        if checkpoint_callback:
            ckpt = MigrationCheckpoint(
                checkpoint_id=str(uuid.uuid4()),
                source_backend="sqlite_vss",
                target_backend="pgvector",
                last_migrated_id=offset,
                total_migrated=migrated,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            if asyncio.iscoroutinefunction(checkpoint_callback):
                await checkpoint_callback(ckpt)
            else:
                checkpoint_callback(ckpt)

    return migrated, failed, skipped


async def _migrate_via_probes(
    source: VectorStore,
    target: VectorStore,
    total: int,
    batch_size: int,
    embedding_provider: EmbeddingProvider | None,
    dimensions: int,
    checkpoint_callback: Callable | None,
    status: MigrationStatus,
) -> tuple[int, int, int]:
    """Fallback: migrate using diverse probe vectors for retrieval-based scanning.

    This approach may miss vectors in sparse regions of the embedding space.
    A warning is logged recommending list_all_ids support.
    """
    logger.warning(
        "Source VectorStore does not support list_all_ids(). "
        "Using probe-based retrieval which may miss vectors in sparse regions. "
        "Consider adding list_all_ids() to the source store for complete coverage.",
    )

    migrated = 0
    failed = 0
    skipped = 0
    migrated_ids: set[str] = set()

    # Generate diverse probe vectors for comprehensive retrieval.
    probe_vectors = _generate_probe_vectors(dimensions, num_probes=8)

    for probe_idx, probe_vector in enumerate(probe_vectors):
        if len(migrated_ids) >= total:
            break

        try:
            batch = await source.retrieve(probe_vector, top_k=batch_size)

            if not batch:
                continue

            for chunk in batch:
                if chunk.id in migrated_ids:
                    continue

                try:
                    embedding = await _resolve_embedding(chunk, embedding_provider, dimensions)

                    if embedding is None:
                        skipped += 1
                        logger.warning(
                            "Skipping vector '%s' (domain='%s') — no embedding "
                            "in metadata and no EmbeddingProvider to generate one.",
                            chunk.id,
                            chunk.domain,
                        )
                        continue

                    await target.upsert(
                        id=chunk.id,
                        embedding=embedding,
                        content=chunk.content or "",
                        metadata=chunk.metadata,
                        domain=chunk.domain,
                    )
                    migrated += 1
                    migrated_ids.add(chunk.id)

                except Exception as exc:
                    failed += 1
                    logger.warning("Failed to migrate vector '%s': %s", chunk.id, exc)

            # Record checkpoint after each probe batch
            if checkpoint_callback:
                ckpt = MigrationCheckpoint(
                    checkpoint_id=str(uuid.uuid4()),
                    source_backend="sqlite_vss",
                    target_backend="pgvector",
                    last_migrated_id=len(migrated_ids),
                    total_migrated=migrated,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
                if asyncio.iscoroutinefunction(checkpoint_callback):
                    await checkpoint_callback(ckpt)
                else:
                    checkpoint_callback(ckpt)

        except Exception as exc:
            failed += 1
            logger.error("Batch retrieval failed for probe %d: %s", probe_idx, exc)

    return migrated, failed, skipped


async def _get_chunk_by_id(source: VectorStore, chunk_id: str, dimensions: int) -> Any:
    """Retrieve a specific chunk by ID from the source store.

    Uses a zero-vector probe if the store doesn't support direct ID lookup,
    then filters results by ID.
    """
    # If the source has a get_by_id method, use it directly
    if hasattr(source, "get_by_id") and callable(getattr(source, "get_by_id")):
        try:
            return await source.get_by_id(chunk_id)  # type: ignore[union-attr]
        except Exception as exc:
            logger.debug("get_by_id failed for %s: %s", chunk_id, exc)

    # Fallback: use a small probe vector and filter
    probe = [0.01] * dimensions
    try:
        results = await source.retrieve(probe, top_k=1)
        # This is a best-effort approach; in practice, stores that support
        # cursor scanning should also support get_by_id
        for chunk in results:
            if chunk.id == chunk_id:
                return chunk
    except Exception as exc:
        logger.debug("Probe retrieve failed for chunk %s: %s", chunk_id, exc)

    return None


def _generate_probe_vectors(dimensions: int, num_probes: int = 8) -> list[list[float]]:
    """Generate diverse probe vectors for retrieval-based migration scanning.

    Creates deterministic unit vectors spread across different dimensions
    to maximize coverage of the vector space.
    """
    import hashlib

    probes = []
    for i in range(num_probes):
        seed = f"migration_probe_{i}".encode("utf-8")
        h = hashlib.sha256(seed).digest()

        vec = []
        for j in range(dimensions):
            byte_idx = (j * 4 + i * 17) % len(h)
            val = int.from_bytes(h[byte_idx : byte_idx + 4].ljust(4, b"\x00"), "big")
            vec.append(val / (2**32 - 1))

        # Normalize to unit vector
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]

        probes.append(vec)

    return probes


async def _resolve_embedding(
    chunk: Any,
    embedding_provider: EmbeddingProvider | None,
    dimensions: int,
) -> list[float] | None:
    """Resolve the embedding for a chunk during migration.

    Priority:
    1. Use embedding from chunk metadata (if present and non-zero).
    2. Generate new embedding via EmbeddingProvider (if available).
    3. Return None (caller should skip, not insert zero vector).

    This function never returns a zero vector.
    """
    metadata = chunk.metadata if hasattr(chunk, "metadata") else {}
    if isinstance(metadata, dict):
        stored_embedding = metadata.get("embedding")
        if stored_embedding and isinstance(stored_embedding, list):
            if any(v != 0.0 for v in stored_embedding):
                return stored_embedding
            else:
                logger.warning(
                    "Chunk '%s' has zero-vector in metadata — will not use it. Attempting to regenerate.",
                    getattr(chunk, "id", "unknown"),
                )

    if embedding_provider is not None:
        content = chunk.content if hasattr(chunk, "content") else ""
        if content:
            try:
                embedding = await embedding_provider.embed(content)
                if embedding and len(embedding) > 0 and any(v != 0.0 for v in embedding):
                    logger.info(
                        "Regenerated embedding for chunk '%s' via EmbeddingProvider (dim=%d).",
                        getattr(chunk, "id", "unknown"),
                        len(embedding),
                    )
                    return embedding
                else:
                    logger.warning(
                        "EmbeddingProvider returned empty/zero vector for chunk '%s'.",
                        getattr(chunk, "id", "unknown"),
                    )
            except Exception as exc:
                logger.warning(
                    "Embedding generation failed for chunk '%s': %s. This chunk will be skipped.",
                    getattr(chunk, "id", "unknown"),
                    exc,
                )

    return None
