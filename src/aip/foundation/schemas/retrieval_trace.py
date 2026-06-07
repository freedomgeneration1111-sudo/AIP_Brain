"""Retrieval trace and golden test types for AIP retrieval architecture.

Phase 5.0 deliverable: trace instrumentation that captures WHY each result
appeared. Without this, we cannot measure whether GraphRetriever helps or
merely changes the flavor of failure.

These types are the contract between retrievers, the orchestrator, and
the eval framework. Every retriever must populate its trace fields.

Layer: foundation only. No adapter/orchestration imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Retrieval Hit — the unified shape for ALL retriever outputs
# ---------------------------------------------------------------------------


class EvidenceStatus(str, Enum):
    """Provenance classification for retrieval hits.

    AIP treats different evidence sources differently in scoring:
    - approved: verified knowledge (wiki, canon) — boost
    - raw: original source, unverified — neutral
    - model_output: derived content — slight downweight
    - rejected: discredited — exclude unless query asks history
    - superseded: replaced by newer version — downweight
    """

    APPROVED = "approved"
    RAW = "raw"
    MODEL_OUTPUT = "model_output"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class RetrievalChannel(str, Enum):
    """Which retrieval path produced this hit."""

    FTS = "fts"
    VECTOR = "vector"
    GRAPH = "graph"
    WIKI = "wiki"
    PROCEDURAL = "procedural"
    LEGACY = "legacy"  # pre-protocol _search_sources path


@dataclass
class RetrievalHit:
    """Unified retrieval hit — every retriever returns this shape.

    This is the contract that prevents retrieval from becoming a cancer
    of inconsistent paths. Fusion does not care whether the source came
    from FTS5, vector, graph, wiki, or procedural memory.
    """

    # Identity
    id: str  # unique hit identifier (turn_id, artifact_id, wiki_id, etc.)
    source_type: str  # corpus_turn / wiki_article / artifact / paper / email / procedure
    source_id: str  # reference to source record

    # Content
    title: str | None = None  # display title
    text: str = ""  # retrieved passage text (truncated to budget)
    snippet: str = ""  # short highlight for display (first 200 chars)

    # Ranking
    rank: int = 0  # rank within retriever (1-based)
    score: float = 0.0  # raw relevance score from retriever

    # Provenance
    confidence: float = 0.0  # source confidence (Beast tagging, extraction, etc.)
    recency_ts: datetime | None = None  # timestamp for freshness scoring
    importance: float | None = None  # Sexton importance weight
    domain: str | None = None  # project domain classification
    entities: list[str] = field(default_factory=list)  # entities mentioned in hit

    # Classification
    retrieval_channel: RetrievalChannel = RetrievalChannel.LEGACY
    evidence_status: EvidenceStatus = EvidenceStatus.RAW

    # Debug / trace
    debug: dict[str, Any] = field(default_factory=dict)
    # Retriever-specific trace data. Examples:
    #   FTS: {"match_columns": ["searchable_text"], "bm25_score": -3.2}
    #   Vector: {"distance": 0.45, "model": "nomic-embed-text"}
    #   Graph: {"seed_entity": "Komal", "ppr_score": 0.12, "zone": "A"}


# ---------------------------------------------------------------------------
# Retrieval Query — what goes INTO the retrieval system
# ---------------------------------------------------------------------------


@dataclass
class RetrievalQuery:
    """Normalized query passed to all retrievers.

    The RetrievalOrchestrator creates one RetrievalQuery and dispatches
    it to all enabled retrievers. Each retriever sees the same query.
    """

    raw_query: str  # original user query string
    normalized_query: str = ""  # lowered, stripped, de-duplicated
    domain_filter: str | None = None  # optional project domain constraint
    source_type_filter: str | None = None  # optional source type constraint
    intent_hint: str | None = None  # identity / current_status / procedure / history / catalog
    max_candidates: int = 40  # cap on total candidates across all retrievers

    def __post_init__(self):
        if not self.normalized_query:
            self.normalized_query = " ".join(self.raw_query.lower().split())


# ---------------------------------------------------------------------------
# Retrieval Budget — token allocation for context construction
# ---------------------------------------------------------------------------


@dataclass
class RetrievalBudget:
    """Token budget for retrieval context assembly.

    The budgeter decides what the model actually sees. Do not just raise
    max_sources to 30 and shove 30 chunks into the prompt. Every section
    has an allocation. GraphRAG should improve candidate recall; the
    budgeter decides what makes it into context.
    """

    total_tokens: int = 8000  # total context window for evidence
    wiki_allocation: float = 0.12  # 10-15% for approved wiki background
    evidence_allocation: float = 0.60  # 55-65% for retrieved evidence
    graph_debug_allocation: float = 0.03  # 0-5% for graph/debug (when active)
    chat_turns_allocation: float = 0.12  # 10-15% for recent chat turns
    # Remaining = answer reserve + system prompt

    # Per-source caps
    max_sources: int = 25  # max candidate hits to consider
    max_wiki_articles: int = 3  # max wiki articles to inject
    max_same_conversation: int = 3  # max turns from same conversation
    max_same_domain_pct: float = 0.40  # max % of hits from one domain

    # Diversity
    min_direct_seed_mentions: int = 3  # ensure seed entity mentions appear
    min_graph_expanded: int = 2  # ensure graph-expanded hits appear


# ---------------------------------------------------------------------------
# Per-retriever trace record
# ---------------------------------------------------------------------------


@dataclass
class RetrieverTrace:
    """Trace record from a single retriever invocation.

    Captures everything about one retriever's contribution to the
    final result. Essential for debugging why retrieval improved or
    regressed across build phases.
    """

    retriever_name: str  # "FTSRetriever", "VectorRetriever", "GraphRetriever", etc.
    enabled: bool = True  # was this retriever active?
    degraded: bool = False  # did it fall back to partial results?
    error: str | None = None  # error message if degraded/failed

    # Timing
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_ms: float = 0.0

    # Results
    hit_count: int = 0  # total hits returned
    top_score: float = 0.0  # highest score
    top_hit_ids: list[str] = field(default_factory=list)  # top-10 hit IDs
    scores: list[float] = field(default_factory=list)  # all scores (for distribution)

    # Channel-specific debug
    debug: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Full retrieval trace — the complete picture for one query
# ---------------------------------------------------------------------------


@dataclass
class RetrievalTrace:
    """Complete trace of a retrieval invocation across all retrievers.

    This is what gets persisted, logged, and evaluated. The golden test
    framework compares traces across builds to detect regressions.
    """

    # Query
    query: RetrievalQuery = field(default_factory=RetrievalQuery)
    trace_id: str = ""  # unique trace identifier

    # Entity detection (populated after entity seed selection)
    detected_entities: list[str] = field(default_factory=list)
    entity_confidences: dict[str, float] = field(default_factory=dict)

    # Query expansion (Phase 5.3)
    query_expansions: list[str] = field(default_factory=list)
    # Expanded search terms from graph neighbors + template rules

    # Per-retriever traces
    retriever_traces: list[RetrieverTrace] = field(default_factory=list)

    # Fusion results
    fusion_ranks: list[tuple[str, int, float]] = field(default_factory=list)
    # [(hit_id, rank, rrf_score)] post-RRF ranking

    # Context selection
    final_selected_ids: list[str] = field(default_factory=list)  # what enters the prompt
    final_selected_texts: list[str] = field(default_factory=list)  # corresponding texts
    budget_usage: dict[str, int] = field(default_factory=dict)
    # {"evidence_tokens": 3200, "wiki_tokens": 450, "chat_tokens": 600, ...}

    # Wiki injection
    wiki_injected: bool = False
    wiki_articles: list[str] = field(default_factory=list)  # article IDs injected

    # Graph-specific
    direct_mentions_count: int = 0  # entity-turn direct hits
    graph_expanded_entities: list[str] = field(default_factory=list)  # PPR-expanded

    # Budget exclusions
    excluded_due_to_budget: int = 0  # hits cut by budget constraint

    # Fallbacks
    fallbacks_triggered: list[str] = field(default_factory=list)
    # e.g. ["vector_store_unavailable", "graph_timeout"]

    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)

    # Summary metrics (computed after retrieval)
    total_hits: int = 0
    unique_domains: list[str] = field(default_factory=list)

    def compute_summary(self) -> None:
        """Compute derived metrics from trace data."""
        self.total_hits = sum(rt.hit_count for rt in self.retriever_traces)
        domains_seen: set[str] = set()
        for rt in self.retriever_traces:
            domains_seen.update(rt.debug.get("domains", []))
        self.unique_domains = sorted(domains_seen)


# ---------------------------------------------------------------------------
# Golden test types
# ---------------------------------------------------------------------------


@dataclass
class GoldenCluster:
    """A cluster of related content that must appear in retrieval results."""

    name: str  # e.g. "principal_role"
    keywords: list[str] = field(default_factory=list)  # keywords that identify this cluster
    domain: str | None = None  # expected domain


@dataclass
class GoldenTestResult:
    """Result of running one golden test against retrieval output."""

    test_name: str
    query: str
    total_hits: int
    hits_at_10: list[RetrievalHit]
    hits_at_25: list[RetrievalHit]
    hits_at_40: list[RetrievalHit]

    # Cluster recall
    cluster_hits: dict[str, list[RetrievalHit]] = field(default_factory=dict)
    # cluster_name → hits that match

    # Metrics
    recall_at_10: float = 0.0
    recall_at_25: float = 0.0
    recall_at_40: float = 0.0
    noise_top_10: float = 0.0  # fraction of top-10 not matching any cluster

    # Pass/fail
    passed: bool = False
    failures: list[str] = field(default_factory=list)

    # Full trace
    trace: RetrievalTrace | None = None


__all__ = [
    "EvidenceStatus",
    "RetrievalChannel",
    "RetrievalHit",
    "RetrievalQuery",
    "RetrievalBudget",
    "RetrieverTrace",
    "RetrievalTrace",
    "GoldenCluster",
    "GoldenTestResult",
]
