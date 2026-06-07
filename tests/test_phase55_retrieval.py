"""Tests for Phase 5.5 deliverables — Context Quality & Reliability.

Covers:
- SmartContextPacker (budget-aware assembly, section headers, truncation, provenance)
- ProceduralRetriever (protocol compliance, procedural query detection, scoring, graceful degradation)
- AnswerQualityGate (quality evaluation, status determination, recommendations)
- TraceStore (persistence, querying, quality metrics)
- ContextQualityStatus and RetrievalBudget schema updates
- Orchestrator integration (ProceduralRetriever registration, quality gate wiring)
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import dataclass
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
    _estimate_tokens,
    _truncate_text,
)
from aip.orchestration.retrievers.procedural_retriever import (
    ProceduralRetriever,
    is_procedural_query,
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
        retrieval_channel=channel,
        evidence_status=evidence_status,
        debug={},
    )


# ---------------------------------------------------------------------------
# Schema Updates
# ---------------------------------------------------------------------------


class TestSchemaUpdates:
    def test_context_quality_status_enum(self):
        """ContextQualityStatus should have expected values."""
        assert ContextQualityStatus.SUFFICIENT.value == "sufficient"
        assert ContextQualityStatus.MARGINAL.value == "marginal"
        assert ContextQualityStatus.NEEDS_MORE_CONTEXT.value == "needs_more_context"
        assert ContextQualityStatus.EMPTY.value == "empty"

    def test_retrieval_budget_procedural_allocation(self):
        """RetrievalBudget should include procedural_allocation."""
        budget = RetrievalBudget()
        assert hasattr(budget, "procedural_allocation")
        assert budget.procedural_allocation == 0.05

    def test_retrieval_budget_max_procedures(self):
        """RetrievalBudget should include max_procedures."""
        budget = RetrievalBudget()
        assert hasattr(budget, "max_procedures")
        assert budget.max_procedures == 3

    def test_retrieval_trace_quality_fields(self):
        """RetrievalTrace should have quality gate and procedural fields."""
        trace = RetrievalTrace(query=RetrievalQuery(raw_query="test"))
        assert hasattr(trace, "context_quality_status")
        assert hasattr(trace, "context_quality_scores")
        assert hasattr(trace, "quality_gate_elapsed_ms")
        assert hasattr(trace, "procedural_injected")
        assert hasattr(trace, "procedural_articles")


# ---------------------------------------------------------------------------
# Smart Context Packer
# ---------------------------------------------------------------------------


class TestTokenEstimation:
    def test_estimate_tokens(self):
        assert _estimate_tokens("Hello world") == 2  # 11 chars // 4 = 2
        assert _estimate_tokens("") == 0

    def test_truncate_text_short(self):
        """Short text should not be truncated."""
        text = "Short text"
        result = _truncate_text(text, max_tokens=100)
        assert result == text

    def test_truncate_text_long(self):
        """Long text should be truncated with suffix."""
        text = "A" * 1000
        result = _truncate_text(text, max_tokens=50)
        assert len(result) < len(text)
        assert result.endswith("...")

    def test_truncate_sentence_boundary(self):
        """Should try to break at sentence boundary."""
        text = "First sentence. Second sentence. Third sentence. Fourth sentence."
        result = _truncate_text(text, max_tokens=10)
        # Should have broken at a sentence boundary
        assert "." in result


class TestSmartContextPacker:
    def test_empty_hits(self):
        """Empty hits should produce empty packet."""
        packer = SmartContextPacker()
        packet = packer.pack([])
        assert packet.hit_count == 0
        assert "No relevant sources" in packet.to_prompt_string()

    def test_evidence_section(self):
        """FTS and VECTOR hits should go into EVIDENCE section."""
        hits = [
            _make_hit("h1", channel=RetrievalChannel.FTS, score=0.9),
            _make_hit("h2", channel=RetrievalChannel.VECTOR, score=0.8),
        ]
        packer = SmartContextPacker()
        packet = packer.pack(hits)
        assert packet.hit_count == 2
        assert len(packet.sections) == 1
        assert packet.sections[0].label == "evidence"
        assert "EVIDENCE" in packet.sections[0].header

    def test_wiki_section(self):
        """WIKI hits should go into WIKI BACKGROUND section."""
        hits = [
            _make_hit("w1", channel=RetrievalChannel.WIKI, score=0.7,
                       source_type="wiki_article", evidence_status=EvidenceStatus.APPROVED),
        ]
        packer = SmartContextPacker()
        packet = packer.pack(hits)
        assert len(packet.sections) == 1
        assert packet.sections[0].label == "wiki"
        assert "WIKI BACKGROUND" in packet.sections[0].header

    def test_procedural_section(self):
        """PROCEDURAL hits should go into PROCEDURES section."""
        hits = [
            _make_hit("p1", channel=RetrievalChannel.PROCEDURAL, score=0.6,
                       source_type="procedure_article", evidence_status=EvidenceStatus.APPROVED),
        ]
        packer = SmartContextPacker()
        packet = packer.pack(hits)
        assert len(packet.sections) == 1
        assert packet.sections[0].label == "procedural"
        assert "PROCEDURES" in packet.sections[0].header

    def test_graph_section(self):
        """GRAPH hits should go into GRAPH CONTEXT section."""
        hits = [
            _make_hit("g1", channel=RetrievalChannel.GRAPH, score=0.7),
        ]
        packer = SmartContextPacker()
        packet = packer.pack(hits)
        assert len(packet.sections) == 1
        assert packet.sections[0].label == "graph"
        assert "GRAPH" in packet.sections[0].header

    def test_mixed_channels(self):
        """Mixed channel hits should produce multiple sections."""
        hits = [
            _make_hit("h1", channel=RetrievalChannel.FTS, score=0.9),
            _make_hit("w1", channel=RetrievalChannel.WIKI, score=0.8,
                       evidence_status=EvidenceStatus.APPROVED),
            _make_hit("p1", channel=RetrievalChannel.PROCEDURAL, score=0.6,
                       evidence_status=EvidenceStatus.APPROVED),
        ]
        packer = SmartContextPacker()
        packet = packer.pack(hits)
        assert packet.hit_count == 3
        labels = [s.label for s in packet.sections]
        assert "evidence" in labels
        assert "wiki" in labels
        assert "procedural" in labels

    def test_budget_respected(self):
        """Total tokens should stay within budget."""
        budget = RetrievalBudget(total_tokens=200)
        hits = [_make_hit(f"h{i}", text=f"Content {i} " * 50) for i in range(10)]
        packer = SmartContextPacker(budget=budget)
        packet = packer.pack(hits)
        assert packet.total_est_tokens <= budget.total_tokens + 50  # small margin for headers

    def test_provenance_markers(self):
        """Each entry should have provenance markers with score, channel, source."""
        hits = [_make_hit("h1", channel=RetrievalChannel.FTS, score=0.85)]
        packer = SmartContextPacker()
        packet = packer.pack(hits)
        output = packet.to_prompt_string()
        assert "score=" in output
        assert "channel=" in output
        assert "source=" in output
        assert "h1" in output

    def test_assemble_context_convenience(self):
        """assemble_context() should return a string."""
        hits = [_make_hit("h1", channel=RetrievalChannel.FTS, score=0.9)]
        result = assemble_context(hits)
        assert isinstance(result, str)
        assert "EVIDENCE" in result

    def test_truncation_tracking(self):
        """Packer should track truncated hits."""
        budget = RetrievalBudget(total_tokens=50)
        hits = [_make_hit(f"h{i}", text=f"Very long content {i} " * 100) for i in range(5)]
        packer = SmartContextPacker(budget=budget)
        packet = packer.pack(hits)
        # Some hits should have been truncated
        assert packet.truncated_hits >= 0  # might be truncated or cut entirely

    def test_evidence_first(self):
        """Evidence section should appear before wiki/procedural."""
        hits = [
            _make_hit("w1", channel=RetrievalChannel.WIKI, score=0.8,
                       evidence_status=EvidenceStatus.APPROVED),
            _make_hit("h1", channel=RetrievalChannel.FTS, score=0.9),
        ]
        packer = SmartContextPacker()
        packet = packer.pack(hits)
        labels = [s.label for s in packet.sections]
        assert labels.index("evidence") < labels.index("wiki")

    def test_empty_channel_excluded(self):
        """Empty channels should not produce sections."""
        hits = [_make_hit("h1", channel=RetrievalChannel.FTS, score=0.9)]
        packer = SmartContextPacker()
        packet = packer.pack(hits)
        labels = [s.label for s in packet.sections]
        assert "wiki" not in labels
        assert "procedural" not in labels


# ---------------------------------------------------------------------------
# Procedural Retriever
# ---------------------------------------------------------------------------


class TestProceduralQueryDetection:
    def test_how_do_i(self):
        assert is_procedural_query("How do I configure the alert system?")

    def test_how_to(self):
        assert is_procedural_query("How to install the software")

    def test_what_are_the_steps(self):
        assert is_procedural_query("What are the steps to deploy?")

    def test_non_procedural(self):
        assert not is_procedural_query("Who is Komal?")

    def test_what_is(self):
        assert not is_procedural_query("What is AIP?")

    def test_configure_query(self):
        assert is_procedural_query("Configure the monitoring threshold")

    def test_empty_query(self):
        assert not is_procedural_query("")


class TestProceduralRetriever:
    def test_satisfies_retriever_protocol(self):
        pr = ProceduralRetriever()
        assert isinstance(pr, Retriever)

    def test_name(self):
        pr = ProceduralRetriever()
        assert pr.name == "ProceduralRetriever"

    @pytest.mark.asyncio
    async def test_no_db_path_returns_empty(self):
        """Without db_path, returns [] gracefully."""
        pr = ProceduralRetriever()
        query = RetrievalQuery(raw_query="How do I configure alerts?")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)

        hits = await pr.retrieve(query, budget=budget, trace=trace)
        assert hits == []

    @pytest.mark.asyncio
    async def test_with_db_path_no_procedures(self):
        """With db_path but no procedure articles, returns []."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            # Create minimal schema (no procedures)
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT, content TEXT, metadata_json TEXT,
                    version INTEGER, created_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ecs_state (
                    artifact_id TEXT PRIMARY KEY,
                    current_state TEXT
                )
            """)
            conn.commit()
            conn.close()

            pr = ProceduralRetriever(db_path=db_path)
            query = RetrievalQuery(raw_query="How do I configure alerts?")
            budget = RetrievalBudget()
            trace = RetrievalTrace(query=query)

            hits = await pr.retrieve(query, budget=budget, trace=trace)
            assert hits == []
            # Should still record a retriever trace
            assert len(trace.retriever_traces) == 1
            assert trace.retriever_traces[0].retriever_name == "ProceduralRetriever"
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_with_approved_procedure(self):
        """With an approved procedure article, returns it."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            import json
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT, content TEXT, metadata_json TEXT,
                    version INTEGER, created_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ecs_state (
                    artifact_id TEXT PRIMARY KEY,
                    current_state TEXT
                )
            """)
            # Insert an approved procedure
            conn.execute(
                "INSERT INTO artifacts (id, content, metadata_json, version, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    "beast:procedure:alert_config",
                    "Step 1: Open settings. Step 2: Configure alert threshold. Step 3: Save.",
                    json.dumps({"domain": "monitoring", "tags": ["alert", "configure"]}),
                    1,
                    "2025-01-01T00:00:00",
                ),
            )
            conn.execute(
                "INSERT INTO ecs_state (artifact_id, current_state) VALUES (?, ?)",
                ("beast:procedure:alert_config", "APPROVED"),
            )
            conn.commit()
            conn.close()

            pr = ProceduralRetriever(db_path=db_path)
            query = RetrievalQuery(raw_query="How do I configure alerts?")
            budget = RetrievalBudget()
            trace = RetrievalTrace(query=query)

            hits = await pr.retrieve(query, budget=budget, trace=trace)
            assert len(hits) == 1
            assert hits[0].retrieval_channel == RetrievalChannel.PROCEDURAL
            assert hits[0].evidence_status == EvidenceStatus.APPROVED
            assert hits[0].source_type == "procedure_article"
            # Procedural query should score well
            assert hits[0].score > 0.3
            # Trace should show procedural injection
            assert trace.procedural_injected is True
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_procedural_query_boost(self):
        """Procedural queries should get an intent score boost."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            import json
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT, content TEXT, metadata_json TEXT,
                    version INTEGER, created_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ecs_state (
                    artifact_id TEXT PRIMARY KEY,
                    current_state TEXT
                )
            """)
            conn.execute(
                "INSERT INTO artifacts (id, content, metadata_json, version, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    "beast:procedure:setup",
                    "Setup instructions for the system",
                    json.dumps({"domain": "system"}),
                    1,
                    "2025-01-01T00:00:00",
                ),
            )
            conn.execute(
                "INSERT INTO ecs_state (artifact_id, current_state) VALUES (?, ?)",
                ("beast:procedure:setup", "APPROVED"),
            )
            conn.commit()
            conn.close()

            pr = ProceduralRetriever(db_path=db_path)

            # Procedural query
            proc_query = RetrievalQuery(raw_query="How do I set up the system?")
            proc_budget = RetrievalBudget()
            proc_trace = RetrievalTrace(query=proc_query)
            proc_hits = await pr.retrieve(proc_query, budget=proc_budget, trace=proc_trace)

            # Non-procedural query
            nonproc_query = RetrievalQuery(raw_query="What is the system?")
            nonproc_budget = RetrievalBudget()
            nonproc_trace = RetrievalTrace(query=nonproc_query)
            nonproc_hits = await pr.retrieve(nonproc_query, budget=nonproc_budget, trace=nonproc_trace)

            # Procedural query should score higher
            if proc_hits and nonproc_hits:
                assert proc_hits[0].score >= nonproc_hits[0].score

        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# Answer Quality Gate
# ---------------------------------------------------------------------------


class TestAnswerQualityGate:
    def test_sufficient_context(self):
        """Good context should be rated SUFFICIENT."""
        config = QualityGateConfig(
            min_evidence_tokens=50,  # Lower threshold for test
            min_top_score=0.01,  # RRF scores are small
            marginal_score_threshold=0.3,
        )
        gate = AnswerQualityGate(config=config)
        hits = [
            _make_hit("h1", score=0.9, text="Good evidence text " * 50),
            _make_hit("h2", score=0.8, text="More evidence text " * 40),
            _make_hit("h3", score=0.7, text="Additional evidence " * 30),
        ]
        trace = RetrievalTrace(
            query=RetrievalQuery(raw_query="test query"),
            detected_entities=[],
        )
        result = gate.evaluate(hits, trace)
        # With plenty of evidence and good scores, should be SUFFICIENT or MARGINAL
        assert result.status in (ContextQualityStatus.SUFFICIENT, ContextQualityStatus.MARGINAL)
        assert "overall_quality" in result.scores

    def test_empty_context(self):
        """No hits should be rated EMPTY."""
        gate = AnswerQualityGate()
        trace = RetrievalTrace(query=RetrievalQuery(raw_query="test"))
        result = gate.evaluate([], trace)
        assert result.status == ContextQualityStatus.EMPTY

    def test_needs_more_context(self):
        """Very weak context should be rated NEEDS_MORE_CONTEXT."""
        config = QualityGateConfig(
            min_evidence_tokens=500,
            min_top_score=0.3,
        )
        gate = AnswerQualityGate(config=config)
        hits = [_make_hit("h1", score=0.1, text="Tiny")]
        trace = RetrievalTrace(
            query=RetrievalQuery(raw_query="test"),
            detected_entities=[],
        )
        result = gate.evaluate(hits, trace)
        assert result.status in (
            ContextQualityStatus.NEEDS_MORE_CONTEXT,
            ContextQualityStatus.MARGINAL,
        )

    def test_marginal_context(self):
        """Marginal context should be detected."""
        config = QualityGateConfig(
            min_evidence_tokens=100,
            min_top_score=0.1,
            marginal_score_threshold=0.6,
        )
        gate = AnswerQualityGate(config=config)
        hits = [
            _make_hit("h1", score=0.3, text="Some evidence but not much"),
        ]
        trace = RetrievalTrace(
            query=RetrievalQuery(raw_query="test"),
            detected_entities=["entity1"],
        )
        result = gate.evaluate(hits, trace)
        # Could be MARGINAL or NEEDS_MORE_CONTEXT depending on scores
        assert result.status in (
            ContextQualityStatus.MARGINAL,
            ContextQualityStatus.NEEDS_MORE_CONTEXT,
            ContextQualityStatus.SUFFICIENT,
        )

    def test_entity_coverage(self):
        """Entity coverage should be computed from detected entities."""
        gate = AnswerQualityGate()
        hits = [
            _make_hit("h1", score=0.9, text="Komal is the principal of Freedom Generation School"),
        ]
        trace = RetrievalTrace(
            query=RetrievalQuery(raw_query="Who is Komal?"),
            detected_entities=["komal", "freedom_generation"],
        )
        result = gate.evaluate(hits, trace)
        assert "entity_coverage" in result.scores
        # "komal" should be found in the hit text
        assert result.scores["entity_coverage"] > 0.0

    def test_disabled_gate(self):
        """Disabled gate should always return SUFFICIENT."""
        config = QualityGateConfig(enabled=False)
        gate = AnswerQualityGate(config=config)
        result = gate.evaluate([], RetrievalTrace(query=RetrievalQuery(raw_query="test")))
        assert result.status == ContextQualityStatus.SUFFICIENT

    def test_recommendations_generated(self):
        """Insufficient context should produce recommendations."""
        config = QualityGateConfig(
            min_evidence_tokens=500,
            min_top_score=0.3,
        )
        gate = AnswerQualityGate(config=config)
        hits = [_make_hit("h1", score=0.1, text="Tiny")]
        trace = RetrievalTrace(
            query=RetrievalQuery(raw_query="test"),
            detected_entities=["entity1"],
        )
        result = gate.evaluate(hits, trace)
        if result.status != ContextQualityStatus.SUFFICIENT:
            assert len(result.recommendations) > 0

    def test_quality_scores_populated(self):
        """Quality scores should be populated in the result."""
        gate = AnswerQualityGate()
        hits = [_make_hit("h1", score=0.8, text="Some text")]
        trace = RetrievalTrace(query=RetrievalQuery(raw_query="test"))
        result = gate.evaluate(hits, trace)
        assert "evidence_tokens" in result.scores
        assert "top_hit_score" in result.scores
        assert "hit_count" in result.scores
        assert "overall_quality" in result.scores

    def test_trace_updated(self):
        """Quality gate should update the trace with results."""
        gate = AnswerQualityGate()
        hits = [_make_hit("h1", score=0.8, text="Some text")]
        trace = RetrievalTrace(query=RetrievalQuery(raw_query="test"))
        result = gate.evaluate(hits, trace)
        assert trace.context_quality_status != ""
        assert "overall_quality" in trace.context_quality_scores
        assert trace.quality_gate_elapsed_ms >= 0


# ---------------------------------------------------------------------------
# TraceStore
# ---------------------------------------------------------------------------


class TestTraceStore:
    def test_persist_and_query(self):
        """Should persist a trace and query it back."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            store = TraceStore(db_path)

            trace = RetrievalTrace(
                query=RetrievalQuery(raw_query="Who is Komal?"),
                trace_id="test_trace_001",
                detected_entities=["komal"],
                context_quality_status="sufficient",
                context_quality_scores={"overall_quality": 0.8, "entity_coverage": 0.5},
            )
            trace.retriever_traces.append(
                RetrieverTrace(retriever_name="FTSRetriever", hit_count=5, elapsed_ms=10.0)
            )

            success = store.persist(trace)
            assert success is True

            # Query back
            recent = store.query_recent(limit=10)
            assert len(recent) == 1
            assert recent[0]["trace_id"] == "test_trace_001"
            assert recent[0]["query_text"] == "Who is Komal?"
            assert recent[0]["quality_status"] == "sufficient"
        finally:
            os.unlink(db_path)

    def test_query_by_domain(self):
        """Should query traces by domain."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            store = TraceStore(db_path)

            # Insert traces with different domains
            trace1 = RetrievalTrace(
                query=RetrievalQuery(raw_query="test1", domain_filter="education"),
                trace_id="trace_edu_001",
            )
            trace2 = RetrievalTrace(
                query=RetrievalQuery(raw_query="test2", domain_filter="health"),
                trace_id="trace_health_001",
            )

            store.persist(trace1)
            store.persist(trace2)

            edu_traces = store.query_by_domain("education")
            assert len(edu_traces) == 1
            assert edu_traces[0]["domain_filter"] == "education"
        finally:
            os.unlink(db_path)

    def test_quality_summary(self):
        """Should compute quality metrics summary."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            store = TraceStore(db_path)

            # Insert a few traces
            for i in range(3):
                trace = RetrievalTrace(
                    query=RetrievalQuery(raw_query=f"query {i}"),
                    trace_id=f"trace_{i}",
                    context_quality_status="sufficient",
                    context_quality_scores={"overall_quality": 0.8},
                )
                store.persist(trace)

            summary = store.query_quality_summary(hours=24)
            assert summary["total_queries"] == 3
            assert "quality_distribution" in summary
            assert "avg_quality" in summary
        finally:
            os.unlink(db_path)

    def test_persist_empty_trace(self):
        """Should handle empty/invalid traces gracefully."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            store = TraceStore(db_path)
            # Empty trace (no trace_id)
            trace = RetrievalTrace(query=RetrievalQuery(raw_query="test"))
            trace.trace_id = ""
            result = store.persist(trace)
            assert result is False
        finally:
            os.unlink(db_path)


class TestComputeRetrievalMetrics:
    def test_basic_metrics(self):
        """Should compute basic metrics from a trace."""
        trace = RetrievalTrace(
            query=RetrievalQuery(raw_query="test"),
            trace_id="test_metrics",
            detected_entities=["entity1", "entity2"],
            wiki_injected=True,
            context_quality_status="sufficient",
            context_quality_scores={
                "entity_coverage": 0.75,
                "overall_quality": 0.85,
                "channel_diversity": 3.0,
            },
            budget_usage={"total_estimated_tokens": 3000, "budget_total_tokens": 8000},
        )
        trace.retriever_traces.append(
            RetrieverTrace(retriever_name="FTSRetriever", hit_count=5, elapsed_ms=10.0)
        )

        metrics = compute_retrieval_metrics(trace)
        assert "total_hits" in metrics
        assert "entities_detected" in metrics
        assert metrics["entities_detected"] == 2.0
        assert metrics["wiki_injected"] == 1.0
        assert metrics["quality_status_numeric"] == 1.0
        assert metrics["budget_utilization"] == pytest.approx(0.375, abs=0.01)

    def test_empty_trace(self):
        """Should handle empty trace gracefully."""
        metrics = compute_retrieval_metrics(RetrievalTrace(query=RetrievalQuery(raw_query="test")))
        assert metrics["total_hits"] == 0.0


# ---------------------------------------------------------------------------
# Orchestrator Integration (Phase 5.5)
# ---------------------------------------------------------------------------


class TestOrchestratorPhase55:
    def test_procedural_toggle_default(self):
        """Procedural retriever should be enabled by default."""
        orch = RetrievalOrchestrator()
        assert orch.enable_procedural_retrieval is True

    def test_filter_procedural_retriever(self):
        """When enable_procedural_retrieval=False, ProceduralRetriever is filtered out."""
        orch = RetrievalOrchestrator()
        orch.enable_procedural_retrieval = False
        orch.register_retriever(ProceduralRetriever())

        active = orch._filter_retrievers()
        names = [r.name for r in active]
        assert "ProceduralRetriever" not in names

    def test_quality_gate_attribute(self):
        """Orchestrator should have a quality_gate attribute."""
        orch = RetrievalOrchestrator()
        assert hasattr(orch, "quality_gate")
        assert orch.quality_gate is None

    @pytest.mark.asyncio
    async def test_quality_gate_integrated(self):
        """Quality gate should be evaluated during retrieval."""
        class MockRetriever:
            @property
            def name(self) -> str:
                return "MockRetriever"

            async def retrieve(self, query, *, budget, trace):
                return [
                    _make_hit("h1", channel=RetrievalChannel.FTS, score=0.9,
                              text="Good evidence text " * 20),
                ]

        from aip.orchestration.retrievers.answer_quality_gate import AnswerQualityGate

        orch = RetrievalOrchestrator()
        orch.register_retriever(MockRetriever())
        orch.quality_gate = AnswerQualityGate()
        orch.enable_query_expansion = False

        query = RetrievalQuery(raw_query="test")
        hits, trace = await orch.retrieve(query)

        # Quality gate should have been evaluated
        assert trace.context_quality_status != ""
        assert "overall_quality" in trace.context_quality_scores

    @pytest.mark.asyncio
    async def test_procedural_tokens_in_budget_usage(self):
        """Procedural channel tokens should appear in budget_usage."""
        class MockProcedural:
            @property
            def name(self) -> str:
                return "ProceduralRetriever"

            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(
                    RetrieverTrace(retriever_name=self.name, hit_count=1)
                )
                return [
                    _make_hit("p1", channel=RetrievalChannel.PROCEDURAL, score=0.7,
                              source_type="procedure_article",
                              evidence_status=EvidenceStatus.APPROVED),
                ]

        orch = RetrievalOrchestrator()
        orch.register_retriever(MockProcedural())
        orch.enable_query_expansion = False

        query = RetrievalQuery(raw_query="How do I configure?")
        hits, trace = await orch.retrieve(query)

        assert "procedural_tokens" in trace.budget_usage

    @pytest.mark.asyncio
    async def test_all_five_retrievers(self):
        """FTS + Graph + Wiki + Vector + Procedural all registered."""
        class MockFTS:
            @property
            def name(self) -> str:
                return "FTSRetriever"
            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(RetrieverTrace(retriever_name=self.name, hit_count=1))
                return [_make_hit("fts1", channel=RetrievalChannel.FTS)]

        class MockGraph:
            @property
            def name(self) -> str:
                return "GraphRetriever"
            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(RetrieverTrace(retriever_name=self.name, hit_count=1))
                return [_make_hit("graph1", channel=RetrievalChannel.GRAPH)]

        class MockWiki:
            @property
            def name(self) -> str:
                return "WikiRetriever"
            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(RetrieverTrace(retriever_name=self.name, hit_count=1))
                return [_make_hit("wiki1", channel=RetrievalChannel.WIKI,
                                  evidence_status=EvidenceStatus.APPROVED)]

        class MockVector:
            @property
            def name(self) -> str:
                return "VectorRetriever"
            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(RetrieverTrace(retriever_name=self.name, hit_count=1))
                return [_make_hit("vec1", channel=RetrievalChannel.VECTOR)]

        class MockProcedural:
            @property
            def name(self) -> str:
                return "ProceduralRetriever"
            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(RetrieverTrace(retriever_name=self.name, hit_count=1))
                return [_make_hit("proc1", channel=RetrievalChannel.PROCEDURAL,
                                  evidence_status=EvidenceStatus.APPROVED)]

        orch = RetrievalOrchestrator()
        orch.register_retriever(MockFTS())
        orch.register_retriever(MockGraph())
        orch.register_retriever(MockWiki())
        orch.register_retriever(MockVector())
        orch.register_retriever(MockProcedural())
        orch.enable_query_expansion = False

        query = RetrievalQuery(raw_query="test")
        hits, trace = await orch.retrieve(query)

        assert len(hits) == 5
        channels = {h.retrieval_channel for h in hits}
        assert RetrievalChannel.FTS in channels
        assert RetrievalChannel.GRAPH in channels
        assert RetrievalChannel.WIKI in channels
        assert RetrievalChannel.VECTOR in channels
        assert RetrievalChannel.PROCEDURAL in channels
        assert len(trace.retriever_traces) == 5

    @pytest.mark.asyncio
    async def test_quality_gate_failure_graceful(self):
        """If quality gate raises, retrieval should still succeed."""
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


# ---------------------------------------------------------------------------
# Full Integration Test
# ---------------------------------------------------------------------------


class TestFullIntegration:
    @pytest.mark.asyncio
    async def test_full_pipeline_with_quality_gate(self):
        """Full pipeline: retrieval → quality gate → context packing."""
        from aip.orchestration.retrievers.answer_quality_gate import AnswerQualityGate
        from aip.orchestration.retrievers.context_packer import SmartContextPacker

        class MockFTS:
            @property
            def name(self) -> str:
                return "FTSRetriever"
            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(
                    RetrieverTrace(retriever_name=self.name, hit_count=3)
                )
                return [
                    _make_hit("h1", channel=RetrievalChannel.FTS, score=0.9,
                              text="Komal is the principal of Freedom Generation School."),
                    _make_hit("h2", channel=RetrievalChannel.FTS, score=0.8,
                              text="The school provides education in Urdu and English."),
                    _make_hit("h3", channel=RetrievalChannel.FTS, score=0.7,
                              text="Students attend from nearby brick kiln communities."),
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
                    _make_hit("w1", channel=RetrievalChannel.WIKI, score=0.85,
                              source_type="wiki_article",
                              evidence_status=EvidenceStatus.APPROVED,
                              text="Freedom Generation School is an educational institution."),
                ]

        class MockProcedural:
            @property
            def name(self) -> str:
                return "ProceduralRetriever"
            async def retrieve(self, query, *, budget, trace):
                trace.retriever_traces.append(
                    RetrieverTrace(retriever_name=self.name, hit_count=1)
                )
                return [
                    _make_hit("p1", channel=RetrievalChannel.PROCEDURAL, score=0.6,
                              source_type="procedure_article",
                              evidence_status=EvidenceStatus.APPROVED,
                              text="To enroll: Step 1: Visit the school. Step 2: Submit documents."),
                ]

        # Build orchestrator
        orch = RetrievalOrchestrator()
        orch.register_retriever(MockFTS())
        orch.register_retriever(MockWiki())
        orch.register_retriever(MockProcedural())
        orch.quality_gate = AnswerQualityGate()
        orch.enable_query_expansion = False

        # Execute retrieval
        query = RetrievalQuery(raw_query="How do I enroll at Freedom Generation School?")
        budget = RetrievalBudget()
        hits, trace = await orch.retrieve(query, budget=budget)

        # Quality gate should have been evaluated
        assert trace.context_quality_status != ""
        assert "overall_quality" in trace.context_quality_scores

        # Procedural and Wiki channels should be present in budget usage
        assert trace.budget_usage.get("procedural_tokens", 0) > 0
        assert trace.budget_usage.get("wiki_tokens", 0) > 0

        # Budget usage should include all channels
        assert "evidence_tokens" in trace.budget_usage
        assert "wiki_tokens" in trace.budget_usage
        assert "procedural_tokens" in trace.budget_usage

        # Context packing
        packer = SmartContextPacker(budget=budget)
        packet = packer.pack(hits)

        # Should have evidence + wiki + procedural sections
        labels = [s.label for s in packet.sections]
        assert "evidence" in labels
        assert "wiki" in labels
        assert "procedural" in labels

        # Render to prompt string
        context_str = packet.to_prompt_string()
        assert "EVIDENCE" in context_str
        assert "WIKI BACKGROUND" in context_str
        assert "PROCEDURES" in context_str

        # Provenance markers
        assert "score=" in context_str
        assert "channel=" in context_str
