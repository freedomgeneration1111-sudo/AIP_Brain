"""VectorRetriever — vector similarity search wrapping VectorStore + EmbeddingProvider.

Implements the Retriever protocol. This completes the retrieval stack by
bringing semantic (embedding-based) search into the RRF fusion pipeline
alongside FTS, Graph, and Wiki retrievers.

Design decisions:
- EmbeddingProvider.embed() is used to vectorize the query
- VectorStore.retrieve() returns Chunk objects which we convert to RetrievalHit
- RetrievalChannel.VECTOR is set on every hit
- Graceful degradation (AIP-G-02): if embedding_provider or vector_store
  is None or unavailable, returns [] (never raises)
- Supports optional domain filtering via query.domain_filter
- Records embedding model info in debug trace

This replaces the legacy _apply_vector_hybrid() bolt-on in ask_pipeline,
which ran AFTER the orchestrator and applied manual weighted scoring.
Now vector results participate in RRF fusion on equal footing with FTS
and Graph results.

Layer: orchestration. Imports from foundation (schemas, protocols).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from aip.foundation.schemas.retrieval_trace import (
    EvidenceStatus,
    RetrievalBudget,
    RetrievalChannel,
    RetrievalHit,
    RetrievalQuery,
    RetrievalTrace,
    RetrieverTrace,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# VectorRetriever
# ---------------------------------------------------------------------------


class VectorRetriever:
    """Vector similarity retriever wrapping VectorStore + EmbeddingProvider.

    Implements the Retriever protocol from foundation.protocols.retrieval.

    Primary search: embed the query via EmbeddingProvider, then retrieve
    similar chunks from VectorStore. Results are converted to RetrievalHit
    with retrieval_channel=RetrievalChannel.VECTOR.

    Graceful degradation (AIP-G-02):
    - If embedding_provider is None, returns [] (no embedding possible)
    - If vector_store is None, returns [] (no vector index)
    - If embed() fails, returns [] and records the error in trace
    - If retrieve() fails, returns [] and records the error in trace
    - Never raises — always returns list[RetrievalHit]
    """

    def __init__(
        self,
        vector_store: Any = None,
        embedding_provider: Any = None,
        default_top_k: int = 30,
        score_normalization: str = "minmax",
    ) -> None:
        """Initialize VectorRetriever.

        Args:
            vector_store: VectorStore protocol implementation (pgvector,
                sqlite-vss, or InMemoryVectorStore).
            embedding_provider: EmbeddingProvider protocol implementation
                (Ollama, OpenAI-compatible, or mock).
            default_top_k: Default number of results to retrieve from
                VectorStore. Will be capped by budget.max_sources.
            score_normalization: Method for normalizing raw vector scores
                to a 0-1 range. "minmax" (default) or "none".
        """
        self._vector_store = vector_store
        self._embedding_provider = embedding_provider
        self._default_top_k = default_top_k
        self._score_normalization = score_normalization

    @property
    def name(self) -> str:
        return "VectorRetriever"

    async def retrieve(
        self,
        query: RetrievalQuery,
        *,
        budget: RetrievalBudget,
        trace: RetrievalTrace,
    ) -> list[RetrievalHit]:
        """Execute vector similarity retrieval.

        Steps:
        1. Embed the query using EmbeddingProvider
        2. Retrieve similar chunks from VectorStore
        3. Normalize and convert to RetrievalHit
        4. Apply budget cap
        """
        # Guard: require both vector_store and embedding_provider
        if self._vector_store is None or self._embedding_provider is None:
            # Record that we're disabled, not broken
            if self._vector_store is None and self._embedding_provider is None:
                self._record_trace(
                    trace, started=time.monotonic(), hit_count=0, top_score=0.0,
                    top_hit_ids=[], errors=None,
                    disabled_reason="no_vector_store_or_embedding_provider",
                )
            elif self._vector_store is None:
                self._record_trace(
                    trace, started=time.monotonic(), hit_count=0, top_score=0.0,
                    top_hit_ids=[], errors=None,
                    disabled_reason="no_vector_store",
                )
            else:
                self._record_trace(
                    trace, started=time.monotonic(), hit_count=0, top_score=0.0,
                    top_hit_ids=[], errors=None,
                    disabled_reason="no_embedding_provider",
                )
            return []

        started = time.monotonic()
        errors: list[str] = []
        domains_seen: list[str] = []

        try:
            # Step 1: Embed the query
            try:
                query_vector = await self._embedding_provider.embed(query.raw_query)
            except Exception as exc:
                msg = f"Embedding failed: {exc}"
                logger.warning(msg)
                errors.append(msg)
                self._record_trace(
                    trace, started=started, hit_count=0, top_score=0.0,
                    top_hit_ids=[], errors=errors,
                )
                trace.fallbacks_triggered.append("vector_embed_failed")
                return []

            if not query_vector or len(query_vector) == 0:
                self._record_trace(
                    trace, started=started, hit_count=0, top_score=0.0,
                    top_hit_ids=[], errors=["empty_embedding"],
                )
                trace.fallbacks_triggered.append("vector_empty_embedding")
                return []

            # Step 2: Retrieve from VectorStore
            top_k = min(self._default_top_k, budget.max_sources * 2)
            domain_filter = query.domain_filter

            try:
                chunks = await self._vector_store.retrieve(
                    query_vector, domain=domain_filter, top_k=top_k
                )
            except Exception as exc:
                msg = f"VectorStore.retrieve() failed: {exc}"
                logger.warning(msg)
                errors.append(msg)
                self._record_trace(
                    trace, started=started, hit_count=0, top_score=0.0,
                    top_hit_ids=[], errors=errors,
                )
                trace.fallbacks_triggered.append("vector_store_failed")
                return []

            if not chunks:
                self._record_trace(
                    trace, started=started, hit_count=0, top_score=0.0,
                    top_hit_ids=[], errors=None,
                )
                return []

            # Step 3: Normalize scores and convert to RetrievalHit
            raw_scores = [c.score for c in chunks]
            normalized = self._normalize_scores(raw_scores)

            hits: list[RetrievalHit] = []
            for i, chunk in enumerate(chunks):
                meta = chunk.metadata or {}
                chunk_type = meta.get("type", "vector_chunk")

                # Build snippet
                snippet = ""
                if chunk.content:
                    snippet = chunk.content[:200].replace("\n", " ")

                # Build entities from metadata
                entities: list[str] = []
                if meta.get("tags"):
                    entities.extend(meta["tags"])
                if meta.get("domains"):
                    entities.extend(meta["domains"])

                domain = chunk.domain or meta.get("domain")

                hit = RetrievalHit(
                    id=chunk.id,
                    source_type=chunk_type,
                    source_id=chunk.id,
                    title=meta.get("title", chunk.id),
                    text=chunk.content or "",
                    snippet=snippet,
                    rank=i + 1,
                    score=round(normalized[i], 4),
                    confidence=0.0,  # Vector search doesn't have confidence tags
                    importance=meta.get("importance"),
                    domain=domain,
                    entities=entities,
                    retrieval_channel=RetrievalChannel.VECTOR,
                    evidence_status=EvidenceStatus.RAW,
                    debug={
                        "source": "vector_retriever",
                        "raw_score": round(chunk.score, 6),
                        "normalized_score": round(normalized[i], 4),
                        "dimension": len(query_vector),
                        "conversation_id": meta.get("conversation_id", ""),
                    },
                )
                hits.append(hit)

                if domain and domain not in domains_seen:
                    domains_seen.append(domain)

            # Step 4: Apply budget cap
            hits = hits[: budget.max_sources]

            # Re-rank after cap
            for i, hit in enumerate(hits, start=1):
                hit.rank = i

            # Record trace
            self._record_trace(
                trace, started=started, hit_count=len(hits),
                top_score=hits[0].score if hits else 0.0,
                top_hit_ids=[h.id for h in hits[:10]],
                errors=errors if errors else None,
                domains_seen=domains_seen,
                embedding_dim=len(query_vector),
                raw_chunk_count=len(chunks),
            )

            return hits

        except Exception as exc:
            logger.error("VectorRetriever failed unexpectedly: %s", exc)
            self._record_trace(
                trace, started=started, hit_count=0, top_score=0.0,
                top_hit_ids=[], errors=[str(exc)],
            )
            trace.fallbacks_triggered.append("vector_retriever_error")
            return []

    def _normalize_scores(self, raw_scores: list[float]) -> list[float]:
        """Normalize raw vector scores to 0-1 range.

        Uses min-max normalization by default, which maps the lowest
        score to 0 and the highest to 1. This ensures vector scores
        are on the same scale as FTS position-based scores (0.5-1.0)
        for fair RRF fusion.

        If only one result, returns [0.8] (reasonable default).
        """
        if not raw_scores:
            return []

        if self._score_normalization == "none":
            return raw_scores

        # Min-max normalization
        min_score = min(raw_scores)
        max_score = max(raw_scores)
        score_range = max_score - min_score

        if score_range == 0:
            # All scores identical — give them all a reasonable mid-high score
            return [0.8] * len(raw_scores)

        # Normalize to 0.3-1.0 range (never go below 0.3 since these
        # are legitimate vector matches, not random noise)
        normalized = []
        for s in raw_scores:
            norm = 0.3 + 0.7 * ((s - min_score) / score_range)
            normalized.append(norm)

        return normalized

    def _record_trace(
        self,
        trace: RetrievalTrace,
        *,
        started: float,
        hit_count: int,
        top_score: float,
        top_hit_ids: list[str],
        errors: list[str] | None = None,
        domains_seen: list[str] | None = None,
        embedding_dim: int = 0,
        raw_chunk_count: int = 0,
        disabled_reason: str = "",
    ) -> None:
        """Record retriever trace into the shared RetrievalTrace."""
        elapsed_ms = (time.monotonic() - started) * 1000.0
        degraded = bool(errors) and hit_count > 0
        error_msg = "; ".join(errors) if errors else None

        enabled = not bool(disabled_reason)

        retriever_trace = RetrieverTrace(
            retriever_name=self.name,
            enabled=enabled,
            degraded=degraded,
            error=error_msg,
            started_at=datetime.now(),
            finished_at=datetime.now(),
            elapsed_ms=round(elapsed_ms, 2),
            hit_count=hit_count,
            top_score=top_score,
            top_hit_ids=top_hit_ids,
            debug={
                "channel": "vector",
                "embedding_dim": embedding_dim,
                "raw_chunk_count": raw_chunk_count,
                "score_normalization": self._score_normalization,
                "disabled_reason": disabled_reason,
                "domains": domains_seen or [],
            },
        )
        trace.retriever_traces.append(retriever_trace)


__all__ = ["VectorRetriever"]
