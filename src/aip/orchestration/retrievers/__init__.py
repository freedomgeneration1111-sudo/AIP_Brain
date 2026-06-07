"""Retriever backends for the unified retrieval architecture.

Phase 5.1: FTSRetriever, RRF fusion, RetrievalOrchestrator.
Phase 5.2: GraphRetriever (entity-turn index + PPR expansion).
Phase 5.3: Query expansion, WikiRetriever, configurable hub leash.

Each retriever implements the Retriever protocol from
foundation.protocols.retrieval and returns list[RetrievalHit].
"""

from __future__ import annotations

from aip.orchestration.retrievers.fts_retriever import FTSRetriever, sanitize_fts_query
from aip.orchestration.retrievers.graph_retriever import (
    GraphRetriever,
    detect_query_entities,
    apply_hub_leash,
)
from aip.orchestration.retrievers.orchestrator import (
    RetrievalOrchestrator,
    apply_budget_curation,
    apply_importance_weighting,
)
from aip.orchestration.retrievers.rrf_fusion import rrf_fuse
from aip.orchestration.retrievers.query_expansion import expand_query, QueryExpansion
from aip.orchestration.retrievers.wiki_retriever import WikiRetriever

__all__ = [
    "FTSRetriever",
    "sanitize_fts_query",
    "rrf_fuse",
    "RetrievalOrchestrator",
    "apply_importance_weighting",
    "apply_budget_curation",
    "GraphRetriever",
    "detect_query_entities",
    "apply_hub_leash",
    "expand_query",
    "QueryExpansion",
    "WikiRetriever",
]
