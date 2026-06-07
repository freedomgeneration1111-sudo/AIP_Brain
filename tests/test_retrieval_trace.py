"""Tests for retrieval trace and golden test types.

Phase 5.0: Verify that trace dataclasses, golden test loading,
and cluster matching work correctly before building retrievers on top.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from aip.foundation.schemas.retrieval_trace import (
    EvidenceStatus,
    GoldenTestResult,
    RetrievalBudget,
    RetrievalChannel,
    RetrievalHit,
    RetrievalQuery,
    RetrievalTrace,
    RetrieverTrace,
)


# ---------------------------------------------------------------------------
# RetrievalHit
# ---------------------------------------------------------------------------


class TestRetrievalHit:
    def test_default_values(self):
        hit = RetrievalHit(id="t1", source_type="corpus_turn", source_id="t1")
        assert hit.retrieval_channel == RetrievalChannel.LEGACY
        assert hit.evidence_status == EvidenceStatus.RAW
        assert hit.rank == 0
        assert hit.entities == []
        assert hit.debug == {}

    def test_full_construction(self):
        hit = RetrievalHit(
            id="t1",
            source_type="corpus_turn",
            source_id="t1",
            title="Test Turn",
            text="Komal is the principal of Freedom Generation School",
            rank=1,
            score=0.95,
            confidence=0.8,
            importance=0.9,
            domain="freedom_gen",
            entities=["Komal", "Freedom Generation School"],
            retrieval_channel=RetrievalChannel.FTS,
            evidence_status=EvidenceStatus.APPROVED,
            debug={"bm25_score": -2.5},
        )
        assert hit.rank == 1
        assert hit.retrieval_channel == RetrievalChannel.FTS
        assert hit.evidence_status == EvidenceStatus.APPROVED
        assert "Komal" in hit.entities

    def test_channel_values(self):
        assert RetrievalChannel.FTS.value == "fts"
        assert RetrievalChannel.VECTOR.value == "vector"
        assert RetrievalChannel.GRAPH.value == "graph"
        assert RetrievalChannel.WIKI.value == "wiki"
        assert RetrievalChannel.PROCEDURAL.value == "procedural"

    def test_evidence_status_values(self):
        assert EvidenceStatus.APPROVED.value == "approved"
        assert EvidenceStatus.RAW.value == "raw"
        assert EvidenceStatus.MODEL_OUTPUT.value == "model_output"
        assert EvidenceStatus.REJECTED.value == "rejected"
        assert EvidenceStatus.SUPERSEDED.value == "superseded"


# ---------------------------------------------------------------------------
# RetrievalQuery
# ---------------------------------------------------------------------------


class TestRetrievalQuery:
    def test_normalization(self):
        q = RetrievalQuery(raw_query="  Who   is Komal?  ")
        assert q.normalized_query == "who is komal?"

    def test_explicit_normalized(self):
        q = RetrievalQuery(raw_query="Who is Komal?", normalized_query="komal identity")
        assert q.normalized_query == "komal identity"

    def test_defaults(self):
        q = RetrievalQuery(raw_query="test")
        assert q.domain_filter is None
        assert q.intent_hint is None
        assert q.max_candidates == 40


# ---------------------------------------------------------------------------
# RetrievalBudget
# ---------------------------------------------------------------------------


class TestRetrievalBudget:
    def test_default_allocations(self):
        b = RetrievalBudget()
        assert b.total_tokens == 8000
        assert b.wiki_allocation == pytest.approx(0.12)
        assert b.evidence_allocation == pytest.approx(0.60)
        assert b.max_sources == 25

    def test_custom_budget(self):
        b = RetrievalBudget(total_tokens=16000, max_sources=50)
        assert b.total_tokens == 16000
        assert b.max_sources == 50


# ---------------------------------------------------------------------------
# RetrieverTrace
# ---------------------------------------------------------------------------


class TestRetrieverTrace:
    def test_defaults(self):
        t = RetrieverTrace(retriever_name="FTSRetriever")
        assert t.enabled is True
        assert t.degraded is False
        assert t.error is None
        assert t.hit_count == 0

    def test_with_results(self):
        t = RetrieverTrace(
            retriever_name="GraphRetriever",
            hit_count=15,
            top_score=0.88,
            top_hit_ids=["h1", "h2", "h3"],
            degraded=True,
            error="PPR timeout — used direct mentions only",
        )
        assert t.hit_count == 15
        assert t.degraded is True
        assert len(t.top_hit_ids) == 3


# ---------------------------------------------------------------------------
# RetrievalTrace
# ---------------------------------------------------------------------------


class TestRetrievalTrace:
    def test_compute_summary(self):
        trace = RetrievalTrace(
            query=RetrievalQuery(raw_query="Komal"),
            retriever_traces=[
                RetrieverTrace(retriever_name="FTSRetriever", hit_count=20, debug={"domains": ["freedom_gen", "ministry"]}),
                RetrieverTrace(retriever_name="VectorRetriever", hit_count=10, debug={"domains": ["freedom_gen"]}),
            ],
        )
        trace.compute_summary()
        assert trace.total_hits == 30
        assert "freedom_gen" in trace.unique_domains
        assert "ministry" in trace.unique_domains

    def test_empty_trace(self):
        trace = RetrievalTrace(query=RetrievalQuery(raw_query=""))
        trace.compute_summary()
        assert trace.total_hits == 0
        assert trace.unique_domains == []


# ---------------------------------------------------------------------------
# Golden test YAML loading
# ---------------------------------------------------------------------------


class TestGoldenTestLoading:
    def test_load_valid_yaml(self, tmp_path):
        test_data = {
            "query": "Who is Komal?",
            "must_include_clusters": ["principal_role", "school"],
            "must_not_dominate": ["unrelated"],
            "success": {"recall_at_25": 0.75},
        }
        yaml_file = tmp_path / "komal.yaml"
        with open(yaml_file, "w") as f:
            yaml.dump(test_data, f)

        with open(yaml_file) as f:
            loaded = yaml.safe_load(f)

        assert loaded["query"] == "Who is Komal?"
        assert "principal_role" in loaded["must_include_clusters"]
        assert loaded["success"]["recall_at_25"] == 0.75

    def test_golden_test_schema_completeness(self, tmp_path):
        """Verify all golden test files have required fields."""
        golden_dir = Path(__file__).parent / "retrieval_goldens"
        if not golden_dir.exists():
            pytest.skip("Golden test directory not found")

        for yaml_file in golden_dir.glob("*.yaml"):
            with open(yaml_file) as f:
                data = yaml.safe_load(f)

            assert "query" in data, f"{yaml_file.name}: missing 'query'"
            assert "must_include_clusters" in data, f"{yaml_file.name}: missing 'must_include_clusters'"
            assert isinstance(data["must_include_clusters"], list), f"{yaml_file.name}: must_include_clusters must be list"
            assert "success" in data, f"{yaml_file.name}: missing 'success'"
            assert "recall_at_25" in data["success"], f"{yaml_file.name}: missing recall_at_25 in success"


# ---------------------------------------------------------------------------
# GoldenTestResult
# ---------------------------------------------------------------------------


class TestGoldenTestResult:
    def test_passing_result(self):
        r = GoldenTestResult(
            test_name="komal",
            query="Who is Komal?",
            total_hits=30,
            hits_at_10=[],
            hits_at_25=[],
            hits_at_40=[],
            recall_at_10=0.50,
            recall_at_25=0.83,
            recall_at_40=0.83,
            noise_top_10=0.20,
            passed=True,
        )
        assert r.passed is True
        assert r.failures == []

    def test_failing_result(self):
        r = GoldenTestResult(
            test_name="komal",
            query="Who is Komal?",
            total_hits=5,
            hits_at_10=[],
            hits_at_25=[],
            hits_at_40=[],
            recall_at_10=0.16,
            recall_at_25=0.33,
            recall_at_40=0.50,
            noise_top_10=0.60,
            passed=False,
            failures=["recall_at_25=0.33 < 0.75", "noise_top_10=0.60 > 0.25"],
        )
        assert r.passed is False
        assert len(r.failures) == 2
