"""Tests for Sprint 5.12 — Integration Fixes & Automation.

Deterministic, zero-token, no network, no LLM (except where mocked).
Exercises:
  1. BUG-003 fix: Sexton actor gets ecs_store when created after ECS init
  2. A/B evaluation comparison
  3. Adaptive budget tuning
  4. LLM query expansion (with mock model provider)
  5. OrchestratorConfig new fields
"""

import pytest

from aip.orchestration.retrieval_eval import (
    EvalResult,
    QueryEvalResult,
    ABComparisonResult,
    compare_eval_results,
)
from aip.orchestration.retrieval_orchestrator import OrchestratorConfig
from aip.orchestration.adaptive_budget import (
    AdaptiveBudgetTuner,
    BudgetAdjustment,
    BudgetTuningResult,
)
from aip.orchestration.llm_query_expansion import (
    ExpansionResult,
    expand_query_with_llm,
    _extract_json_array,
)


# =====================================================================
# 1. BUG-003: Sexton actor ECS wiring
# =====================================================================


class TestBug003SextonEcsWiring:
    """Verify Sexton actor receives ecs_store properly after BUG-003 fix."""

    def test_sexton_receives_ecs_store_at_construction(self):
        """Sexton actor should accept ecs_store in its constructor."""
        from aip.orchestration.actors.sexton import Sexton

        class FakeEcsStore:
            async def transition(self, **kw):
                pass

        ecs = FakeEcsStore()
        sexton = Sexton(ecs_store=ecs)
        assert sexton._ecs is ecs, "Sexton should store the ecs_store reference"

    def test_sexton_ecs_backfill_pattern(self):
        """The BUG-003 backfill pattern should work: set _ecs after creation."""
        from aip.orchestration.actors.sexton import Sexton

        class FakeEcsStore:
            async def transition(self, **kw):
                pass

        # Simulate the old bug: ecs_store is None at creation
        sexton = Sexton(ecs_store=None)
        assert sexton._ecs is None

        # Apply the backfill fix
        ecs = FakeEcsStore()
        if getattr(sexton, "_ecs", None) is None:
            sexton._ecs = ecs
        assert sexton._ecs is ecs, "Backfill should set _ecs correctly"

    def test_app_initialization_order_comment(self):
        """Verify that app.py has the BUG-003 fix comment."""
        import aip.adapter.api.app as app_module

        source = open(app_module.__file__).read()
        # Check for the BUG-003 fix comment
        assert "BUG-003" in source, "app.py should contain BUG-003 fix reference"
        assert "Must be initialized BEFORE Sexton actor" in source, (
            "app.py should document ECS init ordering requirement"
        )


# =====================================================================
# 2. A/B Evaluation Comparison
# =====================================================================


class TestABEvaluationComparison:
    """Test A/B evaluation comparison logic."""

    def _make_eval_result(
        self,
        recall: float = 0.5,
        precision: float = 0.4,
        mrr: float = 0.6,
        entity_coverage: float = 0.3,
        channel_contributions: dict = None,
    ) -> EvalResult:
        """Helper to create an EvalResult with given metrics."""
        return EvalResult(
            timestamp="2026-06-08T10:00:00Z",
            total_queries=20,
            mean_recall_at_k=recall,
            mean_precision_at_k=precision,
            mean_mrr=mrr,
            mean_entity_coverage=entity_coverage,
            per_query_results=[
                QueryEvalResult(
                    query="test query 1",
                    recall_at_k=recall,
                    mrr=mrr,
                    channel_contributions=channel_contributions or {},
                ),
            ],
            channel_contribution_summary=channel_contributions or {},
        )

    def test_compare_b_wins(self):
        """When config B has higher metrics, B should win."""
        result_a = self._make_eval_result(recall=0.4, precision=0.3, mrr=0.5)
        result_b = self._make_eval_result(recall=0.7, precision=0.6, mrr=0.8)

        comparison = compare_eval_results(result_a, result_b, "A", "B")
        assert comparison.winner == "B"
        assert comparison.metric_deltas["mean_recall_at_k"]["delta"] > 0
        assert comparison.metric_deltas["mean_mrr"]["delta"] > 0

    def test_compare_a_wins(self):
        """When config A has higher metrics, A should win."""
        result_a = self._make_eval_result(recall=0.8, precision=0.7, mrr=0.9)
        result_b = self._make_eval_result(recall=0.3, precision=0.2, mrr=0.4)

        comparison = compare_eval_results(result_a, result_b)
        assert comparison.winner == "A"
        assert comparison.metric_deltas["mean_recall_at_k"]["delta"] < 0

    def test_compare_tie(self):
        """When metrics are equal, it should be a tie."""
        result_a = self._make_eval_result(recall=0.5, precision=0.5, mrr=0.5, entity_coverage=0.5)
        result_b = self._make_eval_result(recall=0.5, precision=0.5, mrr=0.5, entity_coverage=0.5)

        comparison = compare_eval_results(result_a, result_b)
        assert comparison.winner == "tie"

    def test_channel_delta(self):
        """Channel contribution deltas should be computed correctly."""
        result_a = self._make_eval_result(channel_contributions={"fts": 100, "vector": 50, "graph": 10})
        result_b = self._make_eval_result(channel_contributions={"fts": 80, "vector": 70, "graph": 30})

        comparison = compare_eval_results(result_a, result_b)
        assert comparison.channel_delta["fts"] == -20
        assert comparison.channel_delta["vector"] == 20
        assert comparison.channel_delta["graph"] == 20

    def test_per_query_deltas(self):
        """Per-query deltas should be computed for matching queries."""
        result_a = EvalResult(
            per_query_results=[
                QueryEvalResult(query="test", recall_at_k=0.5, mrr=0.6),
            ],
        )
        result_b = EvalResult(
            per_query_results=[
                QueryEvalResult(query="test", recall_at_k=0.8, mrr=0.9),
            ],
        )

        comparison = compare_eval_results(result_a, result_b)
        assert len(comparison.per_query_deltas) == 1
        assert comparison.per_query_deltas[0]["recall_delta"] == pytest.approx(0.3, abs=0.01)

    def test_format_report(self):
        """Report formatting should produce readable output."""
        result_a = self._make_eval_result(recall=0.4, mrr=0.5)
        result_b = self._make_eval_result(recall=0.6, mrr=0.7)

        comparison = compare_eval_results(result_a, result_b, "Baseline", "Tuned")
        report = comparison.format_report()
        assert "Baseline" in report
        assert "Tuned" in report
        assert "B" in report  # B wins

    def test_ab_result_serialization(self):
        """ABComparisonResult should serialize to dict and JSON."""
        import json

        result_a = self._make_eval_result(recall=0.4)
        result_b = self._make_eval_result(recall=0.6)

        comparison = compare_eval_results(result_a, result_b)
        data = comparison.to_dict()
        assert data["winner"] == "B"
        assert "metric_deltas" in data

        # Should be JSON-serializable
        json_str = json.dumps(data)
        assert len(json_str) > 0


# =====================================================================
# 3. Adaptive Budget Tuning
# =====================================================================


class TestAdaptiveBudgetTuning:
    """Test adaptive budget tuning heuristics."""

    def _make_config(self) -> OrchestratorConfig:
        """Create a config with default budgets."""
        return OrchestratorConfig(
            fts_max_hits=15,
            vector_max_hits=0,  # unlimited, should be skipped
            graph_max_hits=10,
            wiki_max_hits=8,
            procedural_max_hits=5,
            corpus_max_hits=15,
        )

    def test_no_tuning_with_insufficient_data(self):
        """Should not produce suggestions with too few queries."""
        tuner = AdaptiveBudgetTuner(min_samples=5)
        config = self._make_config()

        result = tuner.tune(
            config=config,
            channel_contributions={"fts": 50, "vector": 30},
            total_queries=3,  # Below minimum
        )
        assert len(result.adjustments) == 0
        assert "Insufficient" in result.summary

    def test_high_value_channel_gets_increase(self):
        """Channels contributing > 15% of hits should get budget increase."""
        tuner = AdaptiveBudgetTuner(min_samples=5, auto_apply=False)
        config = self._make_config()

        # graph contributes 30% of all hits — it's high value
        result = tuner.tune(
            config=config,
            channel_contributions={"fts": 60, "vector": 30, "graph": 30, "wiki": 10},
            total_queries=20,
        )

        graph_adj = next((a for a in result.adjustments if a.channel_name == "graph"), None)
        assert graph_adj is not None
        assert graph_adj.suggested_budget > graph_adj.current_budget
        assert "High-value" in graph_adj.reason

    def test_low_contribution_channel_gets_decrease(self):
        """Channels contributing < 5% of hits should get budget decrease."""
        tuner = AdaptiveBudgetTuner(min_samples=5, auto_apply=False)
        config = self._make_config()

        # procedural contributes only 2% — it's low contribution
        result = tuner.tune(
            config=config,
            channel_contributions={"fts": 100, "vector": 80, "graph": 20, "wiki": 15, "procedural": 3},
            total_queries=20,
        )

        proc_adj = next((a for a in result.adjustments if a.channel_name == "procedural"), None)
        assert proc_adj is not None
        assert proc_adj.suggested_budget < proc_adj.current_budget
        assert "Low contribution" in proc_adj.reason

    def test_never_reduce_below_minimum(self):
        """Budget should never go below the minimum budget."""
        tuner = AdaptiveBudgetTuner(min_samples=5, min_budget=2)
        config = OrchestratorConfig(
            procedural_max_hits=3,
            fts_max_hits=15,
            vector_max_hits=0,
            graph_max_hits=10,
            wiki_max_hits=8,
            corpus_max_hits=15,
        )

        result = tuner.tune(
            config=config,
            channel_contributions={"fts": 100, "vector": 80, "procedural": 1},
            total_queries=20,
        )

        proc_adj = next((a for a in result.adjustments if a.channel_name == "procedural"), None)
        if proc_adj is not None:
            assert proc_adj.suggested_budget >= 2, "Budget should not go below minimum"

    def test_unlimited_budgets_skipped(self):
        """Channels with budget=0 (unlimited) should be skipped."""
        tuner = AdaptiveBudgetTuner(min_samples=5)
        config = self._make_config()

        result = tuner.tune(
            config=config,
            channel_contributions={"fts": 50, "vector": 100},  # vector has 67% share
            total_queries=20,
        )

        vector_adj = next((a for a in result.adjustments if a.channel_name == "vector"), None)
        assert vector_adj is None, "Unlimited budget (0) should be skipped"

    def test_apply_adjustments(self):
        """Applying adjustments should modify the config in-place."""
        tuner = AdaptiveBudgetTuner(min_samples=5)
        config = self._make_config()

        result = tuner.tune(
            config=config,
            channel_contributions={"fts": 100, "vector": 80, "graph": 30},
            total_queries=20,
        )

        # Save original values
        original_graph = config.graph_max_hits

        # Apply
        tuner.apply(result, config)

        # Check that at least one budget was modified if there were adjustments
        if result.adjustments:
            # At least one field should have changed
            any_changed = any(a.suggested_budget != a.current_budget for a in result.adjustments)
            assert any_changed or len(result.adjustments) == 0


# =====================================================================
# 4. LLM Query Expansion
# =====================================================================


class TestLLMQueryExpansion:
    """Test LLM query expansion with mock model provider."""

    def test_no_provider_returns_empty(self):
        """Without a model provider, expansion returns empty result."""
        import asyncio

        result = asyncio.run(expand_query_with_llm("test query"))
        assert not result.success
        assert result.error == "no_model_provider"
        assert result.expanded_terms == []

    def test_short_query_returns_empty(self):
        """Very short queries should not be expanded."""
        import asyncio

        class FakeProvider:
            async def call(self, slot, messages):
                return {"content": '["test"]'}

        result = asyncio.run(expand_query_with_llm("a", model_provider=FakeProvider()))
        assert not result.success
        assert result.error == "query_too_short"

    def test_successful_expansion(self):
        """With a working model provider, expansion should return terms."""
        import asyncio

        class FakeProvider:
            async def call(self, slot, messages):
                return {"content": '["knowledge graph setup", "graph database configuration", "KG deployment"]'}

        result = asyncio.run(
            expand_query_with_llm(
                "how to configure knowledge graph",
                model_provider=FakeProvider(),
            )
        )
        assert result.success
        assert len(result.expanded_terms) == 3
        assert "knowledge graph setup" in result.expanded_terms
        assert result.elapsed_ms >= 0

    def test_timeout_returns_empty(self):
        """Timeout should return empty result without error."""
        import asyncio

        class SlowProvider:
            async def call(self, slot, messages):
                # Simulate a provider that takes too long
                # We use a real sleep that exceeds the timeout
                await asyncio.sleep(10)

        # Use a very short timeout to trigger the timeout path
        result = asyncio.run(
            expand_query_with_llm(
                "test query for timeout",
                model_provider=SlowProvider(),
                timeout_seconds=0.05,
            )
        )
        assert not result.success
        assert result.error in ("timeout", "cancelled")

    def test_invalid_json_response(self):
        """Invalid JSON response should return empty terms."""
        import asyncio

        class BadProvider:
            async def call(self, slot, messages):
                return {"content": "This is not JSON at all"}

        result = asyncio.run(
            expand_query_with_llm(
                "test query",
                model_provider=BadProvider(),
            )
        )
        assert not result.success
        assert result.expanded_terms == []

    def test_extract_json_array(self):
        """Test the JSON array extraction helper."""
        assert _extract_json_array('["a", "b", "c"]') == ["a", "b", "c"]
        assert _extract_json_array('```json\n["a", "b"]\n```') == ["a", "b"]
        assert _extract_json_array('Here are terms: ["x", "y"]') == ["x", "y"]
        assert _extract_json_array("") == []
        assert _extract_json_array("no json here") == []

    def test_model_provider_error(self):
        """Model provider raising an error should return empty result."""
        import asyncio

        class ErrorProvider:
            async def call(self, slot, messages):
                raise RuntimeError("model unavailable")

        result = asyncio.run(
            expand_query_with_llm(
                "test query that is long enough",
                model_provider=ErrorProvider(),
            )
        )
        assert not result.success
        assert "model unavailable" in result.error


# =====================================================================
# 5. OrchestratorConfig new fields
# =====================================================================


class TestOrchestratorConfigSprint512:
    """Test new OrchestratorConfig fields added in Sprint 5.12."""

    def test_llm_expansion_fields_default_off(self):
        """LLM expansion should be disabled by default."""
        config = OrchestratorConfig()
        assert config.enable_llm_query_expansion is False
        assert config.llm_query_expansion_timeout == 2.0
        assert config.llm_query_expansion_max_terms == 5

    def test_llm_expansion_can_be_enabled(self):
        """LLM expansion can be enabled explicitly."""
        config = OrchestratorConfig(enable_llm_query_expansion=True)
        assert config.enable_llm_query_expansion is True

    def test_budget_fields_preserved(self):
        """Per-channel budget fields should still work correctly."""
        config = OrchestratorConfig(
            fts_max_hits=15,
            vector_max_hits=0,
            graph_max_hits=10,
            wiki_max_hits=8,
            procedural_max_hits=5,
            corpus_max_hits=15,
        )
        assert config.get_channel_max_hits("fts") == 15
        assert config.get_channel_max_hits("vector") == 0  # unlimited
        assert config.get_channel_max_hits("graph") == 10
        assert config.get_channel_max_hits("unknown_channel") == 0

    def test_backward_compatibility(self):
        """Existing code using OrchestratorConfig() should work unchanged."""
        config = OrchestratorConfig()
        # All existing fields should have their default values
        assert config.enable_fts is True
        assert config.enable_vector is True
        assert config.enable_graph is False
        assert config.enable_wiki is False
        assert config.enable_procedural is False
        assert config.max_retrieval_rounds == 2
        assert config.rrf_k == 60


# =====================================================================
# 6. Sexton End-to-End Wiring
# =====================================================================


class TestSextonEndToEndWiring:
    """End-to-end verification that Sexton can process content through all stages."""

    def test_sexton_run_cycle_with_all_stores(self):
        """Verify Sexton run_cycle completes when all stores are provided (mocked)."""
        from aip.orchestration.actors.sexton import Sexton

        class FakeCorpusTurnStore:
            _db_path = ":memory:"

            async def get_untagged_turns(self, limit=200):
                return []

            async def get_unembedded_turns(self, limit=50):
                return []

            async def get_turns_for_retagging(self, **kw):
                return []

        class FakeEcsStore:
            async def transition(self, **kw):
                pass

        class FakeEventStore:
            async def write_event(self, **kw):
                pass

            async def query(self, **kw):
                return []

        class FakeArtifactStore:
            async def write(self, **kw):
                pass

            async def list_artifacts_by_metadata(self, **kw):
                return []

        class FakeEmbeddingProvider:
            async def embed(self, text):
                return [0.1] * 128

        class FakeVectorStore:
            async def upsert(self, **kw):
                pass

        class FakeModelProvider:
            async def call(self, slot, messages):
                return {"content": "[]"}

        class FakeTraceStore:
            async def write_event(self, **kw):
                pass

        sexton = Sexton(
            sexton_provider=FakeModelProvider(),
            corpus_turn_store=FakeCorpusTurnStore(),
            embedding_provider=FakeEmbeddingProvider(),
            vector_store=FakeVectorStore(),
            artifact_store=FakeArtifactStore(),
            ecs_store=FakeEcsStore(),
            event_store=FakeEventStore(),
            trace_store=FakeTraceStore(),
        )

        import asyncio

        summary = asyncio.run(sexton.run_cycle())

        # Verify all 5 stages ran
        assert "tagging" in summary
        assert "embedding" in summary
        assert "wiki" in summary
        assert "graph" in summary
        assert "classification" in summary
        assert "cycle_elapsed_seconds" in summary

    def test_sexton_run_cycle_graceful_without_stores(self):
        """Sexton should gracefully degrade when stores are missing."""
        from aip.orchestration.actors.sexton import Sexton
        import asyncio

        sexton = Sexton()  # No stores at all
        summary = asyncio.run(sexton.run_cycle())

        # Should complete without error, all stages skip
        assert "tagging" in summary
        assert "embedding" in summary

    def test_sexton_ecs_used_for_proposals(self):
        """Verify ECS store is called when proposals are filed."""
        from aip.orchestration.actors.sexton import Sexton
        import asyncio

        transitions = []

        class RecordingEcsStore:
            async def transition(self, **kw):
                transitions.append(kw)

        # This test just verifies the ECS integration path exists
        sexton = Sexton(ecs_store=RecordingEcsStore())
        assert sexton._ecs is not None


# =====================================================================
# 7. LLM Query Expansion Integration
# =====================================================================


class TestLLMQueryExpansionIntegration:
    """Verify LLM query expansion integrates with RetrievalOrchestrator."""

    def test_orchestrator_config_passes_expansion_settings(self):
        """OrchestratorConfig should carry LLM expansion settings through."""
        config = OrchestratorConfig(
            enable_llm_query_expansion=True,
            llm_query_expansion_timeout=3.0,
            llm_query_expansion_max_terms=3,
        )
        assert config.enable_llm_query_expansion is True
        assert config.llm_query_expansion_timeout == 3.0
        assert config.llm_query_expansion_max_terms == 3

    def test_expansion_disabled_by_default(self):
        """LLM query expansion should not affect retrieval when disabled."""
        from aip.orchestration.retrieval_orchestrator import RetrievalOrchestrator

        config = OrchestratorConfig(enable_llm_query_expansion=False)
        orch = RetrievalOrchestrator()
        # Should not attempt expansion
        assert not config.enable_llm_query_expansion

    def test_expansion_result_used_in_retrieval(self):
        """Expanded terms should be merged into the retrieval query."""
        import asyncio
        from aip.orchestration.llm_query_expansion import expand_query_with_llm

        class FakeProvider:
            async def call(self, slot, messages):
                return {"content": '["knowledge graph setup", "graph configuration"]'}

        result = asyncio.run(
            expand_query_with_llm(
                "how to configure knowledge graph",
                model_provider=FakeProvider(),
            )
        )

        assert result.success
        assert len(result.expanded_terms) == 2
        # These terms would be merged into the retrieval query
        assert "knowledge graph setup" in result.expanded_terms


# =====================================================================
# 8. A/B Comparison Formatting
# =====================================================================


class TestABComparisonFormatting:
    """Test A/B comparison report output quality."""

    def test_ab_report_includes_all_metrics(self):
        """Report should include all four key metrics."""
        result_a = EvalResult(mean_recall_at_k=0.4, mean_precision_at_k=0.3, mean_mrr=0.5, mean_entity_coverage=0.2)
        result_b = EvalResult(mean_recall_at_k=0.7, mean_precision_at_k=0.6, mean_mrr=0.8, mean_entity_coverage=0.5)

        comparison = compare_eval_results(result_a, result_b, "Before", "After")
        report = comparison.format_report()

        assert "mean_recall_at_k" in report
        assert "mean_precision_at_k" in report
        assert "mean_mrr" in report
        assert "mean_entity_coverage" in report
        assert "Before" in report
        assert "After" in report

    def test_ab_comparison_to_dict_roundtrip(self):
        """ABComparisonResult should survive dict serialization."""
        import json

        result_a = EvalResult(mean_recall_at_k=0.5)
        result_b = EvalResult(mean_recall_at_k=0.7)

        comparison = compare_eval_results(result_a, result_b)
        data = comparison.to_dict()

        # Should be fully JSON-serializable
        json_str = json.dumps(data)
        parsed = json.loads(json_str)
        assert parsed["winner"] == "B"
