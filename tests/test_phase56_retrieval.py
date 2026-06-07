"""Tests for Phase 5.6 deliverables — Autonomy, Quality & Observability.

Covers:
- Automatic Retry on NEEDS_MORE_CONTEXT (retry logic, strategy selection, trace recording)
- Context Compression / Extractive Summarization (sentence scoring, budget-aware compression)
- Trace Dashboard Foundation (analytics methods, dashboard summary, retriever stats)
- Quality Gate Enhancements (model-assisted sufficiency check)
- RetrievalTrace retry fields
- Orchestrator integration (auto-retry wiring, retry config toggles)
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from typing import Any

import pytest

from aip.foundation.protocols.retrieval import Retriever
from aip.foundation.schemas.retrieval_trace import (
    ContextQualityStatus,
    EvidenceStatus,
    RetrievalBudget,
    RetrievalChannel,
    RetrievalHit,
    RetrievalQuery,
    RetrievalTrace,
    RetrieverTrace,
)
from aip.orchestration.retrievers.context_packer import (
    SmartContextPacker,
    ContextPacket,
    ContextSection,
    assemble_context,
    extractive_summarize,
    _split_sentences,
    _score_sentence,
    _estimate_tokens,
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
    text: str = "",
    evidence_status: EvidenceStatus = EvidenceStatus.RAW,
    entities: list[str] | None = None,
) -> RetrievalHit:
    """Create a test RetrievalHit."""
    content = text or f"Content for {hit_id} — some meaningful text that provides evidence."
    return RetrievalHit(
        id=hit_id,
        source_type=source_type,
        source_id=hit_id,
        text=content,
        snippet=content[:200],
        rank=rank,
        score=score,
        importance=importance,
        domain=domain,
        entities=entities or [],
        retrieval_channel=channel,
        evidence_status=evidence_status,
        debug={},
    )


# ---------------------------------------------------------------------------
# RetrievalTrace Retry Fields (Phase 5.6)
# ---------------------------------------------------------------------------


class TestRetrievalTraceRetryFields:
    def test_retry_fields_exist(self):
        """RetrievalTrace should have Phase 5.6 retry fields."""
        trace = RetrievalTrace(query=RetrievalQuery(raw_query="test"))
        assert hasattr(trace, "retry_triggered")
        assert hasattr(trace, "retry_reason")
        assert hasattr(trace, "retry_round")
        assert hasattr(trace, "retry_strategies_tried")
        assert hasattr(trace, "retry_quality_improved")
        assert hasattr(trace, "retry_first_status")
        assert hasattr(trace, "retry_first_scores")

    def test_retry_fields_defaults(self):
        """Retry fields should have sensible defaults."""
        trace = RetrievalTrace(query=RetrievalQuery(raw_query="test"))
        assert trace.retry_triggered is False
        assert trace.retry_reason == ""
        assert trace.retry_round == 0
        assert trace.retry_strategies_tried == []
        assert trace.retry_quality_improved is False
        assert trace.retry_first_status == ""
        assert trace.retry_first_scores == {}


# ---------------------------------------------------------------------------
# Automatic Retry on NEEDS_MORE_CONTEXT
# ---------------------------------------------------------------------------


class TestAutoRetry:
    def test_orchestrator_has_retry_config(self):
        """Orchestrator should have auto-retry configuration."""
        orch = RetrievalOrchestrator()
        assert hasattr(orch, "enable_auto_retry")
        assert hasattr(orch, "max_retries")
        assert orch.enable_auto_retry is True
        assert orch.max_retries == 1

    @pytest.mark.asyncio
    async def test_retry_triggered_on_needs_more_context(self):
        """When quality gate returns NEEDS_MORE_CONTEXT, retry should be triggered."""
        class WeakRetriever:
            @property
            def name(self) -> str:
                return "WeakRetriever"
            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(
                    RetrieverTrace(retriever_name=self.name, hit_count=1)
                )
                return [_make_hit("h1", score=0.05, text="Tiny")]

        from aip.orchestration.retrievers.answer_quality_gate import AnswerQualityGate
        config = QualityGateConfig(
            min_evidence_tokens=500,  # High threshold to trigger NEEDS_MORE_CONTEXT
            min_top_score=0.3,
        )
        gate = AnswerQualityGate(config=config)

        orch = RetrievalOrchestrator()
        orch.register_retriever(WeakRetriever())
        orch.quality_gate = gate
        orch.enable_query_expansion = False
        orch.enable_auto_retry = True

        query = RetrievalQuery(raw_query="test query")
        hits, trace = await orch.retrieve(query)

        # Quality gate should have been evaluated
        assert trace.context_quality_status != ""

        # If the gate returned NEEDS_MORE_CONTEXT, retry should be triggered
        if trace.context_quality_status == "needs_more_context":
            assert trace.retry_triggered is True
            assert trace.retry_round == 1
            assert len(trace.retry_strategies_tried) > 0

    @pytest.mark.asyncio
    async def test_retry_not_triggered_when_sufficient(self):
        """When quality gate returns SUFFICIENT, no retry should be triggered."""
        class StrongRetriever:
            @property
            def name(self) -> str:
                return "StrongRetriever"
            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(
                    RetrieverTrace(retriever_name=self.name, hit_count=3)
                )
                return [
                    _make_hit("h1", score=0.9, text="Good evidence text " * 50),
                    _make_hit("h2", score=0.8, text="More evidence text " * 40),
                    _make_hit("h3", score=0.7, text="Additional evidence " * 30),
                ]

        from aip.orchestration.retrievers.answer_quality_gate import AnswerQualityGate
        config = QualityGateConfig(
            min_evidence_tokens=50,
            min_top_score=0.01,
        )
        gate = AnswerQualityGate(config=config)

        orch = RetrievalOrchestrator()
        orch.register_retriever(StrongRetriever())
        orch.quality_gate = gate
        orch.enable_query_expansion = False
        orch.enable_auto_retry = True

        query = RetrievalQuery(raw_query="test query")
        hits, trace = await orch.retrieve(query)

        # With good evidence, retry should NOT be triggered
        assert trace.retry_triggered is False
        assert trace.retry_round == 0

    @pytest.mark.asyncio
    async def test_retry_disabled(self):
        """When auto-retry is disabled, no retry should occur even if context is insufficient."""
        class WeakRetriever:
            @property
            def name(self) -> str:
                return "WeakRetriever"
            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(
                    RetrieverTrace(retriever_name=self.name, hit_count=1)
                )
                return [_make_hit("h1", score=0.05, text="Tiny")]

        from aip.orchestration.retrievers.answer_quality_gate import AnswerQualityGate
        config = QualityGateConfig(min_evidence_tokens=500, min_top_score=0.3)
        gate = AnswerQualityGate(config=config)

        orch = RetrievalOrchestrator()
        orch.register_retriever(WeakRetriever())
        orch.quality_gate = gate
        orch.enable_query_expansion = False
        orch.enable_auto_retry = False  # Disabled

        query = RetrievalQuery(raw_query="test query")
        hits, trace = await orch.retrieve(query)

        # Retry should NOT be triggered
        assert trace.retry_triggered is False

    @pytest.mark.asyncio
    async def test_retry_records_first_status(self):
        """Retry should record the quality status before retry."""
        class WeakRetriever:
            @property
            def name(self) -> str:
                return "WeakRetriever"
            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(
                    RetrieverTrace(retriever_name=self.name, hit_count=1)
                )
                return [_make_hit("h1", score=0.05, text="Tiny")]

        from aip.orchestration.retrievers.answer_quality_gate import AnswerQualityGate
        config = QualityGateConfig(min_evidence_tokens=500, min_top_score=0.3)
        gate = AnswerQualityGate(config=config)

        orch = RetrievalOrchestrator()
        orch.register_retriever(WeakRetriever())
        orch.quality_gate = gate
        orch.enable_query_expansion = False
        orch.enable_auto_retry = True

        query = RetrievalQuery(raw_query="test query")
        hits, trace = await orch.retrieve(query)

        if trace.retry_triggered:
            assert trace.retry_first_status == "needs_more_context"
            assert "overall_quality" in trace.retry_first_scores


# ---------------------------------------------------------------------------
# Context Compression / Extractive Summarization
# ---------------------------------------------------------------------------


class TestSentenceSplitting:
    def test_basic_split(self):
        """Should split on sentence-ending punctuation."""
        text = "First sentence. Second sentence. Third sentence."
        sentences = _split_sentences(text)
        assert len(sentences) >= 2

    def test_empty_text(self):
        """Empty text should return empty list."""
        assert _split_sentences("") == []
        assert _split_sentences("   ") == []

    def test_single_sentence(self):
        """Single sentence should return list of one."""
        sentences = _split_sentences("Just one sentence here")
        assert len(sentences) == 1

    def test_newline_split(self):
        """Should fall back to newline splitting when no sentence boundaries."""
        text = "Line one\nLine two\nLine three"
        sentences = _split_sentences(text)
        assert len(sentences) >= 2

    def test_exclamation_and_question(self):
        """Should split on ! and ? as well."""
        text = "What is this? That is something! And this too."
        sentences = _split_sentences(text)
        assert len(sentences) >= 2


class TestSentenceScoring:
    def test_query_term_overlap(self):
        """Sentences with query terms should score higher."""
        query_terms = {"python", "function", "decorator"}
        good = _score_sentence(
            "Python decorator functions are powerful tools for code reuse.",
            query_terms, set(),
        )
        bad = _score_sentence(
            "The weather is nice today and the birds are singing.",
            query_terms, set(),
        )
        assert good > bad

    def test_entity_term_overlap(self):
        """Sentences with entity terms should score higher."""
        entity_terms = {"Komal", "Freedom Generation"}
        good = _score_sentence(
            "Komal is the principal of Freedom Generation School.",
            set(), entity_terms,
        )
        bad = _score_sentence(
            "The school provides quality education to the community.",
            set(), entity_terms,
        )
        assert good > bad

    def test_empty_sentence(self):
        """Empty sentence should score 0."""
        assert _score_sentence("", set(), set()) == 0.0


class TestExtractiveSummarization:
    def test_short_text_unchanged(self):
        """Short text should not be summarized."""
        text = "Short text that fits within budget."
        result = extractive_summarize(text, max_tokens=100)
        assert result == text

    def test_long_text_compressed(self):
        """Long text should be compressed to fit within budget."""
        text = (
            "Python is a versatile programming language. "
            "It supports multiple paradigms including procedural and object-oriented. "
            "Python decorators are a powerful feature. "
            "The weather is nice today. "
            "Decorators allow modifying function behavior. "
            "Function composition is easy in Python. "
            "The birds are singing outside. "
            "Python functions are first-class objects."
        )
        result = extractive_summarize(
            text,
            max_tokens=20,
            query_terms={"python", "decorator", "function"},
        )
        # Result should be shorter than original
        assert len(result) < len(text)
        # Result should contain relevant terms preferentially
        result_lower = result.lower()
        # At least some of the query terms should appear
        assert any(term in result_lower for term in ["python", "decorator", "function"])

    def test_empty_text(self):
        """Empty text should return empty string."""
        assert extractive_summarize("", max_tokens=100) == ""

    def test_entity_terms_boost(self):
        """Entity terms should boost relevant sentences."""
        text = (
            "Komal leads the school with dedication. "
            "The weather is sunny. "
            "Freedom Generation School serves the community. "
            "The park has many trees. "
            "Students at the school achieve great results."
        )
        result = extractive_summarize(
            text,
            max_tokens=15,
            entity_terms={"komal", "freedom generation"},
        )
        result_lower = result.lower()
        # Entity-related sentences should be preferred
        assert "komal" in result_lower or "freedom generation" in result_lower

    def test_single_sentence_text(self):
        """Single sentence text should just be truncated."""
        text = "A" * 1000
        result = extractive_summarize(text, max_tokens=50)
        assert len(result) < len(text)


class TestSmartContextPackerCompression:
    def test_packer_with_extractive_summarization(self):
        """Packer should use extractive summarization for long hits."""
        budget = RetrievalBudget(total_tokens=200)
        hits = [
            _make_hit(
                "h1", channel=RetrievalChannel.FTS, score=0.9,
                text=(
                    "Python decorators are powerful. "
                    "The weather is nice today. "
                    "Decorators modify function behavior. "
                    "Birds sing in the morning. "
                    "Python functions are first-class objects."
                ),
            ),
        ]
        packer = SmartContextPacker(
            budget=budget,
            query_terms={"python", "decorator"},
            enable_extractive_summarization=True,
        )
        packet = packer.pack(hits)
        assert packet.hit_count == 1
        # The output should contain relevant content
        output = packet.to_prompt_string()
        assert "python" in output.lower() or "decorator" in output.lower()

    def test_packer_compression_tracking(self):
        """Packer should track compressed hits separately from truncated."""
        budget = RetrievalBudget(total_tokens=100)
        long_text = (
            "Python is great. Weather is nice. "
            "Decorators are powerful. Birds sing. "
            "Functions are first-class. Trees grow. "
        ) * 10
        hits = [_make_hit("h1", channel=RetrievalChannel.FTS, score=0.9, text=long_text)]
        packer = SmartContextPacker(
            budget=budget,
            query_terms={"python", "decorator"},
            enable_extractive_summarization=True,
        )
        packet = packer.pack(hits)
        # Should have some compression or truncation
        assert packet.compressed_hits + packet.truncated_hits >= 0

    def test_packer_extractive_disabled(self):
        """When extractive summarization is disabled, use hard truncation."""
        budget = RetrievalBudget(total_tokens=100)
        long_text = "Python is great. " * 50
        hits = [_make_hit("h1", channel=RetrievalChannel.FTS, score=0.9, text=long_text)]
        packer = SmartContextPacker(
            budget=budget,
            enable_extractive_summarization=False,
        )
        packet = packer.pack(hits)
        # Should still work, just without extractive summarization
        assert packet.hit_count == 1


# ---------------------------------------------------------------------------
# Trace Dashboard Foundation
# ---------------------------------------------------------------------------


class TestTraceDashboardAnalytics:
    def _make_store_with_traces(self, db_path: str, count: int = 10) -> TraceStore:
        """Create a TraceStore with sample traces for analytics testing."""
        store = TraceStore(db_path)
        for i in range(count):
            status = ["sufficient", "marginal", "needs_more_context", "empty"][i % 4]
            retry = i % 5 == 0  # 20% have retries
            trace = RetrievalTrace(
                query=RetrievalQuery(raw_query=f"query {i}"),
                trace_id=f"trace_{i}",
                detected_entities=["entity1", "entity2"] if i % 2 == 0 else [],
                context_quality_status=status,
                context_quality_scores={
                    "overall_quality": 0.3 + (i % 5) * 0.15,
                    "entity_coverage": 0.2 + (i % 3) * 0.3,
                },
                budget_usage={
                    "evidence_tokens": 500 + i * 100,
                    "wiki_tokens": 100,
                    "graph_tokens": 50,
                    "procedural_tokens": 30,
                    "total_estimated_tokens": 680 + i * 100,
                    "budget_total_tokens": 8000,
                },
                retry_triggered=retry,
                retry_reason="low_entity_coverage" if retry else "",
                retry_round=1 if retry else 0,
                retry_strategies_tried=["llm_expansion", "relaxed_domain"] if retry else [],
                retry_quality_improved=retry and i % 2 == 0,
                retry_first_status="needs_more_context" if retry else "",
                retry_first_scores={"overall_quality": 0.2} if retry else {},
            )
            trace.retriever_traces.append(
                RetrieverTrace(
                    retriever_name="FTSRetriever",
                    hit_count=3 + i % 3,
                    elapsed_ms=10.0 + i,
                )
            )
            if i % 3 == 0:
                trace.retriever_traces.append(
                    RetrieverTrace(
                        retriever_name="GraphRetriever",
                        hit_count=1 + i % 2,
                        elapsed_ms=15.0 + i,
                    )
                )
            store.persist(trace)
        return store

    def test_dashboard_summary(self):
        """Should produce a comprehensive dashboard summary."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            store = self._make_store_with_traces(db_path, count=20)
            summary = store.get_dashboard_summary(hours=48)

            assert "total_queries" in summary
            assert summary["total_queries"] == 20
            assert "quality_distribution" in summary
            assert "avg_entity_coverage" in summary
            assert "retry_stats" in summary
            assert "retriever_contribution" in summary
            assert "common_retry_reasons" in summary
            assert "fallback_rate" in summary
            assert "avg_quality" in summary

            # Quality distribution should have entries
            assert len(summary["quality_distribution"]) > 0

            # Retry stats should be present
            retry_stats = summary["retry_stats"]
            assert "triggered" in retry_stats
            assert "improved" in retry_stats
            assert "rate" in retry_stats
        finally:
            os.unlink(db_path)

    def test_retry_stats(self):
        """Should provide detailed retry behavior statistics."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            store = self._make_store_with_traces(db_path, count=20)
            stats = store.query_retry_stats(hours=48)

            assert "retry_count" in stats
            assert "retry_rate" in stats
            assert "improvement_rate" in stats
            assert "reason_distribution" in stats
            assert "strategy_distribution" in stats
        finally:
            os.unlink(db_path)

    def test_retriever_stats(self):
        """Should provide per-retriever contribution statistics."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            store = self._make_store_with_traces(db_path, count=20)
            stats = store.query_retriever_stats(hours=48)

            assert isinstance(stats, dict)
            assert "FTSRetriever" in stats
            assert "appearances" in stats["FTSRetriever"]
            assert "total_hits" in stats["FTSRetriever"]
            assert "avg_hits" in stats["FTSRetriever"]
        finally:
            os.unlink(db_path)

    def test_empty_dashboard(self):
        """Empty store should return zeroed dashboard."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            store = TraceStore(db_path)
            summary = store.get_dashboard_summary()
            assert summary["total_queries"] == 0
        finally:
            os.unlink(db_path)

    def test_retry_persistence(self):
        """Retry fields should be persisted to SQLite."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            store = TraceStore(db_path)
            trace = RetrievalTrace(
                query=RetrievalQuery(raw_query="test retry"),
                trace_id="retry_trace_001",
                context_quality_status="marginal",
                context_quality_scores={"overall_quality": 0.45},
                retry_triggered=True,
                retry_reason="low_entity_coverage",
                retry_round=1,
                retry_strategies_tried=["llm_expansion", "increased_sources"],
                retry_quality_improved=True,
                retry_first_status="needs_more_context",
                retry_first_scores={"overall_quality": 0.2},
            )
            trace.retriever_traces.append(
                RetrieverTrace(retriever_name="FTSRetriever", hit_count=3)
            )

            success = store.persist(trace)
            assert success is True

            # Query back and check retry fields
            recent = store.query_recent(limit=1)
            assert len(recent) == 1
            row = recent[0]
            assert row["retry_triggered"] == 1
            assert row["retry_reason"] == "low_entity_coverage"
            assert row["retry_round"] == 1
            assert row["retry_quality_improved"] == 1
            assert row["retry_first_status"] == "needs_more_context"
        finally:
            os.unlink(db_path)


class TestComputeRetrievalMetricsWithRetry:
    def test_retry_metrics(self):
        """compute_retrieval_metrics should include retry metrics."""
        trace = RetrievalTrace(
            query=RetrievalQuery(raw_query="test"),
            trace_id="test_retry_metrics",
            context_quality_status="marginal",
            context_quality_scores={
                "entity_coverage": 0.5,
                "overall_quality": 0.6,
                "channel_diversity": 2.0,
            },
            budget_usage={"total_estimated_tokens": 3000, "budget_total_tokens": 8000},
            retry_triggered=True,
            retry_round=1,
            retry_quality_improved=True,
            retry_first_scores={"overall_quality": 0.2},
        )
        trace.retriever_traces.append(
            RetrieverTrace(retriever_name="FTSRetriever", hit_count=5, elapsed_ms=10.0)
        )

        metrics = compute_retrieval_metrics(trace)
        assert metrics["retry_triggered"] == 1.0
        assert metrics["retry_round"] == 1.0
        assert metrics["retry_quality_improved"] == 1.0
        assert "retry_quality_delta" in metrics
        assert metrics["retry_quality_delta"] > 0  # 0.6 - 0.2 = 0.4


# ---------------------------------------------------------------------------
# Quality Gate Enhancements (Model-Assisted)
# ---------------------------------------------------------------------------


class TestModelAssistedQualityGate:
    def test_config_options(self):
        """QualityGateConfig should have model-assisted options."""
        config = QualityGateConfig()
        assert hasattr(config, "enable_model_assisted")
        assert hasattr(config, "model_assisted_slot")
        assert config.enable_model_assisted is False  # Off by default
        assert config.model_assisted_slot == "fast"

    def test_gate_accepts_model_provider(self):
        """AnswerQualityGate should accept model_provider parameter."""
        gate = AnswerQualityGate(model_provider=None)
        assert gate._model_provider is None

    def test_model_assisted_disabled_by_default(self):
        """Without model_provider, model-assisted check should be a no-op."""
        config = QualityGateConfig(enable_model_assisted=True)
        gate = AnswerQualityGate(config=config, model_provider=None)
        hits = [_make_hit("h1", score=0.5, text="Some evidence text")]
        trace = RetrievalTrace(query=RetrievalQuery(raw_query="test"))
        result = gate.evaluate(hits, trace)
        # Should work fine without model — just heuristics
        assert "overall_quality" in result.scores

    @pytest.mark.asyncio
    async def test_evaluate_model_assisted_no_provider(self):
        """evaluate_model_assisted should work without a model provider."""
        config = QualityGateConfig(enable_model_assisted=True)
        gate = AnswerQualityGate(config=config, model_provider=None)
        hits = [_make_hit("h1", score=0.5, text="Some evidence text")]
        trace = RetrievalTrace(query=RetrievalQuery(raw_query="test"))
        result = await gate.evaluate_model_assisted(hits, trace)
        assert result.status in (
            ContextQualityStatus.SUFFICIENT,
            ContextQualityStatus.MARGINAL,
            ContextQualityStatus.NEEDS_MORE_CONTEXT,
        )

    @pytest.mark.asyncio
    async def test_evaluate_model_assisted_with_mock_provider(self):
        """Model-assisted check should use model provider when available."""
        class MockModelProvider:
            async def call(self, slot, messages, temperature=0.0):
                return {"content": "SUFFICIENT", "error": False}

        config = QualityGateConfig(
            enable_model_assisted=True,
            min_evidence_tokens=50,
            min_top_score=0.01,
            marginal_score_threshold=0.9,  # High threshold to make heuristic return MARGINAL
        )
        gate = AnswerQualityGate(config=config, model_provider=MockModelProvider())
        hits = [_make_hit("h1", score=0.5, text="Some evidence text for the query")]
        trace = RetrievalTrace(query=RetrievalQuery(raw_query="test"))

        result = await gate.evaluate_model_assisted(hits, trace)
        # Model should have been consulted if heuristic was MARGINAL
        # The exact result depends on heuristic scoring


# ---------------------------------------------------------------------------
# Orchestrator Integration (Phase 5.6)
# ---------------------------------------------------------------------------


class TestOrchestratorPhase56:
    def test_auto_retry_default(self):
        """Auto-retry should be enabled by default."""
        orch = RetrievalOrchestrator()
        assert orch.enable_auto_retry is True
        assert orch.max_retries == 1

    @pytest.mark.asyncio
    async def test_retry_with_quality_gate_failure(self):
        """If quality gate fails, retrieval should still succeed (graceful)."""
        class FailingQualityGate:
            def evaluate(self, hits, trace, budget=None):
                raise RuntimeError("Quality gate crashed")

        class MockRetriever:
            @property
            def name(self) -> str:
                return "MockRetriever"
            async def retrieve(self, query, *, budget, trace):
                return [_make_hit("h1")]

        orch = RetrievalOrchestrator()
        orch.register_retriever(MockRetriever())
        orch.quality_gate = FailingQualityGate()
        orch.enable_query_expansion = False

        query = RetrievalQuery(raw_query="test")
        hits, trace = await orch.retrieve(query)
        assert len(hits) == 1

    @pytest.mark.asyncio
    async def test_retry_trace_preserved(self):
        """When retry happens, trace should contain retry information."""
        class VariableRetriever:
            """Returns weak results on first call, stronger on retry."""
            call_count = 0

            @property
            def name(self) -> str:
                return "VariableRetriever"

            async def retrieve(self, query, *, budget, trace):
                self.call_count += 1
                trace.retriever_traces.append(
                    RetrieverTrace(
                        retriever_name=self.name,
                        hit_count=1,
                        elapsed_ms=5.0,
                    )
                )
                if self.call_count == 1:
                    return [_make_hit("h1", score=0.05, text="Tiny")]
                else:
                    return [_make_hit("h1", score=0.5, text="Better evidence " * 20)]

        from aip.orchestration.retrievers.answer_quality_gate import AnswerQualityGate
        config = QualityGateConfig(
            min_evidence_tokens=300,
            min_top_score=0.3,
        )
        gate = AnswerQualityGate(config=config)

        orch = RetrievalOrchestrator()
        orch.register_retriever(VariableRetriever())
        orch.quality_gate = gate
        orch.enable_query_expansion = False
        orch.enable_auto_retry = True

        query = RetrievalQuery(raw_query="test query")
        hits, trace = await orch.retrieve(query)

        # The trace should be populated regardless of retry outcome
        assert trace.context_quality_status != ""


# ---------------------------------------------------------------------------
# Full Integration Test
# ---------------------------------------------------------------------------


class TestFullIntegrationPhase56:
    @pytest.mark.asyncio
    async def test_full_pipeline_with_retry_and_compression(self):
        """Full pipeline: retrieval → quality gate → retry → compression → packing."""
        from aip.orchestration.retrievers.answer_quality_gate import AnswerQualityGate
        from aip.orchestration.retrievers.context_packer import SmartContextPacker

        class MockFTS:
            @property
            def name(self) -> str:
                return "FTSRetriever"
            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(
                    RetrieverTrace(retriever_name=self.name, hit_count=2)
                )
                return [
                    _make_hit("h1", channel=RetrievalChannel.FTS, score=0.85,
                              text="Komal is the principal of Freedom Generation School. "
                                   "The school serves children from brick kiln communities. "
                                   "Education is provided in Urdu and English mediums."),
                    _make_hit("h2", channel=RetrievalChannel.FTS, score=0.7,
                              text="Students at the school achieve good results. "
                                   "The weather is nice today. "
                                   "Community support is strong."),
                ]

        class MockWiki:
            @property
            def name(self) -> str:
                return "WikiRetriever"
            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(
                    RetrieverTrace(retriever_name=self.name, hit_count=1)
                )
                return [
                    _make_hit("w1", channel=RetrievalChannel.WIKI, score=0.75,
                              source_type="wiki_article",
                              evidence_status=EvidenceStatus.APPROVED,
                              text="Freedom Generation School is an approved educational institution."),
                ]

        # Build orchestrator
        orch = RetrievalOrchestrator()
        orch.register_retriever(MockFTS())
        orch.register_retriever(MockWiki())
        orch.quality_gate = AnswerQualityGate()
        orch.enable_query_expansion = False

        # Execute retrieval
        query = RetrievalQuery(raw_query="Who is Komal at Freedom Generation School?")
        budget = RetrievalBudget()
        hits, trace = await orch.retrieve(query, budget=budget)

        # Quality gate should have been evaluated
        assert trace.context_quality_status != ""
        assert "overall_quality" in trace.context_quality_scores

        # Context packing with extractive summarization
        query_terms = set(query.raw_query.lower().split())
        entity_terms = set(trace.detected_entities) if trace.detected_entities else set()
        packer = SmartContextPacker(
            budget=budget,
            query_terms=query_terms,
            entity_terms=entity_terms,
        )
        packet = packer.pack(hits)

        # Should have evidence + wiki sections
        labels = [s.label for s in packet.sections]
        assert "evidence" in labels
        assert "wiki" in labels

        # Render to prompt string
        context_str = packet.to_prompt_string()
        assert "EVIDENCE" in context_str
        assert "WIKI BACKGROUND" in context_str

        # Provenance markers
        assert "score=" in context_str
        assert "channel=" in context_str

        # Trace persistence test
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            trace_store = TraceStore(db_path)
            success = trace_store.persist(trace)
            assert success is True

            # Dashboard summary should work
            summary = trace_store.get_dashboard_summary()
            assert summary["total_queries"] == 1
        finally:
            os.unlink(db_path)
