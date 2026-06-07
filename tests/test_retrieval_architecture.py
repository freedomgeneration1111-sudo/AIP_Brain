"""Tests for the unified retrieval architecture — Phase 5.1.

Covers:
- Retriever protocol (structural check)
- FTSRetriever (with mock stores)
- RRF fusion (pure function)
- RetrievalOrchestrator (dispatch + fusion + curation)
- Importance weighting and budget curation
- sanitize_fts_query edge cases
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from datetime import datetime

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
from aip.orchestration.retrievers.fts_retriever import FTSRetriever, sanitize_fts_query
from aip.orchestration.retrievers.rrf_fusion import rrf_fuse
from aip.orchestration.retrievers.orchestrator import (
    RetrievalOrchestrator,
    apply_budget_curation,
    apply_importance_weighting,
)


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
# Retriever protocol
# ---------------------------------------------------------------------------


class TestRetrieverProtocol:
    def test_fts_retriever_satisfies_protocol(self):
        """FTSRetriever should satisfy the Retriever protocol."""
        retriever = FTSRetriever()
        assert isinstance(retriever, Retriever)

    def test_protocol_has_name_and_retrieve(self):
        """Protocol requires 'name' property and 'retrieve' async method."""
        assert hasattr(Retriever, "name")
        assert hasattr(Retriever, "retrieve")

    def test_custom_retriever_satisfies_protocol(self):
        """A simple custom retriever should satisfy the protocol."""
        class DummyRetriever:
            @property
            def name(self) -> str:
                return "DummyRetriever"

            async def retrieve(self, query, *, budget, trace):
                return []

        assert isinstance(DummyRetriever(), Retriever)


# ---------------------------------------------------------------------------
# sanitize_fts_query
# ---------------------------------------------------------------------------


class TestSanitizeFtsQuery:
    def test_basic_query(self):
        result = sanitize_fts_query("What features does AIP have")
        assert "AND" in result
        assert "features" in result
        assert "does" not in result  # stop word

    def test_possessive_apostrophe(self):
        """AIP's should not crash FTS5 — apostrophe stripped, tokens cleaned."""
        result = sanitize_fts_query("How does AIP's retrieval work")
        assert "'" not in result
        # After stripping ', "AIP's" becomes "AIPs" → token "AIPs"
        # stop-word filter removes short tokens, but "AIPs" is 4 chars
        assert "AIPs" in result or "AIP" in result

    def test_special_chars_stripped(self):
        result = sanitize_fts_query("What? *who* +where- (when)")
        assert "?" not in result
        assert "*" not in result
        assert "+" not in result
        assert "-" not in result
        assert "(" not in result

    def test_stop_words_removed(self):
        result = sanitize_fts_query("the is are was were be been")
        # All stop words — should fall back
        assert result != ""

    def test_empty_query(self):
        result = sanitize_fts_query("")
        assert result == ""

    def test_single_char_tokens(self):
        """Single-char meaningful tokens in fallback."""
        result = sanitize_fts_query("a b c")
        # All are stop words or single char — fallback returns original
        assert result != ""


# ---------------------------------------------------------------------------
# RRF Fusion
# ---------------------------------------------------------------------------


class TestRrfFusion:
    def test_single_channel(self):
        """Single channel: RRF should preserve order."""
        hits = [_make_hit("h1", 0.9, 1), _make_hit("h2", 0.8, 2), _make_hit("h3", 0.7, 3)]
        result = rrf_fuse({RetrievalChannel.FTS: hits})
        assert len(result) == 3
        # RRF score for rank 1 = 1/(60+1) ≈ 0.01639
        assert result[0].id == "h1"
        assert result[0].score > result[1].score

    def test_multi_channel_dedup(self):
        """Same hit in two channels: score accumulates (multi-source agreement)."""
        hit_fts = _make_hit("h1", 0.9, 1, RetrievalChannel.FTS)
        hit_vec = _make_hit("h1", 0.8, 2, RetrievalChannel.VECTOR)
        hit_only_fts = _make_hit("h2", 0.7, 2, RetrievalChannel.FTS)

        result = rrf_fuse({
            RetrievalChannel.FTS: [hit_fts, hit_only_fts],
            RetrievalChannel.VECTOR: [hit_vec],
        })
        # h1 should rank higher because it appears in both channels
        assert result[0].id == "h1"
        assert "fts" in result[0].debug["rrf_channels"]
        assert "vector" in result[0].debug["rrf_channels"]

    def test_empty_input(self):
        result = rrf_fuse({})
        assert result == []

    def test_empty_channel(self):
        result = rrf_fuse({RetrievalChannel.FTS: []})
        assert result == []

    def test_k_parameter(self):
        """Smaller k amplifies top ranks more."""
        hits = [_make_hit("h1", 0.9, 1), _make_hit("h2", 0.1, 10)]
        result_small_k = rrf_fuse({RetrievalChannel.FTS: hits}, k=10)
        result_large_k = rrf_fuse({RetrievalChannel.FTS: hits}, k=100)
        # With smaller k, the gap between rank 1 and rank 10 is wider
        gap_small = result_small_k[0].score - result_small_k[1].score
        gap_large = result_large_k[0].score - result_large_k[1].score
        assert gap_small > gap_large

    def test_ranks_reassigned(self):
        """Output hits should have sequential ranks starting from 1."""
        hits = [_make_hit(f"h{i}", score=0.5, rank=i) for i in range(1, 6)]
        result = rrf_fuse({RetrievalChannel.FTS: hits})
        for i, hit in enumerate(result, start=1):
            assert hit.rank == i


# ---------------------------------------------------------------------------
# Importance weighting
# ---------------------------------------------------------------------------


class TestImportanceWeighting:
    def test_high_importance_boosted(self):
        hits = [
            _make_hit("h1", score=0.5, importance=0.9),
            _make_hit("h2", score=0.5, importance=0.1),
        ]
        result = apply_importance_weighting(hits)
        assert result[0].id == "h1"  # higher importance should rank first
        assert result[0].score > result[1].score

    def test_no_importance(self):
        hits = [_make_hit("h1", score=0.5, importance=None)]
        result = apply_importance_weighting(hits)
        # No crash, score unchanged (importance=None → 0.0)
        assert len(result) == 1

    def test_empty_input(self):
        assert apply_importance_weighting([]) == []

    def test_custom_weight(self):
        hits = [_make_hit("h1", score=0.5, importance=1.0)]
        result = apply_importance_weighting(hits, importance_weight=0.5)
        # adjustment = 1.0 * 0.5 = 0.5
        assert result[0].score == pytest.approx(0.5 + 0.5, abs=0.001)


# ---------------------------------------------------------------------------
# Budget curation
# ---------------------------------------------------------------------------


class TestBudgetCuration:
    def test_max_sources_cap(self):
        hits = [_make_hit(f"h{i}") for i in range(50)]
        budget = RetrievalBudget(max_sources=5)
        result = apply_budget_curation(hits, budget)
        assert len(result) == 5

    def test_same_conversation_cap(self):
        hits = [
            _make_hit("h1", domain="d1", importance=0.5),
            _make_hit("h2", domain="d1", importance=0.5),
            _make_hit("h3", domain="d1", importance=0.5),
            _make_hit("h4", domain="d1", importance=0.5),
        ]
        # All from same conversation
        for h in hits:
            h.debug["conversation_id"] = "conv_1"

        budget = RetrievalBudget(max_sources=10, max_same_conversation=2)
        result = apply_budget_curation(hits, budget)
        # Should only accept 2 from same conversation
        assert len(result) == 2

    def test_same_domain_cap(self):
        hits = [_make_hit(f"h{i}", domain="same_domain") for i in range(10)]
        budget = RetrievalBudget(max_sources=10, max_same_domain_pct=0.3)
        result = apply_budget_curation(hits, budget)
        # 0.3 * 10 = 3 from same domain
        assert len(result) == 3

    def test_empty_input(self):
        assert apply_budget_curation([], RetrievalBudget()) == []


# ---------------------------------------------------------------------------
# FTSRetriever
# ---------------------------------------------------------------------------


class TestFTSRetriever:
    def test_name(self):
        r = FTSRetriever()
        assert r.name == "FTSRetriever"

    @pytest.mark.asyncio
    async def test_no_stores(self):
        """With no stores configured, should return empty list gracefully."""
        r = FTSRetriever()
        query = RetrievalQuery(raw_query="test query")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)
        hits = await r.retrieve(query, budget=budget, trace=trace)
        assert hits == []
        # Should still record a retriever trace
        assert len(trace.retriever_traces) == 1
        assert trace.retriever_traces[0].retriever_name == "FTSRetriever"

    @pytest.mark.asyncio
    async def test_mock_corpus_store(self):
        """With a mock corpus store, should return hits and populate trace."""
        @dataclass
        class MockTurn:
            turn_id: str = "t1"
            conversation_id: str = "conv1"
            conversation_name: str = "Test Conv"
            primary_domain: str = "test"
            searchable_text: str = "This is test content about Komal"
            importance: float = 0.8
            beast_confidence: float = 0.9
            turn_timestamp: str = ""
            domains: list = None
            tags: list = None
            bridges: list = None
            source_model: str = "test"
            tagging_version: int = 1

            def __post_init__(self):
                if self.domains is None:
                    self.domains = []
                if self.tags is None:
                    self.tags = []
                if self.bridges is None:
                    self.bridges = []

        class MockCorpusStore:
            async def search(self, query, primary_domain=None, limit=10):
                return [MockTurn()]

        r = FTSRetriever(corpus_turn_store=MockCorpusStore())
        query = RetrievalQuery(raw_query="Who is Komal?")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)

        hits = await r.retrieve(query, budget=budget, trace=trace)
        assert len(hits) == 1
        assert hits[0].id == "t1"
        assert hits[0].retrieval_channel == RetrievalChannel.FTS
        assert hits[0].source_type == "corpus_turn"
        # Trace should be populated
        assert len(trace.retriever_traces) == 1
        assert trace.retriever_traces[0].hit_count == 1
        assert trace.retriever_traces[0].elapsed_ms > 0


# ---------------------------------------------------------------------------
# RetrievalOrchestrator
# ---------------------------------------------------------------------------


class TestRetrievalOrchestrator:
    def test_register_retriever(self):
        orch = RetrievalOrchestrator()
        r = FTSRetriever()
        orch.register_retriever(r)
        assert "FTSRetriever" in orch.retriever_names

    @pytest.mark.asyncio
    async def test_no_retrievers(self):
        """With no retrievers, should return empty hits and valid trace."""
        orch = RetrievalOrchestrator()
        query = RetrievalQuery(raw_query="test")
        hits, trace = await orch.retrieve(query)
        assert hits == []
        assert isinstance(trace, RetrievalTrace)
        assert trace.total_hits == 0

    @pytest.mark.asyncio
    async def test_with_mock_retriever(self):
        """Orchestrator should fuse results from a mock retriever."""
        class MockRetriever:
            @property
            def name(self) -> str:
                return "MockRetriever"

            async def retrieve(self, query, *, budget, trace):
                hits = [
                    _make_hit("h1", score=0.9, rank=1),
                    _make_hit("h2", score=0.8, rank=2),
                ]
                # Manually add trace (retriever protocol requirement)
                trace.retriever_traces.append(
                    RetrieverTrace(retriever_name=self.name, hit_count=2)
                )
                return hits

        orch = RetrievalOrchestrator()
        orch.register_retriever(MockRetriever())
        query = RetrievalQuery(raw_query="test")
        hits, trace = await orch.retrieve(query)
        assert len(hits) == 2
        assert trace.final_selected_ids[:2] == ["h1", "h2"]

    @pytest.mark.asyncio
    async def test_failed_retriever_graceful(self):
        """If a retriever raises, the orchestrator should continue."""
        class FailingRetriever:
            @property
            def name(self) -> str:
                return "FailingRetriever"

            async def retrieve(self, query, *, budget, trace):
                raise RuntimeError("DB connection lost")

        class GoodRetriever:
            @property
            def name(self) -> str:
                return "GoodRetriever"

            async def retrieve(self, query, *, budget, trace):
                return [_make_hit("h1", score=0.5, rank=1)]

        orch = RetrievalOrchestrator()
        orch.register_retriever(FailingRetriever())
        orch.register_retriever(GoodRetriever())

        query = RetrievalQuery(raw_query="test")
        hits, trace = await orch.retrieve(query)
        assert len(hits) == 1
        assert hits[0].id == "h1"
        assert "FailingRetriever_error" in trace.fallbacks_triggered

    @pytest.mark.asyncio
    async def test_importance_and_curation_applied(self):
        """Orchestrator should apply importance weighting and budget curation."""
        class HighImportanceRetriever:
            @property
            def name(self) -> str:
                return "HIRetriever"

            async def retrieve(self, query, *, budget, trace):
                hits = [
                    _make_hit("low_imp", score=0.5, importance=0.1, domain="d1"),
                    _make_hit("high_imp", score=0.5, importance=0.9, domain="d1"),
                ]
                return hits

        orch = RetrievalOrchestrator()
        orch.register_retriever(HighImportanceRetriever())
        query = RetrievalQuery(raw_query="test")
        budget = RetrievalBudget(max_sources=10)
        hits, trace = await orch.retrieve(query, budget=budget)
        # High importance should rank first after weighting
        assert hits[0].id == "high_imp"
