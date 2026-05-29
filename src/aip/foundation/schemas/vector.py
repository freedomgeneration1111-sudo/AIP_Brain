"""Vector store and migration types.

Vector backend selection, pgvector configuration, and migration
status/checkpoint tracking for the sqlite_vss → pgvector migration path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# Type alias for vector backend selection
VectorBackendType = Literal["pgvector", "sqlite_vss"]


@dataclass
class PgvectorConfig:
    """Configuration for the pgvector VectorStore adapter.

    PostgreSQL 16 + pgvector is the required production path.
    All parameters toggleable via config, not hardcoded.
    HNSW parameters tune index quality vs. build time.
    """
    connection_string: str
    pool_min_size: int = 2
    pool_max_size: int = 10
    pool_timeout_seconds: float = 30.0
    statement_timeout_ms: int = 5000
    hnsw_m: int = 16
    hnsw_ef_construction: int = 64
    hnsw_ef_search: int = 40


@dataclass
class MigrationStatus:
    """Tracks the state of a sqlite_vss → pgvector migration.

    Per Phase Scope Definition: migration must be idempotent and resumable.
    checkpoint_id enables resuming from last successful vector.
    """
    source_backend: str
    target_backend: str
    total_vectors: int = 0
    migrated_vectors: int = 0
    failed_vectors: int = 0
    started_at: str = ""
    completed_at: str | None = None
    checkpoint_id: str | None = None


@dataclass
class MigrationCheckpoint:
    """A resumable migration point.

    If migration is interrupted, resume from last_migrated_id + 1.
    """
    checkpoint_id: str
    source_backend: str
    target_backend: str
    last_migrated_id: int = 0
    total_migrated: int = 0
    created_at: str = ""


__all__ = [
    "VectorBackendType",
    "PgvectorConfig",
    "MigrationStatus",
    "MigrationCheckpoint",
]
