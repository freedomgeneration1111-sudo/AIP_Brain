"""RetrievalOrchestrator — unified retrieval dispatch, fusion, and budget curation.

The orchestrator:
1. Receives a RetrievalQuery
2. Expands the query using graph neighbors + template rules + LLM (Phase 5.4)
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

Phase 5.4: LLM query expansion, VectorRetriever, semantic wiki matching,
configurable toggles for all components, rich trace capture.

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
    ContextQualityStatus,
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
    """Unified retrieval orchestrator — dispatch, fuse, curate, trace, retry.

    This replaces the inline retrieval logic in ask_pipeline._search_sources
    with a clean, extensible architecture. New retrievers are registered
    via register_retriever() and participate automatically in RRF fusion.

    Phase 5.4 additions:
    - LLM query expansion via model_provider
    - VectorRetriever registration
    - Configurable toggles for all components
    - Rich trace capture (which retrievers ran, which expansions were used,
      which wiki articles were injected, budget usage breakdown)

    Phase 5.5 additions:
    - ProceduralRetriever registration and filtering
    - Answer Quality Gate integration
    - Procedural channel token tracking in budget usage

    Phase 5.6 additions:
    - Automatic retry when quality gate returns NEEDS_MORE_CONTEXT
    - Retry strategies: LLM expansion (if not used), relaxed domain filter,
      broader entity seeding, increased max_sources
    - Retry limited to 1 round to avoid latency explosion
    - Trace records retry information for observability

    Graceful degradation (AIP-G-02):
    - If a retriever fails, it is skipped and the trace records the error.
    - The remaining retrievers' results are still fused.
    - If ALL retrievers fail, returns [] (never raises).
    - If retry fails, the original results are kept.

    Usage:
        orch = RetrievalOrchestrator()
        orch.register_retriever(fts_retriever)
        orch.graph_store = graph_store  # for query expansion
        orch.model_provider = model_provider  # for LLM expansion
        hits = await orch.retrieve(query, budget=budget, trace=trace)
    """

    def __init__(self) -> None:
        self._retrievers: list[Retriever] = []
        self.graph_store: Any = None  # Optional: for query expansion + graph retrieval
        self.model_provider: Any = None  # Optional: for LLM query expansion (Phase 5.4)

        # Configuration toggles (Phase 5.4 → 5.5)
        self.enable_query_expansion: bool = True
        self.enable_llm_expansion: bool = True
        self.enable_wiki_injection: bool = True
        self.enable_vector_retrieval: bool = True
        self.enable_procedural_retrieval: bool = True

        # Answer Quality Gate (Phase 5.5)
        self.quality_gate: Any = None  # Optional: AnswerQualityGate instance

        # Automatic Retry (Phase 5.6)
        self.enable_auto_retry: bool = True  # Enable retry on NEEDS_MORE_CONTEXT
        self.max_retries: int = 1  # Maximum retry rounds (1 to avoid latency explosion)

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

        # --- Query expansion (Phase 5.3 → 5.4: now async with LLM) ---
        expansion = None
        if self.enable_query_expansion:
            try:
                from aip.orchestration.retrievers.query_expansion import expand_query_async
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

                expansion = await expand_query_async(
                    query,
                    detected_entities=detected_entities,
                    graph_store=self.graph_store,
                    model_provider=self.model_provider if self.enable_llm_expansion else None,
                    enable_llm=self.enable_llm_expansion and self.model_provider is not None,
                )
                # Populate trace with expansion data
                if expansion and expansion.expanded_terms:
                    trace.query_expansions = expansion.expanded_terms
            except Exception as exc:
                logger.debug("Query expansion failed (non-fatal): %s", exc)

        # --- Determine which retrievers to run ---
        active_retrievers = self._filter_retrievers()

        # --- Dispatch to all retrievers ---
        hits_by_channel: dict[RetrievalChannel, list[RetrievalHit]] = {}

        for retriever in active_retrievers:
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

        # --- Compute budget usage (Phase 5.4 → 5.5: added procedural channel) ---
        trace.budget_usage = self._compute_budget_usage(curated, budget)

        # --- Answer Quality Gate (Phase 5.5 → 5.6: now with auto-retry) ---
        quality_result = None
        if self.quality_gate is not None:
            try:
                quality_result = self.quality_gate.evaluate(curated, trace, budget)
                # Update trace with quality gate results
                trace.context_quality_status = quality_result.status.value
                trace.context_quality_scores = quality_result.scores
                trace.quality_gate_elapsed_ms = quality_result.elapsed_ms
            except Exception as exc:
                logger.debug("Quality gate failed (non-fatal): %s", exc)

        # --- Automatic Retry (Phase 5.6) ---
        if (
            self.enable_auto_retry
            and quality_result is not None
            and quality_result.status == ContextQualityStatus.NEEDS_MORE_CONTEXT
            and self.max_retries > 0
        ):
            curated, trace = await self._retry_retrieval(
                query=query,
                budget=budget,
                trace=trace,
                curated=curated,
                quality_result=quality_result,
                expansion=expansion,
                overall_start=overall_start,
            )

        # --- Record expansion metadata in trace (Phase 5.4) ---
        if expansion:
            if expansion.llm_expansion_used:
                trace.fallbacks_triggered.append("llm_expansion_used")
            # Store expansion source info in trace debug
            for rt in trace.retriever_traces:
                if rt.retriever_name == "RetrievalOrchestrator":
                    rt.debug["expansion_source"] = expansion.source
                    rt.debug["llm_expansion_used"] = expansion.llm_expansion_used
                    rt.debug["llm_latency_ms"] = expansion.llm_latency_ms
                    break

        # --- Compute summary ---
        trace.compute_summary()

        elapsed = (time.monotonic() - overall_start) * 1000.0
        logger.debug(
            "RetrievalOrchestrator: %d hits from %d retrievers in %.0fms (retry=%s)",
            len(curated),
            len(active_retrievers),
            elapsed,
            trace.retry_triggered,
        )

        return curated, trace

    async def _retry_retrieval(
        self,
        query: RetrievalQuery,
        budget: RetrievalBudget,
        trace: RetrievalTrace,
        curated: list[RetrievalHit],
        quality_result: Any,
        expansion: Any,
        overall_start: float,
    ) -> tuple[list[RetrievalHit], RetrievalTrace]:
        """Execute a retry round when quality gate returns NEEDS_MORE_CONTEXT.

        Strategies tried (in order of priority):
        1. LLM query expansion (if not already used in the first round)
        2. Relaxed domain filter (search without domain constraints)
        3. Broader entity seeding (include more entities from graph)
        4. Increased max_sources (allow more hits through)

        The retry is limited to one round. If the retry produces better
        results, they replace the originals. If not, we keep the originals
        but record that the retry didn't help.

        Args:
            query: Original retrieval query.
            budget: Token budget (may be increased for retry).
            trace: Current trace (will be updated with retry info).
            curated: Current curated hit list.
            quality_result: Quality gate result from first round.
            expansion: Query expansion result from first round.
            overall_start: Start time for the overall retrieval.

        Returns:
            Tuple of (potentially updated curated hits, updated trace).
        """
        # Save original state for comparison
        first_status = quality_result.status.value
        first_scores = dict(quality_result.scores)
        first_hit_count = len(curated)
        strategies_tried: list[str] = []

        # Determine retry reason from quality gate scores
        retry_reason = "needs_more_context"
        if first_scores.get("entity_coverage", 0) < 0.3:
            retry_reason = "low_entity_coverage"
        elif first_scores.get("evidence_tokens", 0) < 200:
            retry_reason = "insufficient_evidence"
        elif first_scores.get("top_hit_score", 0) < 0.15:
            retry_reason = "low_relevance"

        logger.info(
            "RetrievalOrchestrator: retry triggered (reason=%s, status=%s, hits=%d)",
            retry_reason, first_status, first_hit_count,
        )

        # --- Strategy 1: Force LLM expansion if not already used ---
        llm_already_used = (
            expansion is not None
            and hasattr(expansion, "llm_expansion_used")
            and expansion.llm_expansion_used
        )
        if not llm_already_used and self.model_provider is not None:
            strategies_tried.append("llm_expansion")
            self.enable_llm_expansion = True  # Force on for retry

        # --- Strategy 2: Relaxed domain filter ---
        if query.domain_filter is not None:
            strategies_tried.append("relaxed_domain")
            query.domain_filter = None  # Remove domain constraint

        # --- Strategy 3: Broader entity seeding ---
        if self.graph_store is not None and trace.detected_entities:
            strategies_tried.append("broader_entity_seeding")
            # Try to get more entities from the graph
            try:
                from aip.orchestration.retrievers.graph_retriever import detect_query_entities
                additional_entities = detect_query_entities(
                    query.raw_query, self.graph_store, max_entities=20,
                )
                if additional_entities:
                    new_entity_ids = [eid for eid, _ in additional_entities]
                    # Merge with existing entities (dedup)
                    existing = set(trace.detected_entities)
                    for eid in new_entity_ids:
                        if eid not in existing:
                            trace.detected_entities.append(eid)
                            existing.add(eid)
            except Exception as exc:
                logger.debug("Broader entity seeding failed (non-fatal): %s", exc)

        # --- Strategy 4: Increase max_sources ---
        original_max = budget.max_sources
        if first_hit_count < budget.max_sources // 2:
            strategies_tried.append("increased_sources")
            budget.max_sources = min(budget.max_sources * 2, 50)

        # --- Re-run the full retrieval pipeline with retry settings ---
        try:
            retry_curated, _ = await self._execute_retrieval_round(
                query=query,
                budget=budget,
                trace=trace,
            )

            # Evaluate retry quality
            retry_quality_result = None
            if self.quality_gate is not None and retry_curated:
                try:
                    # Create a temporary trace for quality evaluation
                    retry_quality_result = self.quality_gate.evaluate(
                        retry_curated, trace, budget,
                    )
                except Exception:
                    pass

            # Determine if retry improved things
            retry_improved = False
            if retry_quality_result is not None:
                # Compare quality: any improvement from NEEDS_MORE_CONTEXT counts
                retry_status = retry_quality_result.status
                if retry_status in (
                    ContextQualityStatus.SUFFICIENT,
                    ContextQualityStatus.MARGINAL,
                ):
                    retry_improved = True
                elif retry_status == ContextQualityStatus.NEEDS_MORE_CONTEXT:
                    # Even if still insufficient, check if scores improved
                    orig_quality = first_scores.get("overall_quality", 0)
                    retry_quality = retry_quality_result.scores.get("overall_quality", 0)
                    if retry_quality > orig_quality and len(retry_curated) > first_hit_count:
                        retry_improved = True

            if retry_improved and len(retry_curated) >= first_hit_count:
                # Use retry results — they're better
                curated = retry_curated
                # Update trace with retry quality
                if retry_quality_result:
                    trace.context_quality_status = retry_quality_result.status.value
                    trace.context_quality_scores = retry_quality_result.scores
                trace.budget_usage = self._compute_budget_usage(curated, budget)
                trace.final_selected_ids = [h.id for h in curated[:25]]
                trace.fusion_ranks = [(h.id, h.rank, h.score) for h in curated[:25]]

            # Record retry information in trace
            trace.retry_triggered = True
            trace.retry_reason = retry_reason
            trace.retry_round = 1
            trace.retry_strategies_tried = strategies_tried
            trace.retry_quality_improved = retry_improved
            trace.retry_first_status = first_status
            trace.retry_first_scores = first_scores

        except Exception as exc:
            logger.warning("Retry retrieval round failed (non-fatal): %s", exc)
            # Keep original results, still record the retry attempt
            trace.retry_triggered = True
            trace.retry_reason = retry_reason
            trace.retry_round = 1
            trace.retry_strategies_tried = strategies_tried
            trace.retry_quality_improved = False
            trace.retry_first_status = first_status
            trace.retry_first_scores = first_scores
            trace.fallbacks_triggered.append("retry_failed")
        finally:
            # Restore original settings
            budget.max_sources = original_max
            # Don't restore enable_llm_expansion — it was a force-on for retry

        return curated, trace

    async def _execute_retrieval_round(
        self,
        query: RetrievalQuery,
        budget: RetrievalBudget,
        trace: RetrievalTrace,
    ) -> tuple[list[RetrievalHit], RetrievalTrace]:
        """Execute a single retrieval round (dispatch, fuse, curate).

        This is the core retrieval pipeline, extracted so that both
        the initial retrieval and the retry can use the same logic.

        Returns:
            Tuple of (curated hits, trace with retriever traces appended).
        """
        # --- Query expansion ---
        expansion = None
        if self.enable_query_expansion:
            try:
                from aip.orchestration.retrievers.query_expansion import expand_query_async
                from aip.orchestration.retrievers.graph_retriever import detect_query_entities

                detected_entities = None
                if self.graph_store is not None:
                    detected_entities = detect_query_entities(
                        query.raw_query, self.graph_store,
                    )

                expansion = await expand_query_async(
                    query,
                    detected_entities=detected_entities,
                    graph_store=self.graph_store,
                    model_provider=self.model_provider if self.enable_llm_expansion else None,
                    enable_llm=self.enable_llm_expansion and self.model_provider is not None,
                )
                if expansion and expansion.expanded_terms:
                    trace.query_expansions = expansion.expanded_terms
            except Exception as exc:
                logger.debug("Query expansion failed (non-fatal): %s", exc)

        # --- Dispatch to retrievers ---
        active_retrievers = self._filter_retrievers()
        hits_by_channel: dict[RetrievalChannel, list[RetrievalHit]] = {}

        for retriever in active_retrievers:
            try:
                retriever_hits = await retriever.retrieve(
                    query, budget=budget, trace=trace
                )
                if retriever_hits:
                    channel = retriever_hits[0].retrieval_channel
                    if channel in hits_by_channel:
                        hits_by_channel[channel].extend(retriever_hits)
                    else:
                        hits_by_channel[channel] = retriever_hits
            except Exception as exc:
                logger.error("Retriever '%s' raised: %s", retriever.name, exc)

        # --- Dispatch expanded FTS queries ---
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
                    except Exception:
                        pass

        # --- RRF Fusion ---
        fused = rrf_fuse(hits_by_channel, k=60) if hits_by_channel else []

        # --- Importance weighting ---
        weighted = apply_importance_weighting(fused, importance_weight=0.15)

        # --- Budget curation ---
        curated = apply_budget_curation(weighted, budget)

        # --- Record fusion ranks ---
        trace.fusion_ranks = [(h.id, h.rank, h.score) for h in curated[:25]]
        trace.final_selected_ids = [h.id for h in curated[:25]]
        trace.excluded_due_to_budget = len(fused) - len(curated)

        return curated, trace

    def _filter_retrievers(self) -> list[Retriever]:
        """Filter retrievers based on configuration toggles.

        Allows disabling specific retriever types at runtime without
        unregistering them. This is useful for A/B testing, debugging,
        and configuration-driven behavior.
        """
        active: list[Retriever] = []
        for r in self._retrievers:
            name = r.name
            if name == "VectorRetriever" and not self.enable_vector_retrieval:
                continue
            if name == "WikiRetriever" and not self.enable_wiki_injection:
                continue
            if name == "ProceduralRetriever" and not self.enable_procedural_retrieval:
                continue
            active.append(r)
        return active

    def _compute_budget_usage(
        self,
        curated: list[RetrievalHit],
        budget: RetrievalBudget,
    ) -> dict[str, int]:
        """Compute token budget usage breakdown for the trace.

        This gives visibility into how the budget was allocated across
        channels: how much went to evidence, wiki, etc.
        """
        usage: dict[str, int] = {}

        # Rough token estimation: ~4 chars per token
        evidence_tokens = 0
        wiki_tokens = 0
        graph_tokens = 0
        vector_tokens = 0
        procedural_tokens = 0

        for hit in curated:
            char_count = len(hit.text) if hit.text else 0
            est_tokens = char_count // 4

            if hit.retrieval_channel == RetrievalChannel.WIKI:
                wiki_tokens += est_tokens
            elif hit.retrieval_channel == RetrievalChannel.GRAPH:
                graph_tokens += est_tokens
            elif hit.retrieval_channel == RetrievalChannel.VECTOR:
                vector_tokens += est_tokens
            elif hit.retrieval_channel == RetrievalChannel.PROCEDURAL:
                procedural_tokens += est_tokens
            else:
                evidence_tokens += est_tokens

        usage["evidence_tokens"] = evidence_tokens
        usage["wiki_tokens"] = wiki_tokens
        usage["graph_tokens"] = graph_tokens
        usage["vector_tokens"] = vector_tokens
        usage["procedural_tokens"] = procedural_tokens
        usage["total_estimated_tokens"] = evidence_tokens + wiki_tokens + graph_tokens + vector_tokens + procedural_tokens
        usage["budget_total_tokens"] = budget.total_tokens
        usage["hit_count"] = len(curated)

        return usage


__all__ = [
    "RetrievalOrchestrator",
    "apply_importance_weighting",
    "apply_budget_curation",
]
