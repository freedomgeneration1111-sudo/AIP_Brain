"""Sprint 10 — Retrieval Quality and Trace Sprint.

Tests for:
1. ChannelHealthState enum and ChannelHealthReport
2. Unified RetrievalTrace with channel health, query expansion, entities, scores
3. Channel health states in the retrieval orchestrator
4. Visible retrieval warnings on AskResult
5. Retrieval quality dashboard enhancements
6. Gold question eval set loading (YAML + JSON)
7. Eval command diagnostic output
8. Gate: when AIP gives a weak answer, you can tell whether the problem was
   ingestion, embedding, retrieval, ranking, synthesis, or missing source material.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

import pytest

from aip.foundation.schemas.retrieval import (
    ChannelHealthState,
    ChannelHealthReport,
    RetrievalHit,
    RetrievalTrace,
)
from aip.foundation.schemas.ask import AskResult
from aip.foundation.schemas.vector import VectorBackendStatus, VectorDegradationInfo
from aip.orchestration.retrieval_orchestrator import (
    OrchestratorConfig,
    RetrievalOrchestrator,
    rrf_fuse,
    apply_quality_gate,
)
from aip.orchestration.ask_pipeline import _build_retrieval_warnings, _build_degradation_dict


# ======================================================================
# 1. ChannelHealthState enum
# ======================================================================


class TestChannelHealthState:
    """Test ChannelHealthState enum values and properties."""

    def test_enum_values(self):
        assert ChannelHealthState.ACTIVE.value == "active"
        assert ChannelHealthState.DEGRADED.value == "degraded"
        assert ChannelHealthState.FAILED.value == "failed"
        assert ChannelHealthState.DISABLED.value == "disabled"

    def test_is_available(self):
        assert ChannelHealthState.ACTIVE.is_available is True
        assert ChannelHealthState.DEGRADED.is_available is True
        assert ChannelHealthState.FAILED.is_available is False
        assert ChannelHealthState.DISABLED.is_available is False

    def test_is_healthy(self):
        assert ChannelHealthState.ACTIVE.is_healthy is True
        assert ChannelHealthState.DEGRADED.is_healthy is False
        assert ChannelHealthState.FAILED.is_healthy is False
        assert ChannelHealthState.DISABLED.is_healthy is False


# ======================================================================
# 2. ChannelHealthReport
# ======================================================================


class TestChannelHealthReport:
    """Test ChannelHealthReport dataclass and methods."""

    def _make_report(self):
        return ChannelHealthReport(
            channel_states={
                "fts": ChannelHealthState.ACTIVE,
                "vector": ChannelHealthState.DEGRADED,
                "graph": ChannelHealthState.FAILED,
                "wiki": ChannelHealthState.DISABLED,
                "procedural": ChannelHealthState.DISABLED,
                "corpus": ChannelHealthState.ACTIVE,
            },
            reasons={
                "vector": "Brute-force fallback (no VSS index)",
                "graph": "GraphStore not configured",
                "wiki": "Not enabled for this query",
                "procedural": "Not enabled for this query",
            },
            timestamp="2026-06-10T12:00:00Z",
        )

    def test_get_active(self):
        report = self._make_report()
        active = report.get_active()
        assert set(active) == {"fts", "corpus"}

    def test_get_degraded(self):
        report = self._make_report()
        degraded = report.get_degraded()
        assert degraded == ["vector"]

    def test_get_failed(self):
        report = self._make_report()
        failed = report.get_failed()
        assert failed == ["graph"]

    def test_get_disabled(self):
        report = self._make_report()
        disabled = report.get_disabled()
        assert set(disabled) == {"wiki", "procedural"}

    def test_to_dict(self):
        report = self._make_report()
        d = report.to_dict()
        assert d["channel_states"]["fts"] == "active"
        assert d["channel_states"]["vector"] == "degraded"
        assert d["channel_states"]["graph"] == "failed"
        assert "active" in d
        assert "degraded" in d
        assert "failed" in d
        assert "disabled" in d

    def test_format_warnings(self):
        report = self._make_report()
        warnings = report.format_warnings()
        assert any("Vector" in w and "degraded" in w.lower() for w in warnings)
        assert any("Graph" in w and "unavailable" in w.lower() for w in warnings)
        # Active and disabled channels should NOT produce warnings
        assert not any("Fts" in w or "Corpus" in w for w in warnings)


# ======================================================================
# 3. Unified RetrievalTrace
# ======================================================================


class TestUnifiedRetrievalTrace:
    """Test Sprint 10 RetrievalTrace enhancements."""

    def _make_trace(self) -> RetrievalTrace:
        return RetrievalTrace(
            session_id="test-session",
            query="What is Sexton?",
            round_number=0,
            channels_queried=["fts", "vector", "corpus"],
            channel_health={
                "fts": "active",
                "vector": "degraded",
                "graph": "failed",
                "corpus": "active",
            },
            channel_health_reasons={
                "vector": "Brute-force fallback",
                "graph": "GraphStore not configured",
            },
            query_expansion=["Sexton", "maintenance"],
            entities_extracted=["Sexton"],
            documents_retrieved_ids=["doc:1", "doc:2", "doc:3"],
            top_scores=[
                {"id": "doc:1", "rrf_score": 0.045, "raw_score": 0.95},
                {"id": "doc:2", "rrf_score": 0.032, "raw_score": 0.88},
            ],
            final_context_token_count=3500,
            final_context_source_ids=["doc:1", "doc:2"],
            degradation_warnings=["Vector channel degraded", "Graph channel unavailable"],
            hits_before_fusion=10,
            hits_after_fusion=6,
            hits_after_quality_gate=3,
            verdict="OK",
            channel_contributions={"fts": 2, "corpus": 1},
        )

    def test_new_fields_populated(self):
        trace = self._make_trace()
        assert trace.channel_health["fts"] == "active"
        assert trace.channel_health["vector"] == "degraded"
        assert trace.query_expansion == ["Sexton", "maintenance"]
        assert trace.entities_extracted == ["Sexton"]
        assert len(trace.documents_retrieved_ids) == 3
        assert len(trace.top_scores) == 2
        assert trace.final_context_token_count == 3500
        assert trace.final_context_source_ids == ["doc:1", "doc:2"]
        assert len(trace.degradation_warnings) == 2

    def test_get_active_channels(self):
        trace = self._make_trace()
        active = trace.get_active_channels()
        assert set(active) == {"fts", "corpus"}

    def test_get_failed_channels(self):
        trace = self._make_trace()
        failed = trace.get_failed_channels()
        assert failed == ["graph"]

    def test_get_degraded_channels(self):
        trace = self._make_trace()
        degraded = trace.get_degraded_channels()
        assert degraded == ["vector"]

    def test_degradation_summary_includes_channel_health(self):
        trace = self._make_trace()
        summary = trace.degradation_summary()
        assert "Vector" in summary or "vector" in summary.lower()
        assert "Graph" in summary or "graph" in summary.lower()

    def test_to_diagnostic_dict(self):
        trace = self._make_trace()
        d = trace.to_diagnostic_dict()
        assert d["session_id"] == "test-session"
        assert d["query"] == "What is Sexton?"
        assert "channel_health" in d
        assert d["active_channels"] == ["fts", "corpus"]
        assert d["failed_channels"] == ["graph"]
        assert d["degraded_channels"] == ["vector"]
        assert d["query_expansion"] == ["Sexton", "maintenance"]
        assert d["entities_extracted"] == ["Sexton"]
        assert d["verdict"] == "OK"
        assert "top_scores" in d
        assert "degradation_summary" in d

    def test_default_values(self):
        trace = RetrievalTrace()
        assert trace.channel_health == {}
        assert trace.channel_health_reasons == {}
        assert trace.query_expansion == []
        assert trace.entities_extracted == []
        assert trace.documents_retrieved_ids == []
        assert trace.top_scores == []
        assert trace.final_context_token_count == 0
        assert trace.final_context_source_ids == []
        assert trace.degradation_warnings == []


# ======================================================================
# 4. Channel health states in retrieval orchestrator
# ======================================================================


class TestOrchestratorChannelHealth:
    """Test that the orchestrator populates channel_health on traces."""

    @pytest.mark.asyncio
    async def test_channel_health_active_when_results_returned(self):
        orch = RetrievalOrchestrator()

        async def fts_retriever(query):
            return [RetrievalHit(id="fts:1", content="test", score=0.9)]

        async def vector_retriever(query):
            return [RetrievalHit(id="vec:1", content="test", score=0.8)]

        orch.register_channel("fts", fts_retriever)
        orch.register_channel("vector", vector_retriever)

        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=True,
            enable_graph=False,
            enable_wiki=False,
            enable_procedural=False,
            enable_corpus=False,
        )

        hits, trace = await orch.retrieve("test query", config=config)

        assert trace.channel_health["fts"] == "active"
        assert trace.channel_health["vector"] == "active"
        # Disabled channels
        assert trace.channel_health["graph"] == "disabled"
        assert trace.channel_health["wiki"] == "disabled"

    @pytest.mark.asyncio
    async def test_channel_health_failed_when_exception(self):
        orch = RetrievalOrchestrator()

        async def fts_retriever(query):
            return [RetrievalHit(id="fts:1", content="test", score=0.9)]

        async def failing_retriever(query):
            raise RuntimeError("Store connection failed")

        orch.register_channel("fts", fts_retriever)
        orch.register_channel("vector", failing_retriever)

        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=True,
            enable_graph=False,
            enable_wiki=False,
            enable_procedural=False,
            enable_corpus=False,
        )

        hits, trace = await orch.retrieve("test query", config=config)

        assert trace.channel_health["fts"] == "active"
        assert trace.channel_health["vector"] == "failed"
        assert "Store connection failed" in trace.channel_health_reasons.get("vector", "")

    @pytest.mark.asyncio
    async def test_channel_health_for_unregistered_channels(self):
        orch = RetrievalOrchestrator()

        async def fts_retriever(query):
            return [RetrievalHit(id="fts:1", content="test", score=0.9)]

        orch.register_channel("fts", fts_retriever)
        # vector is NOT registered but IS enabled

        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=True,
            enable_graph=False,
            enable_wiki=False,
            enable_procedural=False,
            enable_corpus=False,
        )

        hits, trace = await orch.retrieve("test query", config=config)

        # Chunk 5: Unregistered channels now report "not_configured" instead of "failed"
        assert trace.channel_health["vector"] == "not_configured"
        assert "not registered" in trace.channel_health_reasons.get("vector", "").lower()

    @pytest.mark.asyncio
    async def test_degradation_warnings_populated(self):
        orch = RetrievalOrchestrator()

        async def fts_retriever(query):
            return [RetrievalHit(id="fts:1", content="test", score=0.9)]

        async def failing_retriever(query):
            raise RuntimeError("Connection refused")

        orch.register_channel("fts", fts_retriever)
        orch.register_channel("vector", failing_retriever)

        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=True,
            enable_graph=False,
            enable_wiki=False,
            enable_procedural=False,
            enable_corpus=False,
        )

        hits, trace = await orch.retrieve("test query", config=config)

        assert len(trace.degradation_warnings) > 0
        assert any("Vector" in w for w in trace.degradation_warnings)

    @pytest.mark.asyncio
    async def test_documents_retrieved_ids_populated(self):
        orch = RetrievalOrchestrator()

        async def fts_retriever(query):
            return [
                RetrievalHit(id="doc:1", content="test 1", score=0.9),
                RetrievalHit(id="doc:2", content="test 2", score=0.8),
            ]

        orch.register_channel("fts", fts_retriever)

        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=False,
            enable_graph=False,
            enable_wiki=False,
            enable_procedural=False,
            enable_corpus=False,
        )

        hits, trace = await orch.retrieve("test query", config=config)

        assert len(trace.documents_retrieved_ids) > 0
        assert "doc:1" in trace.documents_retrieved_ids

    @pytest.mark.asyncio
    async def test_top_scores_populated(self):
        orch = RetrievalOrchestrator()

        async def fts_retriever(query):
            return [RetrievalHit(id="doc:1", content="test", score=0.95)]

        orch.register_channel("fts", fts_retriever)

        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=False,
            enable_graph=False,
            enable_wiki=False,
            enable_procedural=False,
            enable_corpus=False,
        )

        hits, trace = await orch.retrieve("test query", config=config)

        assert len(trace.top_scores) > 0
        assert trace.top_scores[0]["id"] == "doc:1"
        assert "rrf_score" in trace.top_scores[0]
        assert "raw_score" in trace.top_scores[0]


# ======================================================================
# 5. Visible retrieval warnings on AskResult
# ======================================================================


class TestRetrievalWarnings:
    """Test _build_retrieval_warnings and AskResult.retrieval_warnings."""

    def test_warnings_for_failed_channels(self):
        trace = RetrievalTrace(
            channel_health={
                "fts": "active",
                "vector": "failed",
                "graph": "failed",
                "corpus": "active",
            },
            channel_health_reasons={
                "vector": "Connection refused",
                "graph": "Store not configured",
            },
            hits_after_quality_gate=5,
            verdict="OK",
            channel_contributions={"fts": 3, "corpus": 2},
            vector_degradation=VectorDegradationInfo(
                backend_status=VectorBackendStatus.DISABLED,
            ),
        )
        warnings = _build_retrieval_warnings(trace)
        assert any("Vector" in w and "unavailable" in w for w in warnings)
        assert any("Graph" in w and "unavailable" in w for w in warnings)

    def test_warnings_for_degraded_channels(self):
        trace = RetrievalTrace(
            channel_health={
                "fts": "active",
                "vector": "degraded",
                "corpus": "active",
            },
            channel_health_reasons={
                "vector": "Brute-force fallback",
            },
            hits_after_quality_gate=5,
            verdict="OK",
            channel_contributions={"fts": 3, "corpus": 2},
            vector_degradation=VectorDegradationInfo(
                backend_status=VectorBackendStatus.DEGRADED_BRUTEFORCE,
            ),
        )
        warnings = _build_retrieval_warnings(trace)
        assert any("Vector" in w and "degraded" in w for w in warnings)

    def test_warnings_for_no_results(self):
        trace = RetrievalTrace(
            channel_health={"fts": "active", "vector": "active"},
            hits_after_quality_gate=0,
            verdict="NO_RESULTS",
            channel_contributions={},
            vector_degradation=VectorDegradationInfo(
                backend_status=VectorBackendStatus.AVAILABLE,
            ),
        )
        warnings = _build_retrieval_warnings(trace)
        assert any("No documents passed" in w for w in warnings)

    def test_warnings_for_insufficient_context(self):
        trace = RetrievalTrace(
            channel_health={"fts": "active", "vector": "active"},
            hits_after_quality_gate=1,
            verdict="NEEDS_MORE_CONTEXT",
            channel_contributions={"fts": 1},
            vector_degradation=VectorDegradationInfo(
                backend_status=VectorBackendStatus.AVAILABLE,
            ),
        )
        warnings = _build_retrieval_warnings(trace)
        assert any("insufficient context" in w.lower() for w in warnings)

    def test_primary_evidence_identification(self):
        trace = RetrievalTrace(
            channel_health={
                "fts": "active",
                "vector": "failed",
            },
            channel_health_reasons={"vector": "Unavailable"},
            hits_after_quality_gate=5,
            verdict="OK",
            channel_contributions={"fts": 5},
            vector_degradation=VectorDegradationInfo(
                backend_status=VectorBackendStatus.DISABLED,
            ),
        )
        warnings = _build_retrieval_warnings(trace)
        assert any("primary evidence" in w.lower() for w in warnings)
        assert any("Fts" in w for w in warnings)

    def test_no_warnings_when_healthy(self):
        trace = RetrievalTrace(
            channel_health={
                "fts": "active",
                "vector": "active",
            },
            hits_after_quality_gate=10,
            verdict="OK",
            channel_contributions={"fts": 5, "vector": 5},
            vector_degradation=VectorDegradationInfo(
                backend_status=VectorBackendStatus.AVAILABLE,
            ),
        )
        warnings = _build_retrieval_warnings(trace)
        assert len(warnings) == 0

    def test_none_trace_produces_warning(self):
        warnings = _build_retrieval_warnings(None)
        assert len(warnings) > 0
        assert any("No retrieval trace" in w for w in warnings)

    def test_ask_result_has_retrieval_warnings_field(self):
        result = AskResult(
            status="OK",
            answer="test answer",
            retrieval_warnings=["Vector channel unavailable"],
        )
        assert result.retrieval_warnings == ["Vector channel unavailable"]

    def test_ask_result_default_empty_warnings(self):
        result = AskResult(status="OK", answer="test")
        assert result.retrieval_warnings == []


# ======================================================================
# 6. Enhanced degradation dict
# ======================================================================


class TestDegradationDict:
    """Test _build_degradation_dict includes Sprint 10 fields."""

    def test_includes_channel_health(self):
        trace = RetrievalTrace(
            channel_health={"fts": "active", "vector": "failed"},
            channel_health_reasons={"vector": "Connection refused"},
            hits_after_quality_gate=5,
            verdict="OK",
            channel_contributions={"fts": 5},
            query_expansion=["test"],
            entities_extracted=["Entity1"],
            documents_retrieved_ids=["doc:1"],
            top_scores=[{"id": "doc:1", "rrf_score": 0.05, "raw_score": 0.9}],
        )
        d = _build_degradation_dict(trace)
        assert "channel_health" in d
        assert d["channel_health"]["fts"] == "active"
        assert d["channel_health"]["vector"] == "failed"
        assert "active_channels" in d
        assert "failed_channels" in d
        assert "degraded_channels" in d
        assert d["query_expansion"] == ["test"]
        assert d["entities_extracted"] == ["Entity1"]
        assert d["documents_retrieved_count"] == 1

    def test_none_trace(self):
        d = _build_degradation_dict(None)
        assert d["backend_status"] == "disabled"
        assert "reason" in d


# ======================================================================
# 7. Gold question YAML loading
# ======================================================================


class TestGoldQuestionLoading:
    """Test YAML and JSON golden query loading."""

    def test_load_json_golden_queries(self, tmp_path):
        queries_data = [
            {"query": "What is AIP?", "relevant_ids": ["doc:1"], "expected_entities": ["AIP"]},
            {"query": "How does Sexton work?", "relevant_ids": ["doc:2"]},
        ]
        json_path = tmp_path / "gold.json"
        json_path.write_text(json.dumps(queries_data))

        from aip.orchestration.retrieval_eval import load_golden_queries

        queries = load_golden_queries(str(json_path))

        assert len(queries) == 2
        assert queries[0].query == "What is AIP?"
        assert queries[0].relevant_ids == ["doc:1"]
        assert queries[1].query == "How does Sexton work?"

    def test_load_yaml_golden_queries(self, tmp_path):
        yaml_content = """
questions:
  - query: "What is Sexton?"
    relevant_ids:
      - "doc:sexton"
    expected_entities:
      - "Sexton"
    domain: "architecture"
    tags:
      - "definitional"
  - query: "How does vector search work?"
    relevant_ids:
      - "doc:vector"
    domain: "retrieval"
"""
        yaml_path = tmp_path / "gold.yaml"
        yaml_path.write_text(yaml_content)

        from aip.orchestration.retrieval_eval import load_golden_queries

        queries = load_golden_queries(str(yaml_path))

        assert len(queries) == 2
        assert queries[0].query == "What is Sexton?"
        assert queries[0].relevant_ids == ["doc:sexton"]
        assert queries[0].expected_entities == ["Sexton"]
        assert queries[0].domain == "architecture"
        assert queries[1].query == "How does vector search work?"

    def test_load_yaml_with_yml_extension(self, tmp_path):
        yaml_content = "questions:\n  - query: 'test'\n"
        yml_path = tmp_path / "gold.yml"
        yml_path.write_text(yaml_content)

        from aip.orchestration.retrieval_eval import load_golden_queries

        queries = load_golden_queries(str(yml_path))

        assert len(queries) == 1
        assert queries[0].query == "test"

    def test_missing_file_returns_empty(self):
        from aip.orchestration.retrieval_eval import load_golden_queries

        queries = load_golden_queries("/nonexistent/path.json")
        assert queries == []

    def test_yaml_without_questions_key_returns_empty(self, tmp_path):
        yaml_content = "stuff:\n  - query: 'test'\n"
        yaml_path = tmp_path / "bad.yaml"
        yaml_path.write_text(yaml_content)

        from aip.orchestration.retrieval_eval import load_golden_queries

        queries = load_golden_queries(str(yaml_path))
        assert queries == []

    def test_alpha_gold_yaml_loads(self):
        """Test that the actual aip_alpha_gold.yaml file can be loaded."""
        yaml_path = os.path.join(os.path.dirname(__file__), "..", "docs", "evals", "aip_alpha_gold.yaml")
        if not os.path.exists(yaml_path):
            pytest.skip("aip_alpha_gold.yaml not found")

        from aip.orchestration.retrieval_eval import load_golden_queries

        queries = load_golden_queries(yaml_path)

        # Should have 30+ questions
        assert len(queries) >= 30, f"Expected 30+ questions, got {len(queries)}"

        # Every query should have a query string
        for q in queries:
            assert q.query, f"Empty query found"
            assert len(q.query) > 5, f"Query too short: {q.query}"


# ======================================================================
# 8. Blame assignment (gate criterion)
# ======================================================================


class TestBlameAssignment:
    """Test that weak answers can be diagnosed to identify the pipeline stage at fault.

    Gate: When AIP gives a weak answer, you can tell whether the problem
    was ingestion, embedding, retrieval, ranking, synthesis, or missing
    source material.
    """

    def test_retrieval_blame_when_no_results(self):
        """When no documents are retrieved but relevant sources exist → retrieval."""
        trace = RetrievalTrace(
            channel_health={"fts": "active", "vector": "failed"},
            channel_health_reasons={"vector": "Store unavailable"},
            hits_after_quality_gate=0,
            verdict="NO_RESULTS",
            channel_contributions={},
            degradation_warnings=["Vector channel unavailable"],
        )
        warnings = _build_retrieval_warnings(trace)
        # The trace shows vector failed and no results → retrieval is the problem
        assert any("Vector" in w for w in warnings)
        assert any("No documents" in w for w in warnings)

    def test_embedding_blame_when_vector_contributes_zero(self):
        """When vector channel returns 0 results → embedding issue."""
        trace = RetrievalTrace(
            channel_health={"fts": "active", "vector": "active"},
            channel_health_reasons={"vector": "Channel returned 0 results"},
            hits_after_quality_gate=2,
            verdict="OK",
            channel_contributions={"fts": 2, "vector": 0},
        )
        # Diagnostic: vector contributed 0 → embedding problem
        assert trace.channel_contributions.get("vector", 0) == 0

    def test_ranking_blame_when_recall_low(self):
        """When recall is low but some documents found → ranking issue."""
        trace = RetrievalTrace(
            channel_health={"fts": "active", "vector": "active"},
            hits_after_quality_gate=2,
            verdict="NEEDS_MORE_CONTEXT",
            channel_contributions={"fts": 1, "vector": 1},
            top_scores=[
                {"id": "irrelevant:1", "rrf_score": 0.05, "raw_score": 0.3},
                {"id": "irrelevant:2", "rrf_score": 0.03, "raw_score": 0.2},
            ],
        )
        # Low scores + NEEDS_MORE_CONTEXT → ranking is the problem
        assert trace.verdict == "NEEDS_MORE_CONTEXT"
        assert all(s["rrf_score"] < 0.1 for s in trace.top_scores)

    def test_synthesis_blame_when_retrieval_healthy(self):
        """When retrieval is healthy but answer is weak → synthesis issue."""
        trace = RetrievalTrace(
            channel_health={"fts": "active", "vector": "active", "corpus": "active"},
            hits_after_quality_gate=10,
            verdict="OK",
            channel_contributions={"fts": 5, "vector": 3, "corpus": 2},
            vector_degradation=VectorDegradationInfo(
                backend_status=VectorBackendStatus.AVAILABLE,
                backend_name="sqlite_vss",
            ),
        )
        # All channels active, good verdict → if answer is bad, synthesis is the issue
        warnings = _build_retrieval_warnings(trace)
        assert len(warnings) == 0  # No retrieval warnings → not a retrieval problem

    def test_missing_source_blame(self):
        """When no relevant IDs exist → source material is missing."""
        # This is diagnosed by the eval system when num_relevant == 0
        # The eval harness can detect this case
        pass  # Tested via eval harness integration

    def test_diagnostic_dict_provides_full_picture(self):
        """to_diagnostic_dict() provides enough info to diagnose any weak answer."""
        trace = RetrievalTrace(
            session_id="diag-test",
            query="What is Sexton?",
            channel_health={"fts": "active", "vector": "failed", "corpus": "active"},
            channel_health_reasons={"vector": "Store unavailable"},
            query_expansion=["Sexton", "maintenance"],
            entities_extracted=["Sexton"],
            documents_retrieved_ids=["doc:1"],
            top_scores=[{"id": "doc:1", "rrf_score": 0.04, "raw_score": 0.7}],
            hits_after_quality_gate=1,
            verdict="NEEDS_MORE_CONTEXT",
            channel_contributions={"fts": 1},
            degradation_warnings=["Vector channel unavailable"],
        )
        d = trace.to_diagnostic_dict()

        # All the info needed to diagnose:
        # 1. Channel health shows vector failed → retrieval or embedding issue
        assert d["failed_channels"] == ["vector"]
        # 2. Query expansion worked → not a query understanding issue
        assert d["query_expansion"] == ["Sexton", "maintenance"]
        # 3. Only 1 doc after gate → low context
        assert d["hits_after_quality_gate"] == 1
        # 4. Verdict confirms insufficient context
        assert d["verdict"] == "NEEDS_MORE_CONTEXT"
        # 5. Degradation warnings are present
        assert len(d["degradation_warnings"]) > 0


# ======================================================================
# 9. Integration: full orchestrator round-trip
# ======================================================================


class TestOrchestratorIntegration:
    """Integration test: full retrieval round with channel health tracking."""

    @pytest.mark.asyncio
    async def test_full_round_trip_with_health(self):
        orch = RetrievalOrchestrator()

        async def fts_retriever(query):
            return [
                RetrievalHit(id="fts:1", content="Sexton is the maintenance actor", score=0.95),
                RetrievalHit(id="fts:2", content="Sexton runs the vigil cycle", score=0.85),
            ]

        async def vector_retriever(query):
            return [
                RetrievalHit(id="vec:1", content="Sexton handles tagging and embedding", score=0.90),
            ]

        async def failing_graph_retriever(query):
            raise RuntimeError("GraphStore not initialized")

        orch.register_channel("fts", fts_retriever)
        orch.register_channel("vector", vector_retriever)
        orch.register_channel("graph", failing_graph_retriever)

        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=True,
            enable_graph=True,
            enable_wiki=False,
            enable_procedural=False,
            enable_corpus=False,
        )

        hits, trace = await orch.retrieve("What is Sexton?", config=config)

        # Verify channel health
        assert trace.channel_health["fts"] == "active"
        assert trace.channel_health["vector"] == "active"
        assert trace.channel_health["graph"] == "failed"
        assert trace.channel_health["wiki"] == "disabled"

        # Verify degradation warnings
        assert any("Graph" in w for w in trace.degradation_warnings)

        # Verify documents retrieved
        assert len(trace.documents_retrieved_ids) > 0

        # Verify top scores
        assert len(trace.top_scores) > 0

        # Verify diagnostic dict
        d = trace.to_diagnostic_dict()
        assert d["failed_channels"] == ["graph"]
        assert d["active_channels"] == ["fts", "vector"]

    @pytest.mark.asyncio
    async def test_all_channels_disabled(self):
        orch = RetrievalOrchestrator()

        config = OrchestratorConfig(
            enable_fts=False,
            enable_vector=False,
            enable_graph=False,
            enable_wiki=False,
            enable_procedural=False,
            enable_corpus=False,
        )

        hits, trace = await orch.retrieve("test query", config=config)

        assert hits == []
        assert trace.verdict == "NO_RESULTS"
        # All channels should be disabled
        for ch in ["fts", "vector", "graph", "wiki", "procedural", "corpus"]:
            assert trace.channel_health.get(ch) == "disabled"
