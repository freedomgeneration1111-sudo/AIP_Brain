"""RetrievalOrchestrator — unified retrieval dispatch, fusion, and budget curation.

The orchestrator:
1. Receives a RetrievalQuery
2. Expands the query using graph neighbors + template rules (Phase 5.3)
3. Dispatches to all enabled Retriever implementations
4. Dispatches expanded FTS queries (Phase 5.3)
5. Applies RRF fusion across channels
6. Applies importance weighting and budget curation
7. Produces a RetrievalTrace with full instrumentation

This is the SINGLE entry point for all retrieval in AIP. The ask_pipeline
delegates to this instead of calling stores directly.

Phase 5.1: Only FTSRetriever is wired. GraphRetriever, WikiRetriever,
and VectorRetriever get added by registering them with register_retriever().

Phase 5.3: Query expansion, WikiRetriever, configurable hub leash.

Layer: orchestration. Imports from foundation (schemas, protocols) and
from orchestration.retrievers (FTSRetriever, rrf_fuse, query_expansion).
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any

from aip.foundation.schemas.retrieval_trace import (
    RetrievalBudget,
    RetrievalChannel,
    RetrievalHit,
    RetrievalQuery,
    RetrievalTrace,
    RetrieverTrace,
)
from aip.foundation.protocols.retrieval import Retriever
from aip.orchestration.retrievers.rrf_fusion import rrf_fuse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Importance weighting
# ---------------------------------------------------------------------------


def apply_importance_weighting(
    hits: list[RetrievalHit],
    importance_weight: float = 0.15,
) -> list[RetrievalHit]:
    """Apply importance-based score adjustment to fused hits.

    After RRF fusion, we apply a lightweight importance boost so that
    Beast-tagged high-importance turns rank higher. This is NOT a
    replacement for the RRF score — it is a small additive adjustment.

    The adjustment is: score += importance * importance_weight
    This ensures that importance=0.9 hits get a +0.135 boost while
    importance=0.1 hits get only +0.015, preserving the fusion ranking
    while giving a meaningful signal.

    Args:
        hits: Post-fusion hits (may be re-ordered).
        importance_weight: Multiplier for importance adjustment.
            Default 0.15 keeps the adjustment modest.

    Returns:
        Re-sorted hit list (descending by adjusted score).
    """
    if not hits:
        return hits

    adjusted: list[RetrievalHit] = []
    for hit in hits:
        imp = hit.importance if hit.importance is not None else 0.0
        adjustment = imp * importance_weight
        new_score = hit.score + adjustment
        adjusted_hit = RetrievalHit(
            id=hit.id,
            source_type=hit.source_type,
            source_id=hit.source_id,
            title=hit.title,
            text=hit.text,
            snippet=hit.snippet,
            rank=hit.rank,
            score=round(new_score, 6),
            confidence=hit.confidence,
            recency_ts=hit.recency_ts,
            importance=hit.importance,
            domain=hit.domain,
            entities=hit.entities,
            retrieval_channel=hit.retrieval_channel,
            evidence_status=hit.evidence_status,
            debug={
                **hit.debug,
                "importance_adjustment": round(adjustment, 6),
            },
        )
        adjusted.append(adjusted_hit)

    # Re-sort by adjusted score and re-rank
    adjusted.sort(key=lambda h: h.score, reverse=True)
    for i, hit in enumerate(adjusted, start=1):
        hit.rank = i

    return adjusted


# ---------------------------------------------------------------------------
# Budget curation
# ---------------------------------------------------------------------------


def apply_budget_curation(
    hits: list[RetrievalHit],
    budget: RetrievalBudget,
) -> list[RetrievalHit]:
    """Apply budget constraints to the fused hit list.

    Curation rules:
    1. Cap at budget.max_sources
    2. Limit same-conversation repetitions (max_same_conversation)
    3. Limit same-domain concentration (max_same_domain_pct)
    4. Ensure diversity (not all hits from one source)

    These rules prevent the context from being dominated by a single
    conversation or domain, which is a real problem with FTS5-only
    retrieval (one long conversation can fill all top-K slots).

    Args:
        hits: Post-fusion, importance-weighted hit list (sorted desc).
        budget: Budget constraints.

    Returns:
        Curated hit list (may be shorter than input).
    """
    if not hits:
        return hits

    curated: list[RetrievalHit] = []
    conversation_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    excluded = 0

    for hit in hits:
        if len(curated) >= budget.max_sources:
            excluded += 1
            continue

        # Same-conversation cap
        conv_id = hit.debug.get("conversation_id", "")
        if conv_id:
            conv_count = conversation_counts.get(conv_id, 0)
            if conv_count >= budget.max_same_conversation:
                excluded += 1
                continue

        # Same-domain cap
        domain = hit.domain or ""
        if domain:
            dom_count = domain_counts.get(domain, 0)
            max_for_domain = int(budget.max_same_domain_pct * budget.max_sources)
            if max_for_domain > 0 and dom_count >= max_for_domain:
                excluded += 1
                continue

        # Accept
        curated.append(hit)
        if conv_id:
            conversation_counts[conv_id] = conversation_counts.get(conv_id, 0) + 1
        if domain:
            domain_counts[domain] = domain_counts.get(domain, 0) + 1

    # Re-rank
    for i, hit in enumerate(curated, start=1):
        hit.rank = i

    return curated


# ---------------------------------------------------------------------------
# RetrievalOrchestrator
# ---------------------------------------------------------------------------


class RetrievalOrchestrator:
    """Unified retrieval orchestrator — dispatch, fuse, curate, trace.

    This replaces the inline retrieval logic in ask_pipeline._search_sources
    with a clean, extensible architecture. New retrievers are registered
    via register_retriever() and participate automatically in RRF fusion.

    Phase 5.3 additions:
    - Query expansion: graph-based + template-based enrichment
    - Expanded FTS queries dispatched alongside original
    - graph_store optional for expansion and entity detection

    Graceful degradation (AIP-G-02):
    - If a retriever fails, it is skipped and the trace records the error.
    - The remaining retrievers' results are still fused.
    - If ALL retrievers fail, returns [] (never raises).

    Usage:
        orch = RetrievalOrchestrator()
        orch.register_retriever(fts_retriever)
        orch.graph_store = graph_store  # for query expansion
        hits = await orch.retrieve(query, budget=budget, trace=trace)
    """

    def __init__(self) -> None:
        self._retrievers: list[Retriever] = []
        self.graph_store: Any = None  # Optional: for query expansion
        self.enable_query_expansion: bool = True  # Toggle expansion on/off

    def register_retriever(self, retriever: Retriever) -> None:
        """Register a retriever. It will participate in future retrieve() calls."""
        self._retrievers.append(retriever)

    @property
    def retriever_names(self) -> list[str]:
        """Names of registered retrievers (for debug/display)."""
        return [r.name for r in self._retrievers]

    async def retrieve(
        self,
        query: RetrievalQuery,
        *,
        budget: RetrievalBudget | None = None,
        trace_id: str = "",
    ) -> tuple[list[RetrievalHit], RetrievalTrace]:
        """Execute retrieval across all registered retrievers.

        Args:
            query: The normalized retrieval query.
            budget: Token/count constraints. Defaults to RetrievalBudget().
            trace_id: Optional trace identifier for logging.

        Returns:
            Tuple of (curated hits, full trace).
        """
        if budget is None:
            budget = RetrievalBudget()

        trace = RetrievalTrace(
            query=query,
            trace_id=trace_id or f"ret:{uuid.uuid4().hex[:12]}",
            created_at=datetime.now(),
        )

        overall_start = time.monotonic()

        # --- Query expansion (Phase 5.3) ---
        expansion = None
        if self.enable_query_expansion:
            try:
                from aip.orchestration.retrievers.query_expansion import expand_query
                from aip.orchestration.retrievers.graph_retriever import detect_query_entities

                # Detect entities for expansion
                detected_entities = None
                if self.graph_store is not None:
                    detected_entities = detect_query_entities(
                        query.raw_query, self.graph_store
                    )
                    # Populate trace with detected entities
                    if detected_entities:
                        trace.detected_entities = [eid for eid, _ in detected_entities]
                        trace.entity_confidences = {eid: conf for eid, conf in detected_entities}

                expansion = expand_query(
                    query,
                    detected_entities=detected_entities,
                    graph_store=self.graph_store,
                )
                # Populate trace with expansion data
                if expansion and expansion.expanded_terms:
                    trace.query_expansions = expansion.expanded_terms
            except Exception as exc:
                logger.debug("Query expansion failed (non-fatal): %s", exc)

        # --- Dispatch to all retrievers ---
        hits_by_channel: dict[RetrievalChannel, list[RetrievalHit]] = {}

        for retriever in self._retrievers:
            try:
                retriever_hits = await retriever.retrieve(
                    query, budget=budget, trace=trace
                )
                if retriever_hits:
                    # Group by channel (a retriever may return mixed channels,
                    # but FTSRetriever returns all FTS)
                    channel = retriever_hits[0].retrieval_channel
                    if channel in hits_by_channel:
                        hits_by_channel[channel].extend(retriever_hits)
                    else:
                        hits_by_channel[channel] = retriever_hits
            except Exception as exc:
                logger.error("Retriever '%s' raised unexpectedly: %s", retriever.name, exc)
                # Record the failure in the trace
                error_trace = RetrieverTrace(
                    retriever_name=retriever.name,
                    enabled=True,
                    degraded=True,
                    error=f"Unexpected error: {exc}",
                )
                trace.retriever_traces.append(error_trace)
                trace.fallbacks_triggered.append(f"{retriever.name}_error")

        # --- Dispatch expanded FTS queries (Phase 5.3) ---
        # If query expansion produced additional FTS queries, run them
        # through FTSRetriever and merge results into the channel pool.
        if expansion and expansion.expanded_fts_queries:
            fts_retriever = None
            for r in self._retrievers:
                if r.name == "FTSRetriever":
                    fts_retriever = r
                    break

            if fts_retriever is not None:
                for exp_query_str in expansion.expanded_fts_queries:
                    try:
                        exp_query = RetrievalQuery(raw_query=exp_query_str)
                        exp_hits = await fts_retriever.retrieve(
                            exp_query, budget=budget, trace=trace
                        )
                        if exp_hits:
                            channel = RetrievalChannel.FTS
                            if channel in hits_by_channel:
                                hits_by_channel[channel].extend(exp_hits)
                            else:
                                hits_by_channel[channel] = exp_hits
                    except Exception as exc:
                        logger.debug(
                            "Expanded FTS query '%s' failed (non-fatal): %s",
                            exp_query_str, exc,
                        )

        # --- RRF Fusion ---
        if hits_by_channel:
            fused = rrf_fuse(hits_by_channel, k=60)
        else:
            fused = []

        # --- Importance weighting ---
        weighted = apply_importance_weighting(fused, importance_weight=0.15)

        # --- Budget curation ---
        curated = apply_budget_curation(weighted, budget)

        # --- Record fusion ranks in trace ---
        trace.fusion_ranks = [
            (h.id, h.rank, h.score) for h in curated[:25]
        ]

        # --- Record final selection in trace ---
        trace.final_selected_ids = [h.id for h in curated[:25]]
        trace.excluded_due_to_budget = len(fused) - len(curated)

        # --- Compute summary ---
        trace.compute_summary()

        elapsed = (time.monotonic() - overall_start) * 1000.0
        logger.debug(
            "RetrievalOrchestrator: %d hits from %d retrievers in %.0fms",
            len(curated),
            len(self._retrievers),
            elapsed,
        )

        return curated, trace


__all__ = [
    "RetrievalOrchestrator",
    "apply_importance_weighting",
    "apply_budget_curation",
]
