"""Core storage Protocol definitions.

Abstractions for all persistent stores: vector search, full-text search,
artifact management, ECS governance, event sourcing, trace logging,
entity management, and project state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from aip.foundation.schemas import Chunk, Event


@runtime_checkable
class VectorStore(Protocol):
    """Vector store abstraction for pgvector / sqlite-vss swap.

    Supports upsert, retrieval by vector similarity, deletion, health
    checking, and stale vector listing for corpus maintenance.
    """

    async def upsert(
        self,
        id: str,
        embedding: list[float],
        content: str,
        metadata: dict,
        domain: str | None = None,
    ) -> None:
        """Store or update a vector with metadata."""
        ...

    async def retrieve(
        self,
        query_vector: list[float],
        domain: str | None = None,
        top_k: int = 10,
    ) -> list[Chunk]:
        """Retrieve chunks by vector similarity."""
        ...

    async def delete(self, id: str) -> None:
        """Delete a vector entry by id."""
        ...

    async def count(self, domain: str | None = None) -> int:
        """Count entries, optionally filtered by domain."""
        ...

    async def store(self, chunk: Chunk) -> str:
        """Deprecated: use upsert() instead. Returns chunk id."""
        ...

    async def health_check(self) -> dict:
        """Check backend health and return status.

        Returns dict with: connected (bool), pool_size (int),
        latency_ms (int), backend_name (str).
        Used by aip status and production hardening.
        """
        ...

    async def list_stale_vectors(
        self,
        threshold_days: int = 30,
        domain: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """List vectors that have not been updated within threshold_days.

        Used by Beast corpus maintenance and Vigil to identify stale vectors
        that may need re-embedding after model slot changes or knowledge updates.

        Returns list of dicts with: id, domain, updated_at/created_at, metadata.
        """
        ...

    async def list_all_ids(
        self,
        offset: int = 0,
        limit: int = 500,
        domain: str | None = None,
    ) -> list[str]:
        """List all vector IDs with cursor-based pagination.

        Used by vector migration for deterministic complete scanning.
        Without this method, migration falls back to probe-based retrieval
        which may miss vectors in sparse regions.

        Args:
            offset: Number of IDs to skip (for pagination).
            limit: Maximum number of IDs to return.
            domain: Optional domain filter.

        Returns:
            List of vector ID strings.
        """
        ...


@runtime_checkable
class LexicalStore(Protocol):
    """Full-text search abstraction.

    Abstracts SQLite FTS5 so that orchestration and adapter code
    never import sqlite3 directly for search operations.
    Supports domain-filtered retrieval. Laptop-viable, local-only.
    """

    async def search(
        self,
        query: str,
        domain: str | None = None,
        limit: int = 10,
    ) -> list[Chunk]:
        """Full-text search for documents matching query.

        Returns Chunk results with score = FTS5 rank.
        Optionally filtered by domain.
        """
        ...

    async def index_document(
        self,
        doc_id: str,
        content: str,
        domain: str,
        metadata: dict,
    ) -> None:
        """Add or update a document in the FTS5 index.

        Idempotent — re-indexing the same doc_id updates content.
        """
        ...

    async def delete_document(self, doc_id: str) -> None:
        """Remove a document from the FTS5 index.

        Supersession marks old entries, does not delete them — but stale
        FTS5 entries should be cleaned up.
        """
        ...


@runtime_checkable
class CanonicalStore(Protocol):
    """DEFINER-approved durable artifact store."""

    async def read_canonical(self, artifact_id: str) -> dict | None:
        """Read a canonical artifact by ID.

        Returns None if no canonical version exists.
        Canonical artifacts are DEFINER-approved.
        """
        ...

    async def write_canonical(self, artifact_id: str, content: dict, approved_by: str) -> None:
        """Write a canonical artifact.

        Only called after DEFINER approval (ECS APPROVED state).
        approved_by must be "definer" — enforced by AutonomyGate.
        """
        ...

    async def list_canonical(self, domain: str | None = None) -> list[dict]:
        """List canonical artifacts, optionally filtered by domain.

        Returns list of dicts with artifact_id, domain, approved_by, created_at.
        """
        ...


@runtime_checkable
class ArtifactStore(Protocol):
    """Artifact store for reading and writing generated content.

    Method signatures match call sites.
    """

    async def write(self, id: str, content: str, metadata: dict) -> None:
        """Write artifact content with metadata."""
        ...

    async def read(self, id: str, version: int | None = None) -> str:
        """Read artifact content by id.

        version=None returns the latest version.
        version=N returns the specific version (1-indexed).
        """
        ...

    async def list_versions(self, id: str) -> list[int]:
        """Return list of available version numbers for this artifact id."""
        ...


@runtime_checkable
class TraceStore(Protocol):
    """Trace store for logging node execution events.

    Method name is write_event (confirmed per Rev 1.3 R2').
    """

    async def write_event(
        self,
        session_id: str,
        node_type: str,
        failure_type: str,
        outcome: str,
        detail: str | None = None,
    ) -> None:
        """Write a trace event for node execution."""
        ...

    async def query_events(
        self,
        session_id: str,
        node_type: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query trace events for a session (raw dicts).

        Used by L4 trajectory detectors (loop, anxiety, failure streak).
        """
        ...

    async def get_recent_events(self, session_id: str, limit: int = 100) -> list[dict]:
        """Return recent trace events for a session.

        Events returned in descending created_at order (most recent first).
        Used by L4 trajectory regulation for drift/loop detection. Zero tokens.
        """
        ...

    async def get_unclassified_failures(self, limit: int = 100) -> list[dict]:
        """Return recent unclassified failures (failure_type IS NULL and outcome == 'failure').

        Used by Sexton for Appendix E classification. Zero tokens.
        """
        ...


@runtime_checkable
class EntityStore(Protocol):
    """Entity and operations store."""

    async def get_entity(self, entity_id: str) -> dict | None:
        """Get an entity by ID.

        Returns None if entity does not exist.
        """
        ...

    async def list_entities(self, entity_type: str | None = None) -> list[dict]:
        """List entities, optionally filtered by type.

        Returns list of dicts with entity_id, entity_type, name, metadata.
        """
        ...

    async def update_entity(self, entity_id: str, updates: dict) -> None:
        """Update entity fields.

        updates is a dict of field->value pairs to apply.
        """
        ...


@runtime_checkable
class EventStore(Protocol):
    """Event store for recording ECS state transitions and lifecycle events."""

    async def write_event(
        self,
        event_type: str,
        actor: str,
        artifact_id: str,
        from_state: str | None = None,
        to_state: str | None = None,
        **kwargs,
    ) -> None:
        """Write an event recording a state transition or lifecycle event."""
        ...

    async def query(
        self,
        artifact_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Query events by artifact_id and/or event_type.

        Returns most recent events first (descending timestamp).
        Used by review node, DEFINER audit, and Sexton analysis.
        """
        ...


@runtime_checkable
class ProjectStore(Protocol):
    """Project and WorkUnit persistent state."""

    async def list_projects(self, status: str | None = None) -> list[dict]:
        """List projects, optionally filtered by status.

        Used by Beast for corpus maintenance iteration.
        Returns list of dicts with project_id, name, status, etc.
        """
        ...


@runtime_checkable
class EcsStore(Protocol):
    """Artifact governance. EcsStore.transition() is the only legal path for state changes."""

    async def transition(
        self,
        artifact_id: str,
        from_state: str | None,
        to_state: str,
        actor: str,
        reason: str,
        superseded_by: str | None = None,
    ) -> None: ...

    async def current_state(self, artifact_id: str) -> str | None:
        """Return the current ECS state of the artifact, or None if unknown."""
        ...


@runtime_checkable
class SessionStore(Protocol):
    """Session persistence store for chat session state.

    Stores session metadata (role, model slot, turn count, etc.)
    so that sessions survive process restarts. When SessionStore
    is not available, the API falls back to an in-memory dict.
    """

    async def create_session(self, session_id: str, metadata: dict) -> None:
        """Create a new session with the given metadata."""
        ...

    async def get_session(self, session_id: str) -> dict | None:
        """Get session metadata by ID. Returns None if not found."""
        ...

    async def list_sessions(self, limit: int = 100) -> list[dict]:
        """List sessions, most recently updated first."""
        ...

    async def update_session(self, session_id: str, updates: dict) -> None:
        """Update session fields. Creates the session if it does not exist."""
        ...

    async def delete_session(self, session_id: str) -> None:
        """Delete a session by ID."""
        ...


__all__ = [
    "VectorStore",
    "LexicalStore",
    "CanonicalStore",
    "ArtifactStore",
    "TraceStore",
    "EntityStore",
    "EventStore",
    "ProjectStore",
    "EcsStore",
    "SessionStore",
]
