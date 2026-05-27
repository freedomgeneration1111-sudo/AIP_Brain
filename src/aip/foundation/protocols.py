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
    """Abstracts pgvector and sqlite-vss. Both backends implement this."""

    async def retrieve(self, query: str, domain: str, limit: int) -> list: ...  # returns list[Chunk] in practice

    async def store(self, chunk: object) -> str: ...  # returns ChunkId


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
    """Response artifact store."""
    ...


@runtime_checkable
class TraceStore(Protocol):
    """Harness observability. Reads/writes trace_events."""
    ...


@runtime_checkable
class EntityStore(Protocol):
    """Entity and operations store."""
    ...


@runtime_checkable
class EventStore(Protocol):
    """Append-only durable event log."""
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