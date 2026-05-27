"""
Phase 0 protocol definitions (stub form).

Per ANNEX D of AIP 0.1 BuildSpec Phase 0.

This file will be amended by addition only (never rewritten) starting in CHUNK-1.0a.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

# Phase 0 VectorStore (the version that CHUNK-1.0a will amend)
@runtime_checkable
class VectorStore(Protocol):
    """Vector store abstraction for pgvector / sqlite-vss swap.
    Phase 1 amendment (CHUNK-1.0a): added upsert, retrieve with query_vector, delete.
    Phase 0 store() method is deprecated but retained for backward compat.
    """

    # Phase 1 methods (added by CHUNK-1.0a per Rev 1.3)
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
    ) -> list:  # list[Chunk] after schemas append
        """Retrieve chunks by vector similarity."""
        ...

    async def delete(self, id: str) -> None:
        """Delete a vector entry by id."""
        ...

    async def count(self, domain: str | None = None) -> int:
        """Count entries, optionally filtered by domain."""
        ...

    # Deprecated Phase 0 method — retained for backward compatibility
    async def store(self, chunk: object) -> str:
        """Deprecated: use upsert() instead. Returns chunk id."""
        ...


# Other Phase 0 protocols as empty runtime_checkable stubs
@runtime_checkable
class LexicalStore(Protocol):
    """SQLite FTS5 lexical search."""
    ...


@runtime_checkable
class CanonicalStore(Protocol):
    """DEFINER-approved durable artifact store."""
    ...


@runtime_checkable
class ArtifactStore(Protocol):
    """Artifact store for reading and writing generated content.
    P1 fix (CHUNK-1.0a): Phase 0 left this as `...` stub.
    Method signatures match CHUNK-1.6 call sites.
    """
    async def write(self, id: str, content: str, metadata: dict) -> None:
        """Write artifact content with metadata."""
        ...

    async def read(self, id: str) -> str:
        """Read artifact content by id."""
        ...


@runtime_checkable
class TraceStore(Protocol):
    """Trace store for logging node execution events.
    P1 fix (CHUNK-1.0a): Phase 0 left this as `...` stub.
    Method signature matches CHUNK-1.1 call site.
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

    # L4 / CHUNK-3.1 addition (amend by addition only — never rewrite existing methods)
    # Provides the query surface required by TrajectoryMonitor and future Sexton (§10.1, §16.1).
    # Implementations must return events in descending created_at order (most recent first).
    async def get_recent_events(
        self, session_id: str, limit: int = 100
    ) -> list[dict]:
        """Return recent trace events for a session (raw dicts matching §5.9 columns).
        Used by L4 trajectory regulation for drift/loop detection. Zero tokens.
        """
        ...


@runtime_checkable
class EntityStore(Protocol):
    """Entity and operations store."""
    ...


@runtime_checkable
class EventStore(Protocol):
    """Event store for recording ECS state transitions and lifecycle events.
    P1 fix (CHUNK-1.0a): Phase 0 left this as `...` stub.
    Method signature matches CHUNK-1.6 call site.
    """
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


@runtime_checkable
class ProjectStore(Protocol):
    """Project and WorkUnit persistent state."""
    ...


@runtime_checkable
class EcsStore(Protocol):
    """Artifact governance. EcsStore.transition() is the only legal path for state changes."""

    def transition(
        self,
        artifact_id: str,
        from_state: str,
        to_state: str,
        actor: str,
        reason: str,
        superseded_by: str | None = None,
    ) -> None: ...


@runtime_checkable
class BudgetStore(Protocol):
    """Budget and autonomy tracking."""
    ...


@runtime_checkable
class AutonomyGate(Protocol):
    """Two-phase autonomy gate."""
    ...