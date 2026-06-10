"""Retrieval-related types.

Unified retrieval chunk, hit, and result types used across the retrieval
pipeline.  RetrievalHit is the canonical type produced by
RetrievalOrchestrator and consumed by SmartContextPacker.

Sprint 10 additions:
- ``ChannelHealthState`` enum for per-channel health tracking.
- ``ChannelHealthReport`` for structured health summaries.
- Enhanced ``RetrievalTrace`` with unified channel health, query expansion,
  entities extracted, documents retrieved, scores, and final context selection.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aip.foundation.schemas.vector import VectorDegradationInfo


# ---------------------------------------------------------------------------
# Sprint 10: Channel health states
# ---------------------------------------------------------------------------


class ChannelHealthState(enum.Enum):
    """Health state of a single retriever channel.

    Every channel in the retrieval pipeline reports its health state
    on each retrieval round.  This enables the unified trace to honestly
    report which channels were available, which degraded, and which
    failed — so that weak answers can be diagnosed.

    Values:
        active: Channel dispatched successfully and returned results.
        degraded: Channel dispatched but quality was compromised
            (e.g. vector brute-force fallback, partial results).
        failed: Channel dispatched but raised an error or returned
            a structured ChannelFailure.
        disabled: Channel was not enabled for this retrieval round
            (not dispatched at all).
        unavailable: Channel's backing store is not present or not
            configured (e.g. graph_store is None, wiki_store missing).
            This is distinct from "disabled" (operator chose not to
            enable) and "failed" (store was there but errored).
        not_configured: Channel could not register because a required
            dependency was missing at registration time (e.g. no
            embedding provider for the vector channel).
        empty: Channel dispatched successfully and returned zero
            results.  This is distinct from "active" (which implies
            results were returned) and from "failed" (no error occurred).
    """

    ACTIVE = "active"
    DEGRADED = "degraded"
    FAILED = "failed"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"
    EMPTY = "empty"

    @property
    def is_available(self) -> bool:
        """True when the channel can contribute results (even if degraded)."""
        return self in (ChannelHealthState.ACTIVE, ChannelHealthState.DEGRADED, ChannelHealthState.EMPTY)

    @property
    def is_healthy(self) -> bool:
        """True only when the channel is fully active (no degradation)."""
        return self == ChannelHealthState.ACTIVE

    @property
    def was_attempted(self) -> bool:
        """True when the channel was dispatched (even if it returned nothing or failed)."""
        return self in (ChannelHealthState.ACTIVE, ChannelHealthState.DEGRADED, ChannelHealthState.FAILED, ChannelHealthState.EMPTY)


@dataclass
class ChannelHealthDetail:
    """Per-channel health detail for retrieval honesty.

    Each retrieval channel reports this structured detail on every
    retrieval round, providing enough information for operators and
    dashboards to diagnose exactly what happened without scraping logs.

    Chunk 5 addition: This is the primary unit of retrieval honesty.
    It replaces the raw string-only channel_health dict with structured
    data that includes attempt status, result counts, latency, and
    degradation reasons.

    Attributes:
        channel: Name of the channel (e.g. ``"fts"``, ``"vector"``).
        state: Current health state.
        attempted: Whether the channel was dispatched for this round.
        succeeded: Whether the channel returned results without error.
        result_count: Number of hits returned by this channel.
        latency_ms: Wall-clock time in ms for this channel's dispatch.
        degradation_reason: Human-readable reason for degraded/unavailable/
            failed/not_configured state (empty string for active).
        error_summary: Short error message if the channel failed.
        backend_type: For vector channel: "sqlite_vss", "pgvector",
            "brute_force", "in_memory", or empty string.
        vss_available: For vector channel: whether sqlite-vss extension
            is loaded.
        vector_count: For vector channel: total number of stored vectors.
        embedding_provider_configured: For vector channel: whether an
            embedding provider is available.
    """

    channel: str = ""
    state: ChannelHealthState = ChannelHealthState.DISABLED
    attempted: bool = False
    succeeded: bool = False
    result_count: int = 0
    latency_ms: float = 0.0
    degradation_reason: str = ""
    error_summary: str = ""
    # Vector-specific fields
    backend_type: str = ""
    vss_available: bool | None = None
    vector_count: int | None = None
    embedding_provider_configured: bool | None = None

    def to_dict(self) -> dict:
        """Serialize for trace/dashboards/API responses."""
        d: dict = {
            "channel": self.channel,
            "state": self.state.value,
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "result_count": self.result_count,
            "latency_ms": round(self.latency_ms, 2),
            "degradation_reason": self.degradation_reason,
        }
        if self.error_summary:
            d["error_summary"] = self.error_summary
        # Vector-specific fields (only include when populated)
        if self.backend_type:
            d["backend_type"] = self.backend_type
        if self.vss_available is not None:
            d["vss_available"] = self.vss_available
        if self.vector_count is not None:
            d["vector_count"] = self.vector_count
        if self.embedding_provider_configured is not None:
            d["embedding_provider_configured"] = self.embedding_provider_configured
        return d


@dataclass
class ChannelHealthReport:
    """Structured health summary for all retrieval channels.

    Produced by the retrieval orchestrator after each round and
    attached to the RetrievalTrace.  Provides a snapshot of every
    channel's health state with reasons for any issues.

    Attributes:
        channel_states: Mapping of channel name → ChannelHealthState.
        reasons: Mapping of channel name → human-readable reason for
            non-active state (empty string for active channels).
        timestamp: ISO timestamp when the report was generated.
    """

    channel_states: dict[str, ChannelHealthState] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    timestamp: str = ""

    def get_active(self) -> list[str]:
        """Return channel names with ACTIVE health."""
        return [ch for ch, s in self.channel_states.items() if s == ChannelHealthState.ACTIVE]

    def get_degraded(self) -> list[str]:
        """Return channel names with DEGRADED health."""
        return [ch for ch, s in self.channel_states.items() if s == ChannelHealthState.DEGRADED]

    def get_failed(self) -> list[str]:
        """Return channel names with FAILED health."""
        return [ch for ch, s in self.channel_states.items() if s == ChannelHealthState.FAILED]

    def get_disabled(self) -> list[str]:
        """Return channel names with DISABLED health."""
        return [ch for ch, s in self.channel_states.items() if s == ChannelHealthState.DISABLED]

    def to_dict(self) -> dict:
        """Serialize for trace/dashboards/API responses."""
        return {
            "channel_states": {ch: s.value for ch, s in self.channel_states.items()},
            "reasons": self.reasons,
            "timestamp": self.timestamp,
            "active": self.get_active(),
            "degraded": self.get_degraded(),
            "failed": self.get_failed(),
            "disabled": self.get_disabled(),
        }

    def format_warnings(self) -> list[str]:
        """Generate human-readable warnings for non-active channels.

        Returns a list of warning strings suitable for surfacing in
        AskResult.retrieval_warnings or CLI output.
        """
        warnings: list[str] = []
        for ch, state in self.channel_states.items():
            if state == ChannelHealthState.FAILED:
                reason = self.reasons.get(ch, "unknown error")
                warnings.append(f"{ch.capitalize()} channel unavailable: {reason}")
            elif state == ChannelHealthState.DEGRADED:
                reason = self.reasons.get(ch, "degraded quality")
                warnings.append(f"{ch.capitalize()} channel degraded: {reason}")
        return warnings


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
# Multi-channel retrieval pipeline types
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

    Sprint 10 — Unified RetrievalTrace:
    Every ask returns comprehensive diagnostic information:

    - **Channel health**: Per-channel active/degraded/failed status with
      reasons, so every answer can explain what retrieval backends were
      available and which ones had issues.
    - **Query expansion**: What terms (if any) were added to the original
      query before dispatch.
    - **Entities extracted**: What entities were identified from the query
      (used by graph channel and for ranking signals).
    - **Documents retrieved**: Count and IDs of documents that survived
      the full pipeline (fusion + quality gate + source filter).
    - **Scores**: Top retrieval scores for quick quality assessment.
    - **Final context selected**: What was actually packed into the LLM
      context window (after SmartContextPacker budgeting).

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
        channel_contributions: Mapping of channel name → count of hits from
            that channel that survived RRF fusion and the quality gate.
        per_channel_hit_counts: Mapping of channel name → total hits
            returned by that channel before budget enforcement.
        llm_entity_extraction_ms: Wall-clock time in ms for LLM entity
            extraction (0.0 if not invoked).
        llm_entity_extraction_status: Status of LLM entity extraction:
            ``"not_used"``, ``"success"``, ``"failed"``, or ``"timeout"``.
        llm_entity_count: Number of entities returned by LLM extraction.
        vector_degradation: Vector backend degradation metadata.

    Sprint 10 new fields:
        channel_health: Per-channel health state mapping
            (channel_name → ChannelHealthState value).
        channel_health_reasons: Per-channel reason for degraded/failed
            state (channel_name → human-readable reason string).
        query_expansion: Terms added during query expansion.
        entities_extracted: Entities extracted from the query.
        documents_retrieved_ids: IDs of documents that survived the
            full pipeline.
        top_scores: Top N retrieval scores (raw and RRF) for quick
            quality assessment.
        final_context_token_count: Token count of the final packed
            context after SmartContextPacker budgeting.
        final_context_source_ids: IDs of sources included in the
            final context sent to the LLM.
        degradation_warnings: Human-readable warnings about retrieval
            degradation, suitable for surfacing to the user.
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
    # Channel contribution tracking
    channel_contributions: dict[str, int] = field(default_factory=dict)
    per_channel_hit_counts: dict[str, int] = field(default_factory=dict)
    # LLM entity extraction observability
    llm_entity_extraction_ms: float = 0.0
    llm_entity_extraction_status: str = "not_used"  # "not_used" | "success" | "failed" | "timeout"
    llm_entity_count: int = 0
    # Vector backend degradation signaling (Chunk 5)
    vector_degradation: VectorDegradationInfo = field(
        default_factory=lambda: __import__(
            "aip.foundation.schemas.vector", fromlist=["VectorDegradationInfo"]
        ).VectorDegradationInfo(),
    )

    # ------------------------------------------------------------------
    # Sprint 10: Unified RetrievalTrace fields
    # ------------------------------------------------------------------

    # Per-channel health: channel_name → "active" | "degraded" | "failed" | "disabled" | "unavailable" | "not_configured" | "empty"
    channel_health: dict[str, str] = field(default_factory=dict)
    # Per-channel reason for degraded/failed state
    channel_health_reasons: dict[str, str] = field(default_factory=dict)
    # Chunk 5: Per-channel structured health details
    channel_details: dict[str, ChannelHealthDetail] = field(default_factory=dict)
    # Query expansion terms
    query_expansion: list[str] = field(default_factory=list)
    # Entities extracted from query (for graph channel / ranking signals)
    entities_extracted: list[str] = field(default_factory=list)
    # IDs of documents that survived the full pipeline
    documents_retrieved_ids: list[str] = field(default_factory=list)
    # Top retrieval scores for quick quality assessment
    top_scores: list[dict] = field(default_factory=list)  # [{"id": ..., "rrf_score": ..., "raw_score": ...}]
    # Token count of the final packed context
    final_context_token_count: int = 0
    # IDs of sources included in the final LLM context
    final_context_source_ids: list[str] = field(default_factory=list)
    # Human-readable warnings about retrieval degradation
    degradation_warnings: list[str] = field(default_factory=list)
    # Chunk 5: Retrieval honesty flags
    # Whether the answer was produced using only lexical/corpus channels
    lexical_only: bool = False
    # Whether vector context contributed to the final answer
    vector_contributed: bool = False
    # Names of channels that were attempted (dispatched)
    channels_attempted: list[str] = field(default_factory=list)
    # Names of channels that returned results (active or degraded)
    channels_used: list[str] = field(default_factory=list)

    def degradation_summary(self) -> str:
        """Return a human-readable summary of retrieval degradation.

        This is the honest message the system is allowed — and required —
        to give: 'I answered from lexical/corpus memory only. Semantic vector
        retrieval was unavailable.' That is better than a confident but
        secretly weakened answer.

        Sprint 10: Now also includes per-channel health warnings from
        ``channel_health`` and ``degradation_warnings``.
        """
        parts = []
        vdi = self.vector_degradation
        if vdi.backend_status.is_degraded:
            parts.append(
                f"Vector search was degraded (brute-force scan, "
                f"{vdi.brute_force_rows_scanned} rows scanned). "
                "Install sqlite-vss for production-quality vector retrieval."
            )
        elif vdi.backend_status.value == "disabled":
            parts.append(
                "I answered from lexical/corpus memory only. "
                "Semantic vector retrieval was unavailable."
            )
        elif vdi.backend_status.value == "failed":
            parts.append(
                f"Vector search failed: {vdi.reason or 'unknown error'}. "
                "I answered from lexical/corpus memory only."
            )
        if vdi.embed_failures > 0:
            parts.append(
                f"{vdi.embed_failures} embedding(s) failed during storage; "
                f"{vdi.metadata_only_stored} chunk(s) stored as metadata-only "
                "(unsearchable by vector)."
            )
        # Sprint 10 + Chunk 5: Add channel health warnings (all non-active states)
        for channel, health in self.channel_health.items():
            if health == "failed":
                reason = self.channel_health_reasons.get(channel, "unknown error")
                parts.append(f"{channel.capitalize()} channel failed: {reason}")
            elif health == "degraded":
                reason = self.channel_health_reasons.get(channel, "degraded quality")
                parts.append(f"{channel.capitalize()} channel degraded: {reason}")
            elif health == "unavailable":
                reason = self.channel_health_reasons.get(channel, "store not present")
                parts.append(f"{channel.capitalize()} channel unavailable: {reason}")
            elif health == "not_configured":
                reason = self.channel_health_reasons.get(channel, "missing dependency")
                parts.append(f"{channel.capitalize()} channel not configured: {reason}")
            elif health == "empty":
                reason = self.channel_health_reasons.get(channel, "")
                if reason:
                    parts.append(f"{channel.capitalize()} channel returned no results: {reason}")
        # Add explicit degradation warnings
        if self.degradation_warnings:
            parts.extend(self.degradation_warnings)
        return " ".join(parts)

    def get_active_channels(self) -> list[str]:
        """Return names of channels with 'active' health."""
        return [ch for ch, h in self.channel_health.items() if h == "active"]

    def get_failed_channels(self) -> list[str]:
        """Return names of channels with 'failed' health."""
        return [ch for ch, h in self.channel_health.items() if h == "failed"]

    def get_degraded_channels(self) -> list[str]:
        """Return names of channels with 'degraded' health."""
        return [ch for ch, h in self.channel_health.items() if h == "degraded"]

    def get_unavailable_channels(self) -> list[str]:
        """Return names of channels with 'unavailable' health."""
        return [ch for ch, h in self.channel_health.items() if h == "unavailable"]

    def get_not_configured_channels(self) -> list[str]:
        """Return names of channels with 'not_configured' health."""
        return [ch for ch, h in self.channel_health.items() if h == "not_configured"]

    def get_empty_channels(self) -> list[str]:
        """Return names of channels with 'empty' health."""
        return [ch for ch, h in self.channel_health.items() if h == "empty"]

    def to_diagnostic_dict(self) -> dict:
        """Serialize the full trace for diagnostic dashboards and eval.

        Includes all fields needed to diagnose retrieval quality issues:
        channel health, expansion, entities, documents, scores, context.
        """
        vdi = self.vector_degradation
        return {
            "session_id": self.session_id,
            "query": self.query,
            "round_number": self.round_number,
            "channels_queried": self.channels_queried,
            "channel_health": self.channel_health,
            "channel_health_reasons": self.channel_health_reasons,
            "channel_details": {ch: d.to_dict() for ch, d in self.channel_details.items()},
            "active_channels": self.get_active_channels(),
            "failed_channels": self.get_failed_channels(),
            "degraded_channels": self.get_degraded_channels(),
            "unavailable_channels": self.get_unavailable_channels(),
            "not_configured_channels": self.get_not_configured_channels(),
            "empty_channels": self.get_empty_channels(),
            "channels_attempted": self.channels_attempted,
            "channels_used": self.channels_used,
            "lexical_only": self.lexical_only,
            "vector_contributed": self.vector_contributed,
            "query_expansion": self.query_expansion,
            "entities_extracted": self.entities_extracted,
            "per_channel_elapsed_ms": self.per_channel_elapsed_ms,
            "total_elapsed_ms": round(self.total_elapsed_ms, 2),
            "hits_before_fusion": self.hits_before_fusion,
            "hits_after_fusion": self.hits_after_fusion,
            "hits_after_quality_gate": self.hits_after_quality_gate,
            "verdict": self.verdict,
            "channel_contributions": self.channel_contributions,
            "per_channel_hit_counts": self.per_channel_hit_counts,
            "documents_retrieved_ids": self.documents_retrieved_ids,
            "top_scores": self.top_scores[:10],
            "final_context_token_count": self.final_context_token_count,
            "final_context_source_ids": self.final_context_source_ids,
            "degradation_warnings": self.degradation_warnings,
            "degradation_summary": self.degradation_summary(),
            "vector_backend_status": vdi.backend_status.value,
            "vector_backend_name": vdi.backend_name,
            "vector_degraded": vdi.backend_status.is_degraded,
            "llm_entity_extraction_ms": self.llm_entity_extraction_ms,
            "llm_entity_extraction_status": self.llm_entity_extraction_status,
            "llm_entity_count": self.llm_entity_count,
        }


__all__ = [
    "ChannelHealthState",
    "ChannelHealthDetail",
    "ChannelHealthReport",
    "Chunk",
    "RetrievalResult",
    "RetrievalHit",
    "RetrievalTrace",
]
