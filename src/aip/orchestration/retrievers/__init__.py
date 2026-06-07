"""Retriever backends for the unified retrieval architecture.

Phase 5.1: FTSRetriever, RRF fusion, RetrievalOrchestrator.
Phase 5.2: GraphRetriever (entity-turn index + PPR expansion).
Phase 5.3: Query expansion, WikiRetriever, configurable hub leash.
Phase 5.4: VectorRetriever, LLM query expansion, semantic wiki matching.
Phase 5.5: ProceduralRetriever, Smart Context Packer, Answer Quality Gate,
    Trace persistence and quality metrics.
Phase 5.6: Auto-retry on NEEDS_MORE_CONTEXT, extractive summarization,
    Trace dashboard analytics, model-assisted quality gate.

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
from aip.orchestration.retrievers.query_expansion import expand_query, expand_query_async, QueryExpansion
from aip.orchestration.retrievers.wiki_retriever import WikiRetriever
from aip.orchestration.retrievers.vector_retriever import VectorRetriever
from aip.orchestration.retrievers.procedural_retriever import ProceduralRetriever, is_procedural_query
from aip.orchestration.retrievers.context_packer import (
    SmartContextPacker,
    ContextPacket,
    ContextSection,
    assemble_context,
    extractive_summarize,
)
from aip.orchestration.retrievers.answer_quality_gate import (
    AnswerQualityGate,
    QualityGateConfig,
    QualityGateResult,
)
from aip.orchestration.retrievers.trace_store import (
    TraceStore,
    compute_retrieval_metrics,
)

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
    "expand_query_async",
    "QueryExpansion",
    "WikiRetriever",
    "VectorRetriever",
    "ProceduralRetriever",
    "is_procedural_query",
    "SmartContextPacker",
    "ContextPacket",
    "ContextSection",
    "assemble_context",
    "extractive_summarize",
    "AnswerQualityGate",
    "QualityGateConfig",
    "QualityGateResult",
    "TraceStore",
    "compute_retrieval_metrics",
]
