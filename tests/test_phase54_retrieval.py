"""Tests for Phase 5.4 deliverables — VectorRetriever, LLM expansion, semantic wiki.

Covers:
- VectorRetriever (protocol compliance, graceful degradation, score normalization)
- LLM query expansion (expand_query_async, _llm_expand, fallback behavior)
- Semantic wiki matching (WikiRetriever with embedding_provider)
- Trace polish (budget_usage, expansion metadata, retriever filtering)
- Orchestrator configuration toggles
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any

import pytest

from aip.foundation.protocols.retrieval import Retriever
from aip.foundation.schemas.retrieval_trace import (
    EvidenceStatus,
    RetrievalBudget,
    RetrievalChannel,
    RetrievalHit,
    RetrievalQuery,
    RetrievalTrace,
    RetrieverTrace,
)
from aip.orchestration.retrievers.vector_retriever import VectorRetriever
from aip.orchestration.retrievers.query_expansion import (
    QueryExpansion,
    expand_query,
    expand_query_async,
    _template_expand,
    _graph_expand,
    _llm_expand,
)
from aip.orchestration.retrievers.wiki_retriever import WikiRetriever, _cosine_similarity
from aip.orchestration.retrievers.orchestrator import (
    RetrievalOrchestrator,
    apply_importance_weighting,
    apply_budget_curation,
)
from aip.foundation.schemas.retrieval import Chunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hit(
    hit_id: str = "h1",
    score: float = 0.9,
    rank: int = 1,
    channel: RetrievalChannel = RetrievalChannel.FTS,
    importance: float | None = None,
    domain: str | None = None,
    source_type: str = "corpus_turn",
) -> RetrievalHit:
    """Create a test RetrievalHit."""
    return RetrievalHit(
        id=hit_id,
        source_type=source_type,
        source_id=hit_id,
        text=f"Content for {hit_id}",
        snippet=f"Snippet for {hit_id}",
        rank=rank,
        score=score,
        importance=importance,
        domain=domain,
        retrieval_channel=channel,
        evidence_status=EvidenceStatus.RAW,
        debug={},
    )


# ---------------------------------------------------------------------------
# VectorRetriever
# ---------------------------------------------------------------------------


class TestVectorRetriever:
    def test_satisfies_retriever_protocol(self):
        """VectorRetriever should satisfy the Retriever protocol."""
        vr = VectorRetriever()
        assert isinstance(vr, Retriever)

    def test_name(self):
        vr = VectorRetriever()
        assert vr.name == "VectorRetriever"

    @pytest.mark.asyncio
    async def test_no_stores_returns_empty(self):
        """Without vector_store or embedding_provider, returns [] gracefully."""
        vr = VectorRetriever()
        query = RetrievalQuery(raw_query="test")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)

        hits = await vr.retrieve(query, budget=budget, trace=trace)
        assert hits == []
        # Should still record a retriever trace
        assert len(trace.retriever_traces) == 1
        assert trace.retriever_traces[0].retriever_name == "VectorRetriever"
        assert not trace.retriever_traces[0].enabled  # disabled due to no providers

    @pytest.mark.asyncio
    async def test_no_vector_store_returns_empty(self):
        """Without vector_store, returns [] gracefully."""
        class MockEmbedding:
            async def embed(self, text: str) -> list[float]:
                return [0.1, 0.2, 0.3]

        vr = VectorRetriever(embedding_provider=MockEmbedding())
        query = RetrievalQuery(raw_query="test")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)

        hits = await vr.retrieve(query, budget=budget, trace=trace)
        assert hits == []

    @pytest.mark.asyncio
    async def test_no_embedding_provider_returns_empty(self):
        """Without embedding_provider, returns [] gracefully."""
        class MockVectorStore:
            async def retrieve(self, query_vector, domain=None, top_k=10):
                return []

        vr = VectorRetriever(vector_store=MockVectorStore())
        query = RetrievalQuery(raw_query="test")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)

        hits = await vr.retrieve(query, budget=budget, trace=trace)
        assert hits == []

    @pytest.mark.asyncio
    async def test_embedding_failure_graceful(self):
        """If embed() raises, returns [] and records error."""
        class FailingEmbedding:
            async def embed(self, text: str) -> list[float]:
                raise RuntimeError("Embedding service unavailable")

        class MockVectorStore:
            async def retrieve(self, query_vector, domain=None, top_k=10):
                return []

        vr = VectorRetriever(
            vector_store=MockVectorStore(),
            embedding_provider=FailingEmbedding(),
        )
        query = RetrievalQuery(raw_query="test")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)

        hits = await vr.retrieve(query, budget=budget, trace=trace)
        assert hits == []
        assert "vector_embed_failed" in trace.fallbacks_triggered

    @pytest.mark.asyncio
    async def test_successful_retrieval(self):
        """With working stores, returns hits with VECTOR channel."""
        class MockEmbedding:
            async def embed(self, text: str) -> list[float]:
                return [0.1, 0.2, 0.3]

        class MockVectorStore:
            async def retrieve(self, query_vector, domain=None, top_k=10):
                return [
                    Chunk(id="vec1", content="Vector content 1", score=0.95,
                           metadata={"type": "conversation_chunk", "conversation_id": "c1"}, domain="test"),
                    Chunk(id="vec2", content="Vector content 2", score=0.80,
                           metadata={"type": "corpus_turn"}, domain="test2"),
                ]

        vr = VectorRetriever(
            vector_store=MockVectorStore(),
            embedding_provider=MockEmbedding(),
        )
        query = RetrievalQuery(raw_query="test query")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)

        hits = await vr.retrieve(query, budget=budget, trace=trace)
        assert len(hits) == 2
        assert all(h.retrieval_channel == RetrievalChannel.VECTOR for h in hits)
        assert hits[0].id == "vec1"
        assert hits[0].score > hits[1].score  # Higher raw score → higher normalized
        # Trace should be populated
        assert len(trace.retriever_traces) == 1
        assert trace.retriever_traces[0].hit_count == 2

    @pytest.mark.asyncio
    async def test_domain_filter_passed(self):
        """Domain filter from query should be passed to VectorStore."""
        captured_domain = None

        class MockEmbedding:
            async def embed(self, text: str) -> list[float]:
                return [0.1, 0.2, 0.3]

        class MockVectorStore:
            async def retrieve(self, query_vector, domain=None, top_k=10):
                nonlocal captured_domain
                captured_domain = domain
                return []

        vr = VectorRetriever(
            vector_store=MockVectorStore(),
            embedding_provider=MockEmbedding(),
        )
        query = RetrievalQuery(raw_query="test", domain_filter="my_project")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)

        await vr.retrieve(query, budget=budget, trace=trace)
        assert captured_domain == "my_project"

    def test_score_normalization_minmax(self):
        """Min-max normalization should map scores to 0.3-1.0 range."""
        vr = VectorRetriever(score_normalization="minmax")
        raw = [0.1, 0.5, 0.9]
        normalized = vr._normalize_scores(raw)
        assert len(normalized) == 3
        assert normalized[0] == pytest.approx(0.3, abs=0.01)  # min → 0.3
        assert normalized[2] == pytest.approx(1.0, abs=0.01)  # max → 1.0
        assert normalized[1] > normalized[0]  # monotonic

    def test_score_normalization_single_value(self):
        """Single score should get default 0.8."""
        vr = VectorRetriever(score_normalization="minmax")
        normalized = vr._normalize_scores([0.5])
        assert normalized == [0.8]

    def test_score_normalization_identical(self):
        """Identical scores should all get 0.8."""
        vr = VectorRetriever(score_normalization="minmax")
        normalized = vr._normalize_scores([0.5, 0.5, 0.5])
        assert normalized == [0.8, 0.8, 0.8]

    def test_score_normalization_none(self):
        """'none' normalization passes through raw scores."""
        vr = VectorRetriever(score_normalization="none")
        raw = [0.1, 0.5, 0.9]
        normalized = vr._normalize_scores(raw)
        assert normalized == raw

    def test_score_normalization_empty(self):
        """Empty input returns empty."""
        vr = VectorRetriever()
        assert vr._normalize_scores([]) == []

    @pytest.mark.asyncio
    async def test_budget_cap_applied(self):
        """Budget max_sources should cap the number of hits."""
        class MockEmbedding:
            async def embed(self, text: str) -> list[float]:
                return [0.1, 0.2, 0.3]

        class MockVectorStore:
            async def retrieve(self, query_vector, domain=None, top_k=10):
                return [
                    Chunk(id=f"v{i}", content=f"Content {i}", score=0.9 - i * 0.1)
                    for i in range(20)
                ]

        vr = VectorRetriever(
            vector_store=MockVectorStore(),
            embedding_provider=MockEmbedding(),
        )
        query = RetrievalQuery(raw_query="test")
        budget = RetrievalBudget(max_sources=5)
        trace = RetrievalTrace(query=query)

        hits = await vr.retrieve(query, budget=budget, trace=trace)
        assert len(hits) <= 5


# ---------------------------------------------------------------------------
# LLM Query Expansion
# ---------------------------------------------------------------------------


class TestTemplateExpand:
    def test_who_is_pattern(self):
        terms = _template_expand("Who is Komal?")
        assert "Komal" in terms
        assert any("role" in t.lower() for t in terms)

    def test_what_is_pattern(self):
        terms = _template_expand("What is AIP?")
        assert "AIP" in terms

    def test_no_match(self):
        terms = _template_expand("Random query without pattern")
        assert terms == []


class TestGraphExpand:
    def test_no_entities(self):
        assert _graph_expand([], None) == []

    def test_no_graph_store(self):
        assert _graph_expand(["e1"], None) == []


class TestLLMExpand:
    @pytest.mark.asyncio
    async def test_no_model_provider(self):
        terms, latency = await _llm_expand("test query", model_provider=None)
        assert terms == []
        assert latency == 0.0

    @pytest.mark.asyncio
    async def test_empty_query(self):
        class MockModel:
            async def call(self, slot, messages, **kwargs):
                return {"content": '["expanded term"]'}

        terms, latency = await _llm_expand("", model_provider=MockModel())
        assert terms == []

    @pytest.mark.asyncio
    async def test_successful_expansion(self):
        """LLM returns valid JSON array → terms extracted."""
        class MockModel:
            async def call(self, slot, messages, **kwargs):
                return {"content": '["Komal principal role", "Freedom Generation School", "Urdu teacher"]'}

        terms, latency = await _llm_expand("Who is Komal?", model_provider=MockModel())
        assert len(terms) == 3
        assert "Komal principal role" in terms
        assert latency > 0

    @pytest.mark.asyncio
    async def test_model_error_fallback(self):
        """Model returns error → empty terms, no crash."""
        class ErrorModel:
            async def call(self, slot, messages, **kwargs):
                return {"error": True, "error_message": "Service unavailable"}

        terms, latency = await _llm_expand("test", model_provider=ErrorModel())
        assert terms == []

    @pytest.mark.asyncio
    async def test_model_exception_fallback(self):
        """Model raises exception → empty terms, no crash."""
        class CrashModel:
            async def call(self, slot, messages, **kwargs):
                raise RuntimeError("Connection lost")

        terms, latency = await _llm_expand("test", model_provider=CrashModel())
        assert terms == []
        assert latency > 0  # Still measures elapsed time

    @pytest.mark.asyncio
    async def test_json_in_markdown(self):
        """LLM returns JSON wrapped in markdown → still parsed."""
        class MarkdownModel:
            async def call(self, slot, messages, **kwargs):
                return {"content": '```json\n["term1", "term2"]\n```'}

        terms, latency = await _llm_expand("test", model_provider=MarkdownModel())
        assert len(terms) == 2

    @pytest.mark.asyncio
    async def test_filters_original_query(self):
        """Expansions identical to the original query are filtered out."""
        class EchoModel:
            async def call(self, slot, messages, **kwargs):
                return {"content": '["test query", "different expansion"]'}

        terms, latency = await _llm_expand("test query", model_provider=EchoModel())
        assert "test query" not in terms
        assert "different expansion" in terms

    @pytest.mark.asyncio
    async def test_max_expansions_cap(self):
        """Results capped at max_expansions."""
        class VerboseModel:
            async def call(self, slot, messages, **kwargs):
                return {"content": '["a", "b", "c", "d", "e"]'}

        terms, _ = await _llm_expand("test", model_provider=VerboseModel(), max_expansions=2)
        assert len(terms) == 2


class TestExpandQuerySync:
    """Test the synchronous expand_query (backward compat)."""

    def test_basic_expansion(self):
        query = RetrievalQuery(raw_query="Who is Komal?")
        result = expand_query(query, enable_graph=False)
        assert result.original_query == "Who is Komal?"
        assert len(result.expanded_terms) > 0
        assert result.source in ("template", "none")

    def test_no_expansion(self):
        query = RetrievalQuery(raw_query="random words here")
        result = expand_query(query, enable_graph=False, enable_template=False)
        assert result.expanded_terms == []
        assert result.source == "none"


class TestExpandQueryAsync:
    """Test the async expand_query_async with LLM support."""

    @pytest.mark.asyncio
    async def test_llm_expansion(self):
        """With model_provider, should use LLM expansion."""
        class MockModel:
            async def call(self, slot, messages, **kwargs):
                return {"content": '["Komal school principal", "Freedom Generation"]'}

        query = RetrievalQuery(raw_query="Who is Komal?")
        result = await expand_query_async(
            query,
            model_provider=MockModel(),
            enable_llm=True,
            enable_graph=False,
            enable_template=False,
        )
        assert result.llm_expansion_used is True
        assert any("Komal" in t for t in result.expanded_terms)
        assert result.source == "llm"

    @pytest.mark.asyncio
    async def test_llm_fallback_to_template(self):
        """When LLM fails, falls back to template expansion."""
        class FailModel:
            async def call(self, slot, messages, **kwargs):
                raise RuntimeError("Service down")

        query = RetrievalQuery(raw_query="Who is Komal?")
        result = await expand_query_async(
            query,
            model_provider=FailModel(),
            enable_llm=True,
            enable_graph=False,
            enable_template=True,
        )
        # Should still get template-based expansions
        assert result.llm_expansion_used is False
        assert len(result.expanded_terms) > 0

    @pytest.mark.asyncio
    async def test_llm_disabled(self):
        """When enable_llm=False, LLM is not called."""
        class MockModel:
            async def call(self, slot, messages, **kwargs):
                return {"content": '["should not appear"]'}

        query = RetrievalQuery(raw_query="Who is Komal?")
        result = await expand_query_async(
            query,
            model_provider=MockModel(),
            enable_llm=False,
            enable_graph=False,
            enable_template=True,
        )
        assert result.llm_expansion_used is False

    @pytest.mark.asyncio
    async def test_combined_source(self):
        """When both graph and LLM contribute, source is 'combined'."""
        class MockModel:
            async def call(self, slot, messages, **kwargs):
                return {"content": '["LLM expansion"]'}

        # Simple graph store mock
        class MockNode:
            def __init__(self, name):
                self.canonical_name = name
                self.aliases = []

        class MockGraphStore:
            def get_neighbors(self, eid, min_confidence=0.4):
                return [MockNode("GraphNeighbor")]

        query = RetrievalQuery(raw_query="Who is Komal?")
        result = await expand_query_async(
            query,
            detected_entities=[("komal", 0.9)],
            graph_store=MockGraphStore(),
            model_provider=MockModel(),
            enable_llm=True,
            enable_graph=True,
            enable_template=True,
        )
        assert result.source == "combined"
        assert result.llm_expansion_used is True
        assert any("GraphNeighbor" in t for t in result.expanded_terms)
        assert any("LLM" in t for t in result.expanded_terms)


# ---------------------------------------------------------------------------
# Semantic Wiki Matching
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self):
        a = [1.0, 0.0, 0.0]
        assert _cosine_similarity(a, a) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert _cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert _cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_empty_vectors(self):
        assert _cosine_similarity([], []) == 0.0

    def test_different_lengths(self):
        assert _cosine_similarity([1.0], [1.0, 2.0]) == 0.0

    def test_zero_vector(self):
        assert _cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


class TestWikiRetrieverSemantic:
    def test_satisfies_retriever_protocol(self):
        wr = WikiRetriever()
        assert isinstance(wr, Retriever)

    @pytest.mark.asyncio
    async def test_no_db_path_returns_empty(self):
        """Without db_path, returns [] gracefully."""
        wr = WikiRetriever()
        query = RetrievalQuery(raw_query="test")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)

        hits = await wr.retrieve(query, budget=budget, trace=trace)
        assert hits == []

    @pytest.mark.asyncio
    async def test_with_embedding_provider_param(self):
        """WikiRetriever accepts embedding_provider parameter."""
        class MockEmbedding:
            async def embed(self, text: str) -> list[float]:
                return [0.1, 0.2, 0.3]

        wr = WikiRetriever(embedding_provider=MockEmbedding())
        assert wr._embedding_provider is not None
        assert wr.name == "WikiRetriever"


# ---------------------------------------------------------------------------
# Orchestrator Configuration Toggles
# ---------------------------------------------------------------------------


class TestOrchestratorToggles:
    def test_default_toggles(self):
        orch = RetrievalOrchestrator()
        assert orch.enable_query_expansion is True
        assert orch.enable_llm_expansion is True
        assert orch.enable_wiki_injection is True
        assert orch.enable_vector_retrieval is True

    def test_filter_vector_retriever(self):
        """When enable_vector_retrieval=False, VectorRetriever is filtered out."""
        orch = RetrievalOrchestrator()
        orch.enable_vector_retrieval = False
        orch.register_retriever(VectorRetriever())
        orch.register_retriever(FTSRetriever())

        active = orch._filter_retrievers()
        names = [r.name for r in active]
        assert "VectorRetriever" not in names
        assert "FTSRetriever" in names

    def test_filter_wiki_retriever(self):
        """When enable_wiki_injection=False, WikiRetriever is filtered out."""
        from aip.orchestration.retrievers.wiki_retriever import WikiRetriever

        orch = RetrievalOrchestrator()
        orch.enable_wiki_injection = False
        orch.register_retriever(WikiRetriever())
        orch.register_retriever(FTSRetriever())

        active = orch._filter_retrievers()
        names = [r.name for r in active]
        assert "WikiRetriever" not in names
        assert "FTSRetriever" in names

    def test_no_filter_when_enabled(self):
        """All retrievers pass through when toggles are enabled."""
        orch = RetrievalOrchestrator()
        orch.register_retriever(VectorRetriever())

        active = orch._filter_retrievers()
        assert len(active) == 1
        assert active[0].name == "VectorRetriever"

    @pytest.mark.asyncio
    async def test_orchestrator_with_model_provider(self):
        """Orchestrator should use model_provider for LLM expansion."""
        class MockModel:
            async def call(self, slot, messages, **kwargs):
                return {"content": '["expanded term"]'}

        class MockRetriever:
            @property
            def name(self) -> str:
                return "MockRetriever"

            async def retrieve(self, query, *, budget, trace):
                return [_make_hit("h1")]

        orch = RetrievalOrchestrator()
        orch.model_provider = MockModel()
        orch.register_retriever(MockRetriever())

        query = RetrievalQuery(raw_query="Who is Komal?")
        hits, trace = await orch.retrieve(query)

        assert len(hits) == 1
        # LLM expansion should have been attempted
        assert "llm_expansion_used" in trace.fallbacks_triggered

    @pytest.mark.asyncio
    async def test_budget_usage_populated(self):
        """Orchestrator should populate budget_usage in trace."""
        class MockRetriever:
            @property
            def name(self) -> str:
                return "MockRetriever"

            async def retrieve(self, query, *, budget, trace):
                hit = _make_hit("h1", channel=RetrievalChannel.FTS)
                return [hit]

        orch = RetrievalOrchestrator()
        orch.register_retriever(MockRetriever())
        orch.enable_query_expansion = False  # skip expansion for simplicity

        query = RetrievalQuery(raw_query="test")
        hits, trace = await orch.retrieve(query)

        assert "evidence_tokens" in trace.budget_usage
        assert "total_estimated_tokens" in trace.budget_usage
        assert "hit_count" in trace.budget_usage


# ---------------------------------------------------------------------------
# Integration: Full Stack Retrieval
# ---------------------------------------------------------------------------


class TestFullStackRetrieval:
    @pytest.mark.asyncio
    async def test_fts_plus_vector_rrf_fusion(self):
        """FTS + Vector retrievers should fuse via RRF."""
        class MockFTS:
            @property
            def name(self) -> str:
                return "FTSRetriever"

            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(
                    RetrieverTrace(retriever_name=self.name, hit_count=2)
                )
                return [
                    _make_hit("shared", score=0.9, rank=1, channel=RetrievalChannel.FTS),
                    _make_hit("fts_only", score=0.7, rank=2, channel=RetrievalChannel.FTS),
                ]

        class MockVector:
            @property
            def name(self) -> str:
                return "VectorRetriever"

            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(
                    RetrieverTrace(retriever_name=self.name, hit_count=2)
                )
                return [
                    _make_hit("shared", score=0.85, rank=1, channel=RetrievalChannel.VECTOR),
                    _make_hit("vec_only", score=0.6, rank=2, channel=RetrievalChannel.VECTOR),
                ]

        orch = RetrievalOrchestrator()
        orch.register_retriever(MockFTS())
        orch.register_retriever(MockVector())
        orch.enable_query_expansion = False

        query = RetrievalQuery(raw_query="test")
        hits, trace = await orch.retrieve(query)

        # "shared" should rank highest (appears in both channels)
        assert len(hits) >= 3
        assert hits[0].id == "shared"
        # Should have multi-channel fusion debug info
        assert "fts" in hits[0].debug.get("rrf_channels", [])
        assert "vector" in hits[0].debug.get("rrf_channels", [])

    @pytest.mark.asyncio
    async def test_all_four_retrievers_registered(self):
        """FTS + Graph + Wiki + Vector all registered and producing results."""
        class MockFTS:
            @property
            def name(self) -> str:
                return "FTSRetriever"

            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(
                    RetrieverTrace(retriever_name=self.name, hit_count=1)
                )
                return [_make_hit("fts1", channel=RetrievalChannel.FTS)]

        class MockGraph:
            @property
            def name(self) -> str:
                return "GraphRetriever"

            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(
                    RetrieverTrace(retriever_name=self.name, hit_count=1)
                )
                return [_make_hit("graph1", channel=RetrievalChannel.GRAPH)]

        class MockWiki:
            @property
            def name(self) -> str:
                return "WikiRetriever"

            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(
                    RetrieverTrace(retriever_name=self.name, hit_count=1)
                )
                return [_make_hit("wiki1", channel=RetrievalChannel.WIKI)]

        class MockVector:
            @property
            def name(self) -> str:
                return "VectorRetriever"

            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(
                    RetrieverTrace(retriever_name=self.name, hit_count=1)
                )
                return [_make_hit("vec1", channel=RetrievalChannel.VECTOR)]

        orch = RetrievalOrchestrator()
        orch.register_retriever(MockFTS())
        orch.register_retriever(MockGraph())
        orch.register_retriever(MockWiki())
        orch.register_retriever(MockVector())
        orch.enable_query_expansion = False

        query = RetrievalQuery(raw_query="test")
        hits, trace = await orch.retrieve(query)

        assert len(hits) == 4
        channels = {h.retrieval_channel for h in hits}
        assert RetrievalChannel.FTS in channels
        assert RetrievalChannel.GRAPH in channels
        assert RetrievalChannel.WIKI in channels
        assert RetrievalChannel.VECTOR in channels
        assert len(trace.retriever_traces) == 4


# Need FTSRetriever import for the filter test
from aip.orchestration.retrievers.fts_retriever import FTSRetriever
