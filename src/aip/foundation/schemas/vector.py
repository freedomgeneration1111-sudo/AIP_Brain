"""Vector store and migration types.

Vector backend selection, pgvector configuration, migration
status/checkpoint tracking for the sqlite_vss → pgvector migration path,
and the VectorBackendStatus enum for retrieval honesty.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Literal

# Type alias for vector backend selection
VectorBackendType = Literal["pgvector", "sqlite_vss"]


class VectorBackendStatus(enum.Enum):
    """Explicit health status of the vector retrieval backend.

    Retrieval must never silently pretend it is healthier than it is.
    This enum is the single source of truth for vector backend status
    across the entire retrieval pipeline.

    Values:
        available: sqlite-vss extension loaded (or pgvector connected).
            Full vector similarity search is operational.
        degraded_bruteforce: sqlite-vss extension is absent; the store
            falls back to brute-force cosine similarity scans over
            ``embedding_json``.  Results are stamped as degraded.  This
            is honest: the system works, but quality and latency are
            materially worse than with a proper vector index.
        disabled: Vector store is not configured or no embedding
            provider is wired.  No vector search is possible.
        failed: Vector store was configured but initialization or
            runtime operation failed.  The store may be in an
            unrecoverable state.
    """

    AVAILABLE = "available"
    DEGRADED_BRUTEFORCE = "degraded_bruteforce"
    DISABLED = "disabled"
    FAILED = "failed"

    @property
    def is_searchable(self) -> bool:
        """True when vector search can return results (even if degraded)."""
        return self in (VectorBackendStatus.AVAILABLE, VectorBackendStatus.DEGRADED_BRUTEFORCE)

    @property
    def is_degraded(self) -> bool:
        """True when vector search returns results but quality is compromised."""
        return self == VectorBackendStatus.DEGRADED_BRUTEFORCE

    def human_message(self) -> str:
        """Return a human-readable explanation of this status."""
        messages = {
            VectorBackendStatus.AVAILABLE:
                "Vector search is fully operational (indexed backend active).",
            VectorBackendStatus.DEGRADED_BRUTEFORCE:
                "Vector search is degraded: using brute-force cosine scan "
                "(sqlite-vss extension not available). Results may be slower "
                "and less accurate. Install sqlite-vss for production quality.",
            VectorBackendStatus.DISABLED:
                "Vector search is disabled: no vector store or embedding "
                "provider configured. Only lexical/corpus retrieval is available.",
            VectorBackendStatus.FAILED:
                "Vector search has failed: the backend is in an error state. "
                "Only lexical/corpus retrieval is available.",
        }
        return messages.get(self, "Unknown vector backend status.")


@dataclass
class VectorDegradationInfo:
    """Structured degradation metadata for retrieval honesty signaling.

    Attached to RetrievalTrace and AskResult so that every retrieval
    round carries an honest account of what backends were available,
    what was degraded, and why.
    """

    backend_status: VectorBackendStatus = VectorBackendStatus.DISABLED
    backend_name: str = ""
    reason: str = ""
    brute_force_scan_limit: int = 0
    brute_force_rows_scanned: int = 0
    embed_failures: int = 0
    metadata_only_stored: int = 0
    channels_degraded: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize for trace/dashboards/API responses."""
        return {
            "backend_status": self.backend_status.value,
            "backend_name": self.backend_name,
            "reason": self.reason,
            "is_degraded": self.backend_status.is_degraded,
            "is_searchable": self.backend_status.is_searchable,
            "brute_force_scan_limit": self.brute_force_scan_limit,
            "brute_force_rows_scanned": self.brute_force_rows_scanned,
            "embed_failures": self.embed_failures,
            "metadata_only_stored": self.metadata_only_stored,
            "channels_degraded": self.channels_degraded,
            "human_message": self.backend_status.human_message(),
        }


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

    Migration is idempotent and resumable.
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
    "VectorBackendStatus",
    "VectorDegradationInfo",
    "PgvectorConfig",
    "MigrationStatus",
    "MigrationCheckpoint",
]
