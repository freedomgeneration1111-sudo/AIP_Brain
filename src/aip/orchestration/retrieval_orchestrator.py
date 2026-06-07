"""RetrievalOrchestrator — multi-channel retrieval with parallel dispatch and RRF fusion.

Registers retriever channels as async callables, dispatches them concurrently
via ``asyncio.gather()``, fuses results with Reciprocal Rank Fusion (RRF),
and applies a quality gate.  Per-call ``OrchestratorConfig`` controls which
channels are active and budget allocation.

Architecture:
    1. Query expansion (synonym / entity / LLM).
    2. Concurrent dispatch via ``asyncio.gather()``.
    3. Per-channel budget enforcement before fusion.
    4. RRF merge.
    5. Quality gate (min RRF score + min hit count).
    6. Automatic retry on NEEDS_MORE_CONTEXT.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from aip.foundation.schemas.retrieval import RetrievalHit, RetrievalTrace

logger = logging.getLogger(__name__)

# Type alias: a retriever channel is an async callable(query) -> list[RetrievalHit]
RetrieverChannel = Callable[[str], Awaitable[list[RetrievalHit]]]


# ---------------------------------------------------------------------------
# RRF (Reciprocal Rank Fusion)
# ---------------------------------------------------------------------------

def rrf_fuse(
    channel_results: dict[str, list[RetrievalHit]],
    k: int = 60,
) -> list[RetrievalHit]:
    """Merge per-channel hit lists using Reciprocal Rank Fusion.

    Each hit's RRF contribution from a channel is ``1 / (k + rank)``
    where rank is 1-based.  Hits appearing in multiple channels accumulate
    scores.  The merged list is sorted by total RRF score descending.

    Args:
        channel_results: Mapping of channel name → ordered hit list.
        k: RRF constant (default 60 per the original RRF paper).

    Returns:
        Deduplicated, RRF-scored, descending list of RetrievalHit.
        Each hit's ``rrf_score`` is populated.
    """
    rrf_accum: dict[str, float] = {}
    hit_registry: dict[str, RetrievalHit] = {}

    for channel_name, hits in channel_results.items():
        for rank_idx, hit in enumerate(hits):
            rank = rank_idx + 1  # 1-based
            contribution = 1.0 / (k + rank)
            if hit.id not in rrf_accum:
                rrf_accum[hit.id] = 0.0
                hit_registry[hit.id] = hit
            rrf_accum[hit.id] += contribution
            # Keep the best metadata: if a hit appears in multiple channels,
            # preserve the highest raw score and merge channels into metadata.
            existing = hit_registry[hit.id]
            if hit.score > existing.score:
                existing.score = hit.score
            # Record all channels that contributed
            channels = existing.metadata.setdefault("source_channels", [])
            if channel_name not in channels:
                channels.append(channel_name)

    # Build fused list
    fused: list[RetrievalHit] = []
    for hit_id, total_score in rrf_accum.items():
        hit = hit_registry[hit_id]
        hit.rrf_score = total_score
        fused.append(hit)

    fused.sort(key=lambda h: h.rrf_score, reverse=True)
    return fused


# ---------------------------------------------------------------------------
# Quality gate
# ---------------------------------------------------------------------------

def apply_quality_gate(
    hits: list[RetrievalHit],
    min_rrf_score: float = 0.01,
    min_hits: int = 1,
) -> tuple[list[RetrievalHit], str]:
    """Apply quality gate to fused hits.

    Returns (filtered_hits, verdict) where verdict is one of:
      - ``"OK"`` — sufficient hits passed the gate.
      - ``"NEEDS_MORE_CONTEXT"`` — hits exist but are below quality threshold.
      - ``"NO_RESULTS"`` — no hits at all.

    Args:
        hits: Fused hit list (already sorted by rrf_score descending).
        min_rrf_score: Minimum RRF score to pass the gate.
        min_hits: Minimum number of hits required for OK verdict.
    """
    if not hits:
        return [], "NO_RESULTS"

    filtered = [h for h in hits if h.rrf_score >= min_rrf_score]
    if len(filtered) >= min_hits:
        return filtered, "OK"
    # Some hits exist but below threshold → trigger retry
    return filtered, "NEEDS_MORE_CONTEXT"


# ---------------------------------------------------------------------------
# Query expansion
# ---------------------------------------------------------------------------

def expand_query(query: str, extra_terms: list[str] | None = None) -> str:
    """Simple query expansion: append synonyms / entity terms.

    In production this would call an LLM or use a thesaurus; for now
    we just append any extra_terms provided and normalise whitespace.
    """
    if not extra_terms:
        return query
    expanded = query + " " + " ".join(extra_terms)
    return " ".join(expanded.split())  # normalise whitespace


# ---------------------------------------------------------------------------
# RetrievalOrchestrator
# ---------------------------------------------------------------------------

@dataclass
class OrchestratorConfig:
    """Per-call or default configuration for RetrievalOrchestrator.

    ``enable_*`` flags control which channels are dispatched.  ``extra_channels``
    enables additional registered channels not covered by built-in flags.
    ``enable_all_registered`` dispatches every registered channel regardless
    of per-channel flags.

    Per-channel budget: ``max_hits_per_channel`` and ``<channel>_max_hits``
    cap each channel's contribution before RRF fusion.  0 = no limit.
    """

    enable_fts: bool = True
    enable_vector: bool = True
    enable_graph: bool = False  # graph is opt-in (needs entity extraction)
    enable_wiki: bool = False  # wiki is opt-in (needs wiki store)
    enable_procedural: bool = False  # procedural is opt-in
    enable_corpus: bool = True  # corpus turn store channel
    extra_channels: list[str] = field(default_factory=list)  # additional channels to enable
    enable_all_registered: bool = False  # dispatch all registered channels
    max_retrieval_rounds: int = 2  # 0 = first attempt, 1 = first retry
    rrf_k: int = 60
    quality_gate_min_rrf: float = 0.01
    quality_gate_min_hits: int = 1
    max_hits: int = 50

    enable_llm_query_expansion: bool = False
    llm_query_expansion_timeout: float = 2.0
    llm_query_expansion_max_terms: int = 5

    # Per-channel budget: caps each channel's contribution to RRF fusion.
    # 0 = no limit (use global max_hits).  FTS/corpus are capped because
    # they tend to dominate RRF scores; Graph/Wiki have high precision
    # but low recall and need generous limits.
    max_hits_per_channel: int = 0
    fts_max_hits: int = 15
    vector_max_hits: int = 0
    graph_max_hits: int = 10
    wiki_max_hits: int = 8
    procedural_max_hits: int = 5
    corpus_max_hits: int = 15

    def get_channel_max_hits(self, channel_name: str) -> int:
        """Return the per-channel hit limit for a given channel.

        Resolution order:
        1. Channel-specific field (e.g. ``fts_max_hits``) if > 0.
        2. ``max_hits_per_channel`` (global default) if > 0.
        3. 0 (no limit — global ``max_hits`` applies after fusion).

        Args:
            channel_name: The channel name (e.g. "fts", "vector").

        Returns:
            Maximum hits this channel should contribute, or 0 for unlimited.
        """
        # Channel-specific overrides
        specific_map = {
            "fts": self.fts_max_hits,
            "vector": self.vector_max_hits,
            "graph": self.graph_max_hits,
            "wiki": self.wiki_max_hits,
            "procedural": self.procedural_max_hits,
            "corpus": self.corpus_max_hits,
        }
        specific = specific_map.get(channel_name, 0)
        if specific > 0:
            return specific
        if self.max_hits_per_channel > 0:
            return self.max_hits_per_channel
        return 0  # no limit


class RetrievalOrchestrator:
    """Multi-channel retrieval orchestrator with parallel dispatch.

    Retrievers are registered once (lazy registration) and shared across
    calls.  Per-call ``OrchestratorConfig`` controls which channels are
    active for that call.

    Usage::

        orch = RetrievalOrchestrator()
        orch.register_channel("fts", my_fts_retriever)
        orch.register_channel("vector", my_vec_retriever)

        hits, trace = await orch.retrieve("What is AIP?", config=OrchestratorConfig())
    """

    def __init__(self) -> None:
        self._channels: dict[str, RetrieverChannel] = {}
        self._registered: bool = False

    # -- Registration -------------------------------------------------------

    def register_channel(self, name: str, retriever: RetrieverChannel) -> None:
        """Register a retriever channel by name.

        Re-registering the same name replaces the previous callable.
        This is safe to call multiple times (idempotent per name).
        """
        self._channels[name] = retriever
        self._registered = True

    def is_registered(self, name: str) -> bool:
        """Check if a channel is registered."""
        return name in self._channels

    @property
    def channel_names(self) -> list[str]:
        """Names of all registered channels."""
        return list(self._channels.keys())

    # -- Retrieval ----------------------------------------------------------

    async def retrieve(
        self,
        query: str,
        config: OrchestratorConfig | None = None,
        session_id: str = "",
        expanded_terms: list[str] | None = None,
        model_provider: Any = None,
    ) -> tuple[list[RetrievalHit], RetrievalTrace]:
        """Execute a retrieval round with parallel dispatch and RRF fusion.

        This is the main entry point.  It:
        1. Optionally expands the query via LLM (if ``enable_llm_query_expansion``
           is True and a ``model_provider`` is supplied).
        2. Expands the query with any provided or LLM-generated terms.
        3. Determines which channels to dispatch.
        4. Runs enabled channels **concurrently** via ``asyncio.gather()``.
        5. Fuses results with RRF.
        6. Applies quality gate.
        7. Returns fused hits + trace.

        If the quality gate returns ``NEEDS_MORE_CONTEXT`` and
        ``max_retrieval_rounds > 0``, a retry round is automatically
        triggered with a broader query.

        Args:
            query: The user's original query.
            config: Per-call configuration.  Uses defaults if None.
            session_id: Correlation ID for tracing.
            expanded_terms: Extra terms for query expansion (retry rounds
                may add terms automatically).  If LLM query expansion is
                enabled, these are merged with LLM-generated terms.
            model_provider: ModelSlotResolver for LLM query expansion.
                Required only when ``enable_llm_query_expansion`` is True.

        Returns:
            Tuple of (fused_hits, trace).  The trace captures timing,
            hit counts, and the quality-gate verdict.
        """
        if config is None:
            config = OrchestratorConfig()

        # LLM query expansion (optional)
        llm_expanded_terms: list[str] = []
        if config.enable_llm_query_expansion and model_provider is not None:
            try:
                from aip.orchestration.llm_query_expansion import expand_query_with_llm
                expansion_result = await expand_query_with_llm(
                    query=query,
                    model_provider=model_provider,
                    timeout_seconds=config.llm_query_expansion_timeout,
                    max_terms=config.llm_query_expansion_max_terms,
                )
                if expansion_result.success:
                    llm_expanded_terms = expansion_result.expanded_terms
                    logger.info(
                        "llm_query_expansion_applied",
                        extra={
                            "session_id": session_id,
                            "original_query": query[:80],
                            "expanded_terms": llm_expanded_terms,
                            "elapsed_ms": round(expansion_result.elapsed_ms, 1),
                        },
                    )
            except Exception as exc:
                logger.warning(
                    "llm_query_expansion_error",
                    extra={"session_id": session_id, "error": str(exc)},
                )

        # Merge LLM-expanded terms with any provided expanded_terms
        all_expanded = list(expanded_terms or [])
        for term in llm_expanded_terms:
            if term not in all_expanded:
                all_expanded.append(term)

        all_hits: list[RetrievalHit] = []
        final_trace = RetrievalTrace(session_id=session_id, query=query)

        for round_num in range(config.max_retrieval_rounds):
            # Expand query for retry rounds
            current_query = (
                expand_query(query, all_expanded) if round_num == 0
                else expand_query(query, all_expanded)
            )
            if round_num > 0:
                # On retry, broaden: use the original query without FTS AND
                current_query = query  # retry with original (different channel mix)
                logger.info(
                    "retrieval_retry",
                    extra={"session_id": session_id, "round": round_num},
                )

            hits, trace = await self._execute_retrieval_round(
                query=current_query,
                config=config,
                session_id=session_id,
                round_number=round_num,
            )

            all_hits = hits
            final_trace = trace

            if trace.verdict == "OK":
                break
            if trace.verdict == "NO_RESULTS":
                break  # retry won't help if nothing matched
            # verdict == NEEDS_MORE_CONTEXT → retry

        return all_hits[: config.max_hits], final_trace

    async def _execute_retrieval_round(
        self,
        query: str,
        config: OrchestratorConfig,
        session_id: str = "",
        round_number: int = 0,
    ) -> tuple[list[RetrievalHit], RetrievalTrace]:
        """Execute one retrieval round: dispatch channels, fuse, quality-gate."""
        trace = RetrievalTrace(
            session_id=session_id,
            query=query,
            round_number=round_number,
        )

        # Determine which channels are enabled for this call
        channel_flags = {
            "fts": config.enable_fts,
            "vector": config.enable_vector,
            "graph": config.enable_graph,
            "wiki": config.enable_wiki,
            "procedural": config.enable_procedural,
            "corpus": config.enable_corpus,
        }
        # Add extra_channels
        for ch in config.extra_channels:
            channel_flags[ch] = True

        active_channels: dict[str, RetrieverChannel] = {}
        if config.enable_all_registered:
            # Dispatch ALL registered channels
            active_channels = dict(self._channels)
        else:
            for name, enabled in channel_flags.items():
                if enabled and name in self._channels:
                    active_channels[name] = self._channels[name]

        trace.channels_queried = list(active_channels.keys())

        if not active_channels:
            trace.verdict = "NO_RESULTS"
            return [], trace

        round_start = time.monotonic()

        async def _dispatch_one(
            name: str, retriever: RetrieverChannel, q: str
        ) -> tuple[str, list[RetrievalHit], float]:
            """Dispatch one channel and capture timing."""
            ch_start = time.monotonic()
            try:
                hits = await retriever(q)
            except Exception as exc:
                logger.warning("retriever_channel_failed", extra={"channel": name, "error": str(exc)})
                hits = []
            ch_elapsed = (time.monotonic() - ch_start) * 1000.0
            # Stamp per-hit timing and channel
            for i, hit in enumerate(hits):
                hit.source_channel = name
                hit.rank_in_channel = i + 1
                hit.elapsed_ms = ch_elapsed
            return name, hits, ch_elapsed

        # Launch all channels concurrently
        tasks = [
            _dispatch_one(name, retriever, query)
            for name, retriever in active_channels.items()
        ]
        results = await asyncio.gather(*tasks)

        # Collect results
        channel_results: dict[str, list[RetrievalHit]] = {}
        for name, hits, elapsed_ms in results:
            channel_results[name] = hits
            trace.per_channel_elapsed_ms[name] = elapsed_ms

        # Per-channel budget enforcement before fusion
        for ch_name in list(channel_results.keys()):
            ch_limit = config.get_channel_max_hits(ch_name)
            if ch_limit > 0 and len(channel_results[ch_name]) > ch_limit:
                channel_results[ch_name] = channel_results[ch_name][:ch_limit]

        trace.per_channel_hit_counts = {
            ch_name: len(hits) for ch_name, hits in channel_results.items()
        }

        trace.total_elapsed_ms = (time.monotonic() - round_start) * 1000.0
        trace.hits_before_fusion = sum(len(h) for h in channel_results.values())

        # -- RRF fusion -----------------------------------------------------
        fused = rrf_fuse(channel_results, k=config.rrf_k)
        trace.hits_after_fusion = len(fused)

        # -- Quality gate ---------------------------------------------------
        filtered, verdict = apply_quality_gate(
            fused,
            min_rrf_score=config.quality_gate_min_rrf,
            min_hits=config.quality_gate_min_hits,
        )
        trace.hits_after_quality_gate = len(filtered)
        trace.verdict = verdict

        # Channel contribution counts (post quality-gate)
        ch_contrib: dict[str, int] = {}
        for hit in filtered:
            # A hit may have come from multiple channels (dedup in RRF)
            channels_for_hit = hit.metadata.get("source_channels", [])
            if channels_for_hit:
                for ch in channels_for_hit:
                    ch_contrib[ch] = ch_contrib.get(ch, 0) + 1
            else:
                ch = hit.source_channel or "unknown"
                ch_contrib[ch] = ch_contrib.get(ch, 0) + 1
        trace.channel_contributions = ch_contrib

        # Extract graph-channel LLM entity extraction observability
        for hit in filtered:
            if hit.source_channel == "graph" and hit.metadata:
                llm_ms = hit.metadata.get("_llm_entity_extraction_ms")
                llm_status = hit.metadata.get("_llm_entity_extraction_status")
                llm_count = hit.metadata.get("_llm_entity_count")
                if llm_ms is not None:
                    trace.llm_entity_extraction_ms = float(llm_ms)
                if llm_status is not None:
                    trace.llm_entity_extraction_status = str(llm_status)
                if llm_count is not None:
                    trace.llm_entity_count = int(llm_count)
                break  # Only check the first graph hit

        return filtered, trace


# ---------------------------------------------------------------------------
# Orchestrator cache
# ---------------------------------------------------------------------------

class OrchestratorCache:
    """Singleton-ish cache for RetrievalOrchestrator instances.

    Avoids recreating the orchestrator and re-registering retrievers on
    every ``_search_sources_with_trace()`` call.  The cache key is a tuple
    of the store identities (id() of the backing stores), so a new
    orchestrator is created only when the underlying stores change.

    Supports per-call configuration toggles (``enable_*`` flags) by
    accepting an ``OrchestratorConfig`` at retrieval time rather than at
    registration time.
    """

    def __init__(self) -> None:
        self._orchestrator: RetrievalOrchestrator | None = None
        self._store_key: int = 0  # hash of store identities

    def get_or_create(
        self,
        store_key: int,
        register_fn: Callable[[RetrievalOrchestrator], None] | None = None,
    ) -> RetrievalOrchestrator:
        """Return a cached orchestrator, creating one if needed.

        Args:
            store_key: Hash/fingerprint of the store identities.
            register_fn: Called on a new orchestrator to register channels.
                Ignored if the cache hit succeeds.
        """
        if self._orchestrator is not None and self._store_key == store_key:
            return self._orchestrator

        orch = RetrievalOrchestrator()
        if register_fn is not None:
            register_fn(orch)
        self._orchestrator = orch
        self._store_key = store_key
        return orch

    def invalidate(self) -> None:
        """Force the cache to create a new orchestrator on next access."""
        self._orchestrator = None
        self._store_key = 0


# Module-level singleton
_orchestrator_cache = OrchestratorCache()


def get_orchestrator_cache() -> OrchestratorCache:
    """Return the module-level orchestrator cache singleton."""
    return _orchestrator_cache
