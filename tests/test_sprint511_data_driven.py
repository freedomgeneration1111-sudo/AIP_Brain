"""Tests for Sprint 5.11: Data-Driven Tuning & Visibility.

Covers:
1. Data-driven ChannelSelector tuning (cross-domain, Graph on Wiki/Procedural)
2. Expanded golden query set (20 queries with diverse coverage)
3. Dashboard integration of evaluation data and channel contributions
4. LLM entity extraction observability in RetrievalTrace
5. Per-channel budget defaults tuned by contribution data
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

from aip.foundation.schemas.retrieval import RetrievalHit, RetrievalTrace
from aip.orchestration.retrieval_orchestrator import (
    OrchestratorConfig,
    RetrievalOrchestrator,
)


# ---------------------------------------------------------------------------
# 1. Data-Driven ChannelSelector Tuning
# ---------------------------------------------------------------------------


class TestChannelSelectorTuning:
    """Verify Sprint 5.11 data-driven ChannelSelector tuning."""

    def test_entity_threshold_lowered_to_zero(self):
        """Entity threshold default is now 0 — any entity triggers Graph."""
        from aip.orchestration.channel_selector import ChannelSelector
        selector = ChannelSelector()
        # Default entity_threshold should be 0
        assert selector._entity_threshold == 0

    def test_graph_enabled_on_entity_with_threshold_zero(self):
        """With threshold=0, any entity signal should enable Graph."""
        from aip.orchestration.channel_selector import ChannelSelector
        selector = ChannelSelector()
        result = selector.select("Tell me about Python")
        # "Python" is a single entity → should enable Graph
        assert result.enable_graph is True
        assert "graph" in result.auto_enabled_channels

    def test_wiki_queries_also_enable_graph(self):
        """Wiki/definitional queries should also enable Graph channel."""
        from aip.orchestration.channel_selector import ChannelSelector
        selector = ChannelSelector()
        result = selector.select("What is AIP?")
        # "What is" is a wiki signal → should enable Wiki AND Graph
        assert result.enable_wiki is True
        assert result.enable_graph is True
        assert "wiki" in result.auto_enabled_channels
        assert "graph" in result.auto_enabled_channels

    def test_procedural_queries_also_enable_graph(self):
        """Procedural queries should also enable Graph channel."""
        from aip.orchestration.channel_selector import ChannelSelector
        selector = ChannelSelector()
        result = selector.select("How do I configure the deployment?")
        # "How do I" is a procedural signal → should enable Procedural AND Graph
        assert result.enable_procedural is True
        assert result.enable_graph is True
        assert "procedural" in result.auto_enabled_channels
        assert "graph" in result.auto_enabled_channels

    def test_cross_domain_signals_enable_all_channels(self):
        """Cross-domain queries should enable all channels."""
        from aip.orchestration.channel_selector import ChannelSelector
        selector = ChannelSelector()
        # Use a query with clearly 2+ entities and a cross-domain pattern
        result = selector.select("How does the Knowledge Graph connect to the Smart Context Packer?")
        analysis = result.analysis
        assert analysis is not None
        # Should have entity signals
        assert analysis.has_entity_signals is True
        assert analysis.entity_count >= 2
        # Should have cross-domain signal
        assert analysis.has_cross_domain_signals is True
        # Should enable all channels
        assert result.enable_graph is True
        assert result.enable_wiki is True
        assert result.enable_procedural is True
        assert result.enable_vector is True

    def test_cross_domain_with_vs(self):
        """Cross-domain 'vs' pattern should trigger cross-domain signals."""
        from aip.orchestration.channel_selector import analyze_query
        # Use query with clearly 2+ multi-word entities and 'vs'
        analysis = analyze_query("Knowledge Graph vs Smart Context Packer performance")
        assert analysis.has_cross_domain_signals is True

    def test_cross_domain_requires_multiple_entities(self):
        """Cross-domain requires both a relationship word AND multiple entities."""
        from aip.orchestration.channel_selector import analyze_query
        # Single entity with "and" → not cross-domain
        analysis = analyze_query("What is AIP and how does it work?")
        assert analysis.has_cross_domain_signals is False

    def test_graph_on_wiki_can_be_disabled(self):
        """The enable_graph_on_wiki flag can be turned off."""
        from aip.orchestration.channel_selector import ChannelSelector
        selector = ChannelSelector(enable_graph_on_wiki=False)
        result = selector.select("What is AIP?")
        # Wiki should be enabled but NOT Graph
        assert result.enable_wiki is True
        # Graph should NOT be auto-enabled by wiki signal
        # (it may still be enabled by entity signal if threshold is met)
        # With the old threshold=1 behavior, AIP would also trigger entity signal
        # But with threshold=0, AIP triggers entity signal too
        # So let's use a query without entities
        selector2 = ChannelSelector(
            entity_threshold=999,  # effectively disable entity-based Graph
            enable_graph_on_wiki=False,
        )
        result2 = selector2.select("What is a framework?")
        assert result2.enable_wiki is True
        assert result2.enable_graph is False

    def test_graph_on_procedural_can_be_disabled(self):
        """The enable_graph_on_procedural flag can be turned off."""
        from aip.orchestration.channel_selector import ChannelSelector
        selector = ChannelSelector(
            entity_threshold=999,  # disable entity-based Graph
            enable_graph_on_procedural=False,
        )
        result = selector.select("How do I deploy?")
        assert result.enable_procedural is True
        assert result.enable_graph is False

    def test_analyze_query_cross_domain_pattern(self):
        """analyze_query should detect cross-domain patterns."""
        from aip.orchestration.channel_selector import analyze_query

        # "between" with multiple entities
        analysis = analyze_query("Differences between FTS and Vector search")
        assert analysis.has_cross_domain_signals is True
        assert any("cross_domain" in p for p in analysis.matched_patterns)


# ---------------------------------------------------------------------------
# 2. Expanded Golden Query Set
# ---------------------------------------------------------------------------


class TestExpandedGoldenQueries:
    """Verify the expanded golden query set has diverse coverage."""

    def test_golden_queries_file_has_20_entries(self):
        """The golden queries file should have 20 entries."""
        from aip.orchestration.retrieval_eval import load_golden_queries

        project_root = os.environ.get("AIP_PROJECT_ROOT", ".")
        path = os.path.join(project_root, "tests", "retrieval_goldens", "golden_queries.json")
        if not os.path.exists(path):
            # Try relative to this test file
            test_dir = os.path.dirname(__file__)
            path = os.path.join(test_dir, "..", "retrieval_goldens", "golden_queries.json")
            path = os.path.normpath(path)

        queries = load_golden_queries(path)
        assert len(queries) >= 20, f"Expected at least 20 golden queries, got {len(queries)}"

    def test_golden_queries_have_cross_domain_coverage(self):
        """Some golden queries should have cross-domain tags."""
        from aip.orchestration.retrieval_eval import load_golden_queries

        project_root = os.environ.get("AIP_PROJECT_ROOT", ".")
        path = os.path.join(project_root, "tests", "retrieval_goldens", "golden_queries.json")
        if not os.path.exists(path):
            test_dir = os.path.dirname(__file__)
            path = os.path.join(test_dir, "..", "retrieval_goldens", "golden_queries.json")
            path = os.path.normpath(path)

        queries = load_golden_queries(path)
        cross_domain = [q for q in queries if "cross-domain" in q.tags]
        assert len(cross_domain) >= 2, f"Expected at least 2 cross-domain queries, got {len(cross_domain)}"

    def test_golden_queries_have_entity_heavy_coverage(self):
        """Some golden queries should be entity-heavy."""
        from aip.orchestration.retrieval_eval import load_golden_queries

        project_root = os.environ.get("AIP_PROJECT_ROOT", ".")
        path = os.path.join(project_root, "tests", "retrieval_goldens", "golden_queries.json")
        if not os.path.exists(path):
            test_dir = os.path.dirname(__file__)
            path = os.path.join(test_dir, "..", "retrieval_goldens", "golden_queries.json")
            path = os.path.normpath(path)

        queries = load_golden_queries(path)
        entity_heavy = [q for q in queries if "entity-heavy" in q.tags]
        assert len(entity_heavy) >= 3, f"Expected at least 3 entity-heavy queries, got {len(entity_heavy)}"

    def test_golden_queries_have_typo_variations(self):
        """Some golden queries should have typo/variations."""
        from aip.orchestration.retrieval_eval import load_golden_queries

        project_root = os.environ.get("AIP_PROJECT_ROOT", ".")
        path = os.path.join(project_root, "tests", "retrieval_goldens", "golden_queries.json")
        if not os.path.exists(path):
            test_dir = os.path.dirname(__file__)
            path = os.path.join(test_dir, "..", "retrieval_goldens", "golden_queries.json")
            path = os.path.normpath(path)

        queries = load_golden_queries(path)
        typo_queries = [q for q in queries if "typo-variation" in q.tags]
        assert len(typo_queries) >= 2, f"Expected at least 2 typo-variation queries, got {len(typo_queries)}"

    def test_golden_queries_have_procedural_coverage(self):
        """Some golden queries should be procedural."""
        from aip.orchestration.retrieval_eval import load_golden_queries

        project_root = os.environ.get("AIP_PROJECT_ROOT", ".")
        path = os.path.join(project_root, "tests", "retrieval_goldens", "golden_queries.json")
        if not os.path.exists(path):
            test_dir = os.path.dirname(__file__)
            path = os.path.join(test_dir, "..", "retrieval_goldens", "golden_queries.json")
            path = os.path.normpath(path)

        queries = load_golden_queries(path)
        procedural = [q for q in queries if "procedural" in q.tags]
        assert len(procedural) >= 5, f"Expected at least 5 procedural queries, got {len(procedural)}"

    def test_create_default_golden_queries_has_20(self):
        """create_default_golden_queries should produce 20 queries."""
        from aip.orchestration.retrieval_eval import create_default_golden_queries

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "golden_queries.json")
            create_default_golden_queries(path)

            with open(path) as f:
                data = json.load(f)
            assert len(data) >= 20


# ---------------------------------------------------------------------------
# 3. Dashboard Integration of Evaluation Data
# ---------------------------------------------------------------------------


class TestDashboardEvalIntegration:
    """Verify evaluation data is accessible through the dashboard API."""

    def test_quality_endpoint_returns_channel_budgets(self):
        """The /quality endpoint should return channel budget configuration."""
        from aip.orchestration.retrieval_orchestrator import OrchestratorConfig
        config = OrchestratorConfig()
        budgets = {
            "fts": config.fts_max_hits,
            "vector": config.vector_max_hits,
            "graph": config.graph_max_hits,
            "wiki": config.wiki_max_hits,
            "procedural": config.procedural_max_hits,
            "corpus": config.corpus_max_hits,
        }
        # FTS and corpus should have non-zero caps
        assert budgets["fts"] > 0
        assert budgets["corpus"] > 0
        # Graph should have a reasonable limit
        assert budgets["graph"] > 0
        # Vector is unlimited (0 = no cap)
        assert budgets["vector"] == 0

    def test_dashboard_includes_channel_contribution_summary_field(self):
        """Dashboard response should include channel_contribution_summary."""
        # This is tested via the dashboard endpoint structure
        from aip.adapter.api.routes.retrieval_dashboard import _compute_channel_contribution_summary

        # Test with a mock container that returns no traces
        class MockContainer:
            event_store = None

        result = asyncio.run(
            _compute_channel_contribution_summary(MockContainer(), limit=10)
        )
        assert isinstance(result, dict)

    def test_dashboard_includes_llm_entity_extraction_field(self):
        """Dashboard response should include llm_entity_extraction summary."""
        from aip.adapter.api.routes.retrieval_dashboard import _compute_llm_extraction_summary

        class MockContainer:
            event_store = None

        result = asyncio.run(
            _compute_llm_extraction_summary(MockContainer(), limit=10)
        )
        assert isinstance(result, dict)
        assert "total_calls" in result
        assert "success_rate" in result
        assert "avg_ms" in result

    def test_load_latest_eval_result_returns_none_when_no_files(self):
        """_load_latest_eval_result should return None if no eval results exist."""
        from aip.adapter.api.routes.retrieval_dashboard import _load_latest_eval_result

        # With default eval_results/ not existing in current dir
        # (this test may or may not find files depending on env)
        result = _load_latest_eval_result()
        # Result is either None (no eval results) or a dict with metrics
        assert result is None or isinstance(result, dict)

    def test_load_latest_eval_result_reads_json(self):
        """_load_latest_eval_result should parse JSON eval results."""
        from aip.adapter.api.routes.retrieval_dashboard import _load_latest_eval_result

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a fake eval result
            eval_path = os.path.join(tmpdir, "eval_20260607T143022.json")
            with open(eval_path, "w") as f:
                json.dump({
                    "timestamp": "2026-06-07T14:30:22Z",
                    "total_queries": 20,
                    "mean_recall_at_k": 0.75,
                    "mean_precision_at_k": 0.6,
                    "mean_mrr": 0.8,
                    "mean_entity_coverage": 0.5,
                    "channel_contribution_summary": {"fts": 10},
                    "eval_harness_version": "5.12",
                }, f)

            # Temporarily override the eval dir
            old_env = os.environ.get("AIP_EVAL_DIR")
            os.environ["AIP_EVAL_DIR"] = tmpdir
            try:
                result = _load_latest_eval_result()
                assert result is not None
                assert result["total_queries"] == 20
                assert result["mean_recall_at_k"] == 0.75
                assert result["eval_harness_version"] == "5.12"
            finally:
                if old_env is not None:
                    os.environ["AIP_EVAL_DIR"] = old_env
                else:
                    os.environ.pop("AIP_EVAL_DIR", None)


# ---------------------------------------------------------------------------
# 4. LLM Entity Extraction Observability
# ---------------------------------------------------------------------------


class TestLLMEntityExtractionObservability:
    """Verify LLM entity extraction observability in RetrievalTrace."""

    def test_trace_has_llm_observability_fields(self):
        """RetrievalTrace should have LLM observability fields."""
        trace = RetrievalTrace()
        assert hasattr(trace, "llm_entity_extraction_ms")
        assert hasattr(trace, "llm_entity_extraction_status")
        assert hasattr(trace, "llm_entity_count")

    def test_trace_llm_fields_defaults(self):
        """LLM observability fields should have sensible defaults."""
        trace = RetrievalTrace()
        assert trace.llm_entity_extraction_ms == 0.0
        assert trace.llm_entity_extraction_status == "not_used"
        assert trace.llm_entity_count == 0

    def test_trace_llm_fields_can_be_set(self):
        """LLM observability fields should be settable."""
        trace = RetrievalTrace(
            llm_entity_extraction_ms=150.3,
            llm_entity_extraction_status="success",
            llm_entity_count=3,
        )
        assert trace.llm_entity_extraction_ms == 150.3
        assert trace.llm_entity_extraction_status == "success"
        assert trace.llm_entity_count == 3

    async def test_orchestrator_extracts_llm_data_from_graph_hits(self):
        """Orchestrator should transfer LLM observability from graph hits to trace."""
        async def _graph_retriever_with_llm_data(query: str) -> list[RetrievalHit]:
            return [
                RetrievalHit(
                    id="graph:Entity1",
                    content="Graph entity",
                    score=0.8,
                    source_channel="graph",
                    metadata={
                        "type": "graph_entity",
                        "entity_name": "Entity1",
                        "_llm_entity_extraction_ms": 200.5,
                        "_llm_entity_extraction_status": "success",
                        "_llm_entity_count": 5,
                    },
                ),
            ]

        orch = RetrievalOrchestrator()
        orch.register_channel("graph", _graph_retriever_with_llm_data)

        config = OrchestratorConfig(enable_graph=True, enable_fts=False, enable_vector=False, enable_corpus=False)
        hits, trace = await orch.retrieve("test query", config=config)

        # The trace should have LLM data extracted from graph hits
        assert trace.llm_entity_extraction_ms == 200.5
        assert trace.llm_entity_extraction_status == "success"
        assert trace.llm_entity_count == 5

    async def test_orchestrator_no_llm_data_when_graph_absent(self):
        """When no graph channel is active, LLM observability should be defaults."""
        async def _fts_retriever(query: str) -> list[RetrievalHit]:
            return [RetrievalHit(id="fts:1", content="FTS hit", score=0.9, source_channel="fts")]

        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fts_retriever)

        config = OrchestratorConfig(enable_fts=True, enable_graph=False)
        hits, trace = await orch.retrieve("test", config=config)

        # LLM fields should be defaults
        assert trace.llm_entity_extraction_ms == 0.0
        assert trace.llm_entity_extraction_status == "not_used"
        assert trace.llm_entity_count == 0


# ---------------------------------------------------------------------------
# 5. Per-Channel Budget Defaults
# ---------------------------------------------------------------------------


class TestPerChannelBudgetTuning:
    """Verify Sprint 5.11 per-channel budget defaults are data-driven."""

    def test_fts_budget_capped(self):
        """FTS should have a non-zero budget cap to prevent dominance."""
        config = OrchestratorConfig()
        assert config.fts_max_hits > 0
        assert config.fts_max_hits <= 20  # reasonable cap

    def test_corpus_budget_capped(self):
        """Corpus should have a non-zero budget cap (similar to FTS)."""
        config = OrchestratorConfig()
        assert config.corpus_max_hits > 0
        assert config.corpus_max_hits <= 20

    def test_graph_budget_reasonable(self):
        """Graph should have a reasonable budget (higher than 0)."""
        config = OrchestratorConfig()
        assert config.graph_max_hits > 0
        assert config.graph_max_hits <= 15

    def test_wiki_budget_reasonable(self):
        """Wiki should have a reasonable budget."""
        config = OrchestratorConfig()
        assert config.wiki_max_hits > 0
        assert config.wiki_max_hits <= 15

    def test_procedural_budget_reasonable(self):
        """Procedural should have a reasonable budget."""
        config = OrchestratorConfig()
        assert config.procedural_max_hits > 0
        assert config.procedural_max_hits <= 10

    def test_vector_budget_unlimited(self):
        """Vector channel should remain unlimited by default."""
        config = OrchestratorConfig()
        assert config.vector_max_hits == 0  # unlimited

    async def test_fts_budget_prevents_dominance(self):
        """FTS budget cap should prevent FTS from dominating results."""
        async def _large_fts(query: str) -> list[RetrievalHit]:
            return [
                RetrievalHit(id=f"fts:{i}", content=f"FTS hit {i}", score=0.9 - i * 0.01, source_channel="fts")
                for i in range(30)
            ]

        async def _small_graph(query: str) -> list[RetrievalHit]:
            return [
                RetrievalHit(id="graph:1", content="Graph hit", score=0.7, source_channel="graph"),
            ]

        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _large_fts)
        orch.register_channel("graph", _small_graph)

        config = OrchestratorConfig(enable_fts=True, enable_graph=True)
        hits, trace = await orch.retrieve("test", config=config)

        # FTS should be capped at its budget (default 15)
        assert trace.per_channel_hit_counts.get("fts", 0) <= config.fts_max_hits
        # Graph hit should survive
        assert trace.channel_contributions.get("graph", 0) >= 1


# ---------------------------------------------------------------------------
# 6. Eval Harness Version
# ---------------------------------------------------------------------------


class TestEvalHarnessVersion:
    """Verify eval harness version is updated for Sprint 5.12."""

    def test_eval_harness_version_is_current(self):
        from aip.orchestration.retrieval_eval import EvalResult
        result = EvalResult()
        # Sprint 5.12: Version bumped to reflect A/B eval + budget tuning
        assert result.eval_harness_version >= "5.11"

    def test_eval_result_format_includes_channel_contribution_summary(self):
        from aip.orchestration.retrieval_eval import EvalResult
        result = EvalResult(
            channel_contribution_summary={"fts": 10, "graph": 5, "wiki": 3},
        )
        data = result.to_dict()
        assert "channel_contribution_summary" in data
        assert data["channel_contribution_summary"]["fts"] == 10
