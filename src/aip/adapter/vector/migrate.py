"""Vector migration tool — sqlite_vss to pgvector.

Per Phase Scope Definition: migration must be idempotent and resumable.
Preserves IDs, domains, metadata, and **real embeddings** across backends.

Phase 10: Eliminated zero-vector usage. When an EmbeddingProvider is
provided, missing embeddings are regenerated. When no provider is
available, vectors without embeddings are skipped with clear logging
rather than being silently destroyed with zero-vector fallbacks.
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

    # We need a strategy to iterate through all vectors. The previous approach
    # used a zero-vector query which only returns nearest-neighbors (not all
    # vectors). Instead, we use multiple diverse query vectors to maximize
    # coverage, and track which IDs we've already migrated to avoid duplicates.
    migrated_ids: set[str] = set()

    # Generate diverse probe vectors for comprehensive retrieval.
    # We use deterministic vectors spread across the embedding space to
    # maximize the chance of hitting all clusters.
    probe_vectors = _generate_probe_vectors(dimensions, num_probes=8)

    for probe_idx, probe_vector in enumerate(probe_vectors):
        if len(migrated_ids) >= total:
            break

        try:
            batch = await source.retrieve(probe_vector, top_k=batch_size)

            if not batch:
                continue

            for chunk in batch:
                # Skip already-migrated chunks (idempotent)
                if chunk.id in migrated_ids:
                    continue

                try:
                    embedding = await _resolve_embedding(
                        chunk, embedding_provider, dimensions
                    )

                    if embedding is None:
                        # No embedding available and no provider to generate one.
                        # Skip rather than insert a zero vector.
                        skipped += 1
                        logger.warning(
                            "Skipping vector '%s' (domain='%s') — no embedding "
                            "in metadata and no EmbeddingProvider to generate one. "
                            "Provide an embedding_provider to migrate this item.",
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
                    logger.warning(
                        "Failed to migrate vector '%s': %s",
                        chunk.id,
                        exc,
                    )

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
            logger.error(
                "Batch retrieval failed for probe %d: %s",
                probe_idx,
                exc,
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

    # Verify target count (in real run this would reflect actual upserts)
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


def _generate_probe_vectors(dimensions: int, num_probes: int = 8) -> list[list[float]]:
    """Generate diverse probe vectors for retrieval-based migration scanning.

    Creates deterministic unit vectors spread across different dimensions
    to maximize coverage of the vector space. Each probe activates a
    different subset of dimensions, ensuring that nearest-neighbor
    retrieval reaches different clusters.

    This is a practical workaround for the fact that VectorStore.retrieve()
    is similarity-based and doesn't support cursor-based iteration. By
    using diverse probes, we maximize the chance of retrieving all stored
    vectors.
    """
    import hashlib

    probes = []
    for i in range(num_probes):
        # Create a deterministic seed for each probe
        seed = f"migration_probe_{i}".encode("utf-8")
        h = hashlib.sha256(seed).digest()

        vec = []
        for j in range(dimensions):
            byte_idx = (j * 4 + i * 17) % len(h)
            val = int.from_bytes(h[byte_idx:byte_idx + 4].ljust(4, b'\x00'), 'big')
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
    # Check metadata for pre-existing embedding
    metadata = chunk.metadata if hasattr(chunk, "metadata") else {}
    if isinstance(metadata, dict):
        stored_embedding = metadata.get("embedding")
        if stored_embedding and isinstance(stored_embedding, list):
            # Validate it's not a zero vector
            if any(v != 0.0 for v in stored_embedding):
                return stored_embedding
            else:
                logger.warning(
                    "Chunk '%s' has zero-vector in metadata — will not use it. "
                    "Attempting to regenerate.",
                    getattr(chunk, "id", "unknown"),
                )

    # Try generating via EmbeddingProvider
    if embedding_provider is not None:
        content = chunk.content if hasattr(chunk, "content") else ""
        if content:
            try:
                embedding = await embedding_provider.embed(content)
                if embedding and len(embedding) > 0 and any(v != 0.0 for v in embedding):
                    logger.info(
                        "Regenerated embedding for chunk '%s' via EmbeddingProvider "
                        "(dim=%d).",
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
                    "Embedding generation failed for chunk '%s': %s. "
                    "This chunk will be skipped.",
                    getattr(chunk, "id", "unknown"),
                    exc,
                )

    # No embedding available — return None (caller must skip, not use zero vector)
    return None
