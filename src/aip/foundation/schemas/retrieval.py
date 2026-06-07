"""Retrieval-related types.

Unified retrieval chunk, hit, and result types used across the retrieval
pipeline.  RetrievalHit is the canonical type produced by
RetrievalOrchestrator and consumed by SmartContextPacker.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Chunk:
    """Unified retrieval chunk. Replaces RetrievalHit.

    Used by VectorStore.retrieve() and retrieve_for_synthesis().
    One type for the entire retrieval pipeline.
    """

    id: str
    content: str | None = None
    score: float = 0.0
    metadata: dict = field(default_factory=dict)
    domain: str | None = None


@dataclass
class RetrievalResult:
    """Result of retrieve_for_synthesis with low-confidence gate status."""

    status: str  # "OK" | "INSUFFICIENT_MEMORY"
    hits: list[Chunk]
    max_confidence: float
    message: str | None = None


# ---------------------------------------------------------------------------
# Sprint 5.6+ types — multi-channel retrieval pipeline
# ---------------------------------------------------------------------------


@dataclass
class RetrievalHit:
    """Canonical retrieval hit produced by RetrievalOrchestrator.

    Every retriever channel (FTS, Vector, Graph, Wiki, Procedural) converts
    its native results into RetrievalHit instances so that downstream
    components (SmartContextPacker, trace logging, quality gate) work with a
    single, uniform type.

    Attributes:
        id: Unique identifier (chunk_id, turn_id, artifact_id, etc.).
        content: Full text of the hit (used for extractive summarization).
        score: Raw retrieval score from the source channel.
        rrf_score: Reciprocal-rank fusion score (populated after RRF merge).
        source_channel: Which retriever produced this hit
            (``"fts"``, ``"vector"``, ``"graph"``, ``"wiki"``, ``"procedural"``).
        domain: Knowledge domain (may be empty).
        metadata: Arbitrary key/value metadata (type, conversation_id, etc.).
        rank_in_channel: Position within the channel's result list (1-based).
        elapsed_ms: Wall-clock time this individual retriever took (ms).
    """

    id: str
    content: str = ""
    score: float = 0.0
    rrf_score: float = 0.0
    source_channel: str = ""
    domain: str = ""
    metadata: dict = field(default_factory=dict)
    rank_in_channel: int = 0
    elapsed_ms: float = 0.0


@dataclass
class RetrievalTrace:
    """Trace record for a single retrieval round.

    Captures per-channel timing, hit counts, and the fused result set so
    that TraceStore analytics and the dashboard endpoint can report on
    retrieval performance without re-running queries.

    Attributes:
        session_id: Correlation ID for the ask session.
        query: The original user query (or expanded query text).
        round_number: Which retry round (0 = first attempt).
        channels_queried: List of channel names that were dispatched.
        per_channel_elapsed_ms: Mapping of channel name → elapsed ms.
        total_elapsed_ms: End-to-end wall-clock for this round.
        hits_before_fusion: Total hits collected before RRF fusion.
        hits_after_fusion: Total hits after RRF fusion + dedup.
        hits_after_quality_gate: Hits remaining after quality gate.
        verdict: ``"OK"`` | ``"NEEDS_MORE_CONTEXT"`` | ``"NO_RESULTS"``.
    """

    session_id: str = ""
    query: str = ""
    round_number: int = 0
    channels_queried: list[str] = field(default_factory=list)
    per_channel_elapsed_ms: dict[str, float] = field(default_factory=dict)
    total_elapsed_ms: float = 0.0
    hits_before_fusion: int = 0
    hits_after_fusion: int = 0
    hits_after_quality_gate: int = 0
    verdict: str = "OK"


__all__ = [
    "Chunk",
    "RetrievalResult",
    "RetrievalHit",
    "RetrievalTrace",
]
