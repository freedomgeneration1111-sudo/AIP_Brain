"""Sprint 5.24 tests — Graduating experimental features to production defaults.

Deliverable 1: LLM Faithfulness Evaluation graduated to default-on
Deliverable 2: Auto-Apply Read Pool Sizing (with Safeguards + Rollback)
Deliverable 3: Vigil Per-Cycle Quality Report Artifact
Deliverable 4: Graph Extraction Batch Size Auto-Tuning graduated to default-on
Deliverable 5: Health Endpoint Auto-Tuning Status
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from aip.foundation.schemas import SextonConfig, VigilConfig
from aip.orchestration.actors.vigil import Vigil
from aip.adapter.read_pool import (
    ReadPoolAutoSizer,
    ReadPoolHealth,
    PoolSizeAdjustment,
    PoolSizeSuggestion,
)


# ============================================================================
# Shared fakes (reused from Sprint 5.23 test infrastructure)
# ============================================================================


class FakeVigilStore:
    def __init__(self):
        self.checks = []

    async def record_vigil_check(self, canonical_count=0, stale_count=0, status="healthy"):
        self.checks.append({"canonical_count": canonical_count, "stale_count": stale_count, "status": status})


class FakeCanonicalStore:
    def __init__(self):
        self.canonicals = []

    async def list_canonical(self, domain=None):
        return self.canonicals


class FakeEntityStore:
    async def list_entities(self):
        return []

    async def get_entity(self, entity_id):
        return None


class FakeModelProvider:
    """Model provider that returns configurable responses for LLM faithfulness."""

    def __init__(self, response_content=None, slot_error=False):
        self._response_content = response_content
        self._slot_error = slot_error
        self.calls = []

    async def call(self, slot, messages, **kwargs):
        self.calls.append({"slot": slot, "messages": messages})
        if self._slot_error:
            return {"content": "", "error": True, "error_message": "Slot unavailable"}
        if self._response_content:
            return {
                "content": self._response_content,
                "model": "test-eval-model",
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }
        return {"content": "[CI-FIXTURE]", "model": "ci-eval", "usage": {}}


class FakeTraceStore:
    def __init__(self):
        self.events = []

    async def write_event(self, **kwargs):
        self.events.append(kwargs)


@dataclass
class FakeTurn:
    """Minimal turn object for Vigil evaluation tests."""
    turn_id: str = "turn-001"
    conversation_id: str = "conv-001"
    user_text: str = "What is the population of Tokyo?"
    assistant_text: str = "Based on [source: src-001], the population is 13.9 million."
    thinking_text: str = ""
    metadata_json: str = ""
    word_count: int = 50


class FakeCorpusTurnStore:
    """Fake corpus turn store for Vigil tests."""

    def __init__(self, turns=None):
        self._turns = turns or []
        self._metadata_updates = {}

    async def get_augmented_turns_since(self, since=None, limit=100):
        return self._turns

    async def update_metadata_json(self, turn_id, metadata_json):
        self._metadata_updates[turn_id] = metadata_json


class FakeArtifactStore:
    """Fake artifact store that captures written artifacts."""

    def __init__(self):
        self.artifacts = {}

    async def write(self, id, content, metadata=None):
        self.artifacts[id] = {"content": content, "metadata": metadata}


class FakeECSStore:
    """Fake ECS store that captures transitions."""

    def __init__(self):
        self.transitions = []

    async def transition(self, artifact_id, to_state, actor, detail=None, **kwargs):
        self.transitions.append({
            "artifact_id": artifact_id,
            "to_state": to_state,
            "actor": actor,
            "detail": detail,
        })


class FakeEventStore:
    """Fake event store that captures emitted events."""

    def __init__(self):
        self.events = []

    async def emit(self, event_type, artifact_id, metadata=None):
        self.events.append({"event_type": event_type, "artifact_id": artifact_id, "metadata": metadata})

    async def write_event(self, **kwargs):
        self.events.append(kwargs)


class FakeReadPoolMixin:
    """Fake ReadPoolMixin for testing auto-apply."""

    def __init__(self, pool_size: int = 3):
        self._read_pool_size = pool_size
        self._read_pool = []
        self._read_pool_available = []


# ============================================================================
# Deliverable 1: LLM Faithfulness Evaluation — Default-On
# ============================================================================


class TestLLMFaithfulnessDefaultOn:
    """Tests for LLM faithfulness being enabled by default (Sprint 5.24 graduation)."""

    def test_llm_faithfulness_enabled_by_default(self):
        """LLM faithfulness is now enabled by default after Sprint 5.24 graduation."""
        config = VigilConfig()
        assert config.llm_faithfulness_enabled is True, (
            "llm_faithfulness_enabled should default to True after Sprint 5.24 graduation"
        )

    def test_llm_faithfulness_can_still_be_disabled(self):
        """Operators can still explicitly disable LLM faithfulness if needed."""
        config = VigilConfig(llm_faithfulness_enabled=False)
        assert config.llm_faithfulness_enabled is False

    @pytest.mark.asyncio
    async def test_llm_faithfulness_called_by_default_with_flagged_turns(self):
        """When using default config (LLM enabled), Vigil attempts LLM evaluation on flagged turns."""
        llm_response = json.dumps({
            "faithfulness_score": 0.9,
            "hallucination_flags": [],
            "grounding_assessment": "mostly_grounded",
            "explanation": "Response accurately reflects sources.",
        })
        model_provider = FakeModelProvider(response_content=llm_response)
        config = VigilConfig()  # Default: llm_faithfulness_enabled=True
        assert config.llm_faithfulness_enabled is True

        corpus_turns = FakeCorpusTurnStore()
        artifacts = FakeArtifactStore()
        ecs = FakeECSStore()
        events = FakeEventStore()

        turn = FakeTurn(
            turn_id="turn-default-llm",
            assistant_text="The answer is 42.",  # No source citations
            metadata_json=json.dumps({"source_turn_ids": ["src-001", "src-002"]}),
        )
        corpus_turns._turns = [turn]

        vigil = Vigil(
            config=config,
            vigil_store=FakeVigilStore(),
            canonical_store=FakeCanonicalStore(),
            entity_store=FakeEntityStore(),
            model_provider=model_provider,
            trace_store=FakeTraceStore(),
            artifact_store=artifacts,
            ecs_store=ecs,
            event_store=events,
            corpus_turn_store=corpus_turns,
        )

        result = await vigil.run_cycle()
        assert result["llm_faithfulness_enabled"] is True
        assert len(model_provider.calls) > 0
        assert model_provider.calls[0]["slot"] == "evaluation"

    @pytest.mark.asyncio
    async def test_llm_faithfulness_graceful_fallback_still_works(self):
        """Graceful fallback remains robust after graduation."""
        model_provider = FakeModelProvider(slot_error=True)
        config = VigilConfig()  # Default: enabled
        corpus_turns = FakeCorpusTurnStore()
        turn = FakeTurn(
            turn_id="turn-fallback-grad",
            assistant_text="The answer is 42.",
            metadata_json=json.dumps({"source_turn_ids": ["src-001"]}),
        )
        corpus_turns._turns = [turn]

        vigil = Vigil(
            config=config,
            vigil_store=FakeVigilStore(),
            canonical_store=FakeCanonicalStore(),
            entity_store=FakeEntityStore(),
            model_provider=model_provider,
            trace_store=FakeTraceStore(),
            corpus_turn_store=corpus_turns,
        )

        result = await vigil.run_cycle()
        assert result["status"] == "quality_evaluation_complete"
        assert vigil._llm_faithfulness_telemetry["total_llm_evaluations_failed"] > 0


# ============================================================================
# Deliverable 2: Auto-Apply Read Pool Sizing
# ============================================================================


class TestReadPoolAutoApply:
    """Tests for read pool auto-apply with safeguards (Sprint 5.24)."""

    def test_auto_apply_enabled_by_default(self):
        """Auto-apply is enabled by default in Sprint 5.24."""
        sizer = ReadPoolAutoSizer()
        assert sizer.auto_apply_enabled is True

    def test_auto_apply_increases_pool_on_sustained_exhaustion(self):
        """Auto-apply increases pool size when exhaustion is sustained for 5+ observations."""
        sizer = ReadPoolAutoSizer(auto_apply_consecutive_threshold=5)
        store = FakeReadPoolMixin(pool_size=3)

        high_health: ReadPoolHealth = {
            "pool_size": 3, "pool_active": 3,
            "checkout_count": 100, "fallback_count": 50,
            "exhaustion_count": 50, "exhaustion_rate": 0.5,
            "avg_checkout_latency_ms": 5.0,
            "p95_checkout_latency_ms": 10.0,
            "recommendation": "",
        }

        # First 4 observations — no auto-apply yet
        for _ in range(4):
            sizer.observe("graph_store", high_health, store=store)
        assert store._read_pool_size == 3  # No change yet

        # 5th observation — auto-apply triggers
        sizer.observe("graph_store", high_health, store=store)
        assert store._read_pool_size > 3  # Pool size increased
        assert sizer._auto_applied_increase["graph_store"] > 0

    def test_auto_apply_respects_max_increase_safeguard(self):
        """Auto-apply never increases pool_size by more than max_increase above configured."""
        sizer = ReadPoolAutoSizer(
            auto_apply_consecutive_threshold=3,
            auto_apply_max_increase=2,
        )
        store = FakeReadPoolMixin(pool_size=3)

        critical_health: ReadPoolHealth = {
            "pool_size": 3, "pool_active": 3,
            "checkout_count": 100, "fallback_count": 90,
            "exhaustion_count": 90, "exhaustion_rate": 0.9,
            "avg_checkout_latency_ms": 10.0,
            "p95_checkout_latency_ms": 20.0,
            "recommendation": "",
        }

        # Trigger auto-apply multiple times
        for _ in range(10):
            sizer.observe("graph_store", critical_health, store=store)

        # Max increase above configured (3) is 2, so max pool_size is 5
        assert store._read_pool_size <= 3 + 2

    def test_auto_apply_respects_max_pool_safeguard(self):
        """Auto-apply never exceeds the absolute max_pool cap."""
        sizer = ReadPoolAutoSizer(
            auto_apply_consecutive_threshold=3,
            auto_apply_max_pool=6,
            auto_apply_max_increase=10,  # High, but max_pool caps it
        )
        store = FakeReadPoolMixin(pool_size=3)

        critical_health: ReadPoolHealth = {
            "pool_size": 3, "pool_active": 3,
            "checkout_count": 100, "fallback_count": 90,
            "exhaustion_count": 90, "exhaustion_rate": 0.9,
            "avg_checkout_latency_ms": 10.0,
            "p95_checkout_latency_ms": 20.0,
            "recommendation": "",
        }

        for _ in range(20):
            sizer.observe("graph_store", critical_health, store=store)

        assert store._read_pool_size <= 6  # Absolute cap

    def test_auto_apply_records_adjustment_history(self):
        """All auto-applied changes are recorded in adjustment history."""
        sizer = ReadPoolAutoSizer(auto_apply_consecutive_threshold=3)
        store = FakeReadPoolMixin(pool_size=3)

        high_health: ReadPoolHealth = {
            "pool_size": 3, "pool_active": 3,
            "checkout_count": 100, "fallback_count": 50,
            "exhaustion_count": 50, "exhaustion_rate": 0.5,
            "avg_checkout_latency_ms": 5.0,
            "p95_checkout_latency_ms": 10.0,
            "recommendation": "",
        }

        for _ in range(5):
            sizer.observe("graph_store", high_health, store=store)

        history = sizer.get_adjustment_history("graph_store")
        assert len(history) > 0
        assert history[0]["store_name"] == "graph_store"
        assert history[0]["new_pool_size"] > history[0]["previous_pool_size"]

    def test_rollback_restores_configured_pool_size(self):
        """Rollback restores the pool to its configured size."""
        sizer = ReadPoolAutoSizer(auto_apply_consecutive_threshold=3)
        store = FakeReadPoolMixin(pool_size=3)

        high_health: ReadPoolHealth = {
            "pool_size": 3, "pool_active": 3,
            "checkout_count": 100, "fallback_count": 50,
            "exhaustion_count": 50, "exhaustion_rate": 0.5,
            "avg_checkout_latency_ms": 5.0,
            "p95_checkout_latency_ms": 10.0,
            "recommendation": "",
        }

        for _ in range(5):
            sizer.observe("graph_store", high_health, store=store)

        assert store._read_pool_size > 3  # Auto-applied increase

        # Rollback
        result = sizer.rollback("graph_store", store)
        assert result is True
        assert store._read_pool_size == 3  # Restored to configured

    def test_rollback_noop_when_already_at_configured(self):
        """Rollback returns False when already at configured size."""
        sizer = ReadPoolAutoSizer()
        store = FakeReadPoolMixin(pool_size=3)
        result = sizer.rollback("graph_store", store)
        assert result is False

    def test_get_status_returns_full_auto_sizing_state(self):
        """get_status returns complete auto-sizing status for all observed stores."""
        sizer = ReadPoolAutoSizer(auto_apply_consecutive_threshold=3)
        store = FakeReadPoolMixin(pool_size=3)

        high_health: ReadPoolHealth = {
            "pool_size": 3, "pool_active": 3,
            "checkout_count": 100, "fallback_count": 50,
            "exhaustion_count": 50, "exhaustion_rate": 0.5,
            "avg_checkout_latency_ms": 5.0,
            "p95_checkout_latency_ms": 10.0,
            "recommendation": "",
        }

        sizer.observe("graph_store", high_health, store=store)

        status = sizer.get_status()
        assert "auto_apply_enabled" in status
        assert "stores" in status
        assert "graph_store" in status["stores"]
        assert status["stores"]["graph_store"]["configured_pool_size"] == 3
        assert status["stores"]["graph_store"]["current_pool_size"] == 3

    def test_auto_apply_suggestion_marked_as_applied(self):
        """When auto-apply triggers, the suggestion is marked as auto_applied."""
        sizer = ReadPoolAutoSizer(auto_apply_consecutive_threshold=3)
        store = FakeReadPoolMixin(pool_size=3)

        high_health: ReadPoolHealth = {
            "pool_size": 3, "pool_active": 3,
            "checkout_count": 100, "fallback_count": 50,
            "exhaustion_count": 50, "exhaustion_rate": 0.5,
            "avg_checkout_latency_ms": 5.0,
            "p95_checkout_latency_ms": 10.0,
            "recommendation": "",
        }

        # Need enough observations for both suggestion (3) and auto-apply (3)
        for _ in range(4):
            sizer.observe("graph_store", high_health, store=store)

        suggestions = sizer.get_suggestions()
        assert len(suggestions) > 0
        assert suggestions[0]["auto_applied"] is True

    def test_suggestion_only_mode_when_no_store_provided(self):
        """When no store is provided to observe(), only suggestions are generated."""
        sizer = ReadPoolAutoSizer(auto_apply_consecutive_threshold=3)

        high_health: ReadPoolHealth = {
            "pool_size": 3, "pool_active": 3,
            "checkout_count": 100, "fallback_count": 50,
            "exhaustion_count": 50, "exhaustion_rate": 0.5,
            "avg_checkout_latency_ms": 5.0,
            "p95_checkout_latency_ms": 10.0,
            "recommendation": "",
        }

        # Observe without store — no auto-apply possible
        for _ in range(5):
            sizer.observe("graph_store", high_health)  # No store

        suggestions = sizer.get_suggestions()
        assert len(suggestions) > 0
        # No auto-apply could happen (no store)
        assert len(sizer.get_adjustment_history()) == 0


# ============================================================================
# Deliverable 3: Vigil Per-Cycle Quality Report Artifact
# ============================================================================


class TestVigilCycleQualityReport:
    """Tests for per-cycle Vigil quality report artifact (Sprint 5.24)."""

    def _make_vigil(self, llm_enabled=True, model_response=None):
        """Create a Vigil instance with all required stores."""
        config = VigilConfig(
            llm_faithfulness_enabled=llm_enabled,
            llm_faithfulness_model_slot="evaluation",
            llm_faithfulness_sample_size=5,
        )
        model_provider = FakeModelProvider(response_content=model_response)
        corpus_turns = FakeCorpusTurnStore()
        artifacts = FakeArtifactStore()
        ecs = FakeECSStore()
        events = FakeEventStore()

        vigil = Vigil(
            config=config,
            vigil_store=FakeVigilStore(),
            canonical_store=FakeCanonicalStore(),
            entity_store=FakeEntityStore(),
            model_provider=model_provider,
            trace_store=FakeTraceStore(),
            artifact_store=artifacts,
            ecs_store=ecs,
            event_store=events,
            corpus_turn_store=corpus_turns,
        )
        return vigil, model_provider, corpus_turns, artifacts, ecs

    def test_vigil_has_cycle_report_history(self):
        """Vigil has _cycle_report_history attribute initialized."""
        vigil, _, _, _, _ = self._make_vigil()
        assert hasattr(vigil, "_cycle_report_history")
        assert isinstance(vigil._cycle_report_history, list)
        assert len(vigil._cycle_report_history) == 0

    def test_trend_indicators_baseline_on_first_cycle(self):
        """First cycle returns 'baseline' trend indicators (no previous data)."""
        vigil, _, _, _, _ = self._make_vigil()
        trend = vigil._compute_trend_indicators(
            avg_citation_rate=0.8,
            avg_grounding_rate=0.9,
            avg_llm_faithfulness=0.85,
        )
        assert trend["citation_rate_trend"] == "baseline"
        assert trend["grounding_rate_trend"] == "baseline"
        assert trend["llm_faithfulness_trend"] == "baseline"
        assert trend["previous_cycle"] is None

    def test_trend_indicators_detect_improvement(self):
        """Trend indicators detect improving quality between cycles."""
        vigil, _, _, _, _ = self._make_vigil()
        # Simulate a previous cycle with lower scores
        vigil._cycle_report_history.append({
            "avg_citation_rate": 0.5,
            "avg_grounding_rate": 0.6,
            "avg_llm_faithfulness": 0.5,
            "evaluated_count": 10,
            "flagged_count": 5,
        })

        trend = vigil._compute_trend_indicators(
            avg_citation_rate=0.8,
            avg_grounding_rate=0.9,
            avg_llm_faithfulness=0.85,
        )
        assert trend["citation_rate_trend"] == "improving"
        assert trend["grounding_rate_trend"] == "improving"
        assert trend["llm_faithfulness_trend"] == "improving"

    def test_trend_indicators_detect_degradation(self):
        """Trend indicators detect degrading quality between cycles."""
        vigil, _, _, _, _ = self._make_vigil()
        vigil._cycle_report_history.append({
            "avg_citation_rate": 0.8,
            "avg_grounding_rate": 0.9,
            "avg_llm_faithfulness": 0.85,
            "evaluated_count": 10,
            "flagged_count": 1,
        })

        trend = vigil._compute_trend_indicators(
            avg_citation_rate=0.5,
            avg_grounding_rate=0.6,
            avg_llm_faithfulness=0.5,
        )
        assert trend["citation_rate_trend"] == "degrading"
        assert trend["grounding_rate_trend"] == "degrading"

    def test_trend_indicators_stable_when_small_change(self):
        """Trend indicators report 'stable' when change is within 5%."""
        vigil, _, _, _, _ = self._make_vigil()
        vigil._cycle_report_history.append({
            "avg_citation_rate": 0.8,
            "avg_grounding_rate": 0.9,
            "avg_llm_faithfulness": 0.85,
            "evaluated_count": 10,
            "flagged_count": 1,
        })

        trend = vigil._compute_trend_indicators(
            avg_citation_rate=0.81,
            avg_grounding_rate=0.91,
            avg_llm_faithfulness=0.86,
        )
        assert trend["citation_rate_trend"] == "stable"
        assert trend["grounding_rate_trend"] == "stable"

    @pytest.mark.asyncio
    async def test_cycle_quality_report_artifact_created_on_concerns(self):
        """A per-cycle quality report artifact is created when there are quality concerns."""
        vigil, _, corpus_turns, artifacts, ecs = self._make_vigil()

        # Add a turn with low citation rate (triggers quality concerns)
        turn = FakeTurn(
            turn_id="turn-low-cite-report",
            assistant_text="The answer is 42.",
            metadata_json=json.dumps({"source_turn_ids": ["src-001", "src-002", "src-003"]}),
        )
        corpus_turns._turns = [turn]

        result = await vigil.run_cycle()
        assert result["flagged_count"] > 0

        # A vigil-report artifact should have been created
        report_artifacts = [
            aid for aid in artifacts.artifacts
            if aid.startswith("vigil-report-")
        ]
        assert len(report_artifacts) > 0

        # Verify the report content
        report_content = json.loads(artifacts.artifacts[report_artifacts[0]]["content"])
        assert report_content["report_type"] == "vigil_cycle_quality_report"
        assert "aggregate_scores" in report_content
        assert "trend_indicators" in report_content
        assert "thresholds" in report_content

    @pytest.mark.asyncio
    async def test_cycle_quality_report_not_created_when_healthy(self):
        """No per-cycle quality report artifact when all turns are healthy."""
        vigil, _, corpus_turns, artifacts, ecs = self._make_vigil()

        # Add a well-cited turn (no quality concerns)
        turn = FakeTurn(
            turn_id="turn-good-report",
            assistant_text="Based on [source: src-001] and [source: src-002], the results are clear.",
            metadata_json=json.dumps({"source_turn_ids": ["src-001", "src-002"]}),
        )
        corpus_turns._turns = [turn]

        result = await vigil.run_cycle()
        assert result["flagged_count"] == 0

        # No vigil-report artifact should have been created
        report_artifacts = [
            aid for aid in artifacts.artifacts
            if aid.startswith("vigil-report-")
        ]
        assert len(report_artifacts) == 0

    @pytest.mark.asyncio
    async def test_cycle_report_history_tracked(self):
        """Cycle report history is tracked across multiple cycles."""
        vigil, _, corpus_turns, artifacts, _ = self._make_vigil()

        # Run two cycles
        for i in range(2):
            turn = FakeTurn(
                turn_id=f"turn-cycle-{i}",
                assistant_text="Based on [source: src-001], the answer is clear.",
                metadata_json=json.dumps({"source_turn_ids": ["src-001"]}),
            )
            corpus_turns._turns = [turn]
            await vigil.run_cycle()

        assert len(vigil._cycle_report_history) == 2

    @pytest.mark.asyncio
    async def test_run_cycle_result_includes_trend_indicators(self):
        """run_cycle result includes trend_indicators in Sprint 5.24."""
        vigil, _, corpus_turns, _, _ = self._make_vigil()

        turn = FakeTurn(
            turn_id="turn-trend",
            assistant_text="Based on [source: src-001].",
            metadata_json=json.dumps({"source_turn_ids": ["src-001"]}),
        )
        corpus_turns._turns = [turn]

        result = await vigil.run_cycle()
        assert "trend_indicators" in result
        assert "citation_rate_trend" in result["trend_indicators"]


# ============================================================================
# Deliverable 4: Graph Extraction Batch Size Auto-Tuning — Default-On
# ============================================================================


class TestBatchSizeAutoTuneDefaultOn:
    """Tests for batch size auto-tuning being enabled by default (Sprint 5.24)."""

    def test_auto_tune_enabled_by_default(self):
        """Auto-tuning is now enabled by default in SextonConfig."""
        config = SextonConfig()
        assert config.graph_extraction_batch_auto_tune_enabled is True, (
            "graph_extraction_batch_auto_tune_enabled should default to True after Sprint 5.24"
        )

    def test_auto_tune_can_still_be_disabled(self):
        """Operators can still explicitly disable auto-tuning if needed."""
        config = SextonConfig(graph_extraction_batch_auto_tune_enabled=False)
        assert config.graph_extraction_batch_auto_tune_enabled is False

    def test_batch_size_max_remains_8(self):
        """batch_size_max remains at 8 (validated value)."""
        config = SextonConfig()
        assert config.graph_extraction_batch_size_max == 8

    def test_auto_tune_active_by_default_in_sexton(self):
        """When using default SextonConfig, auto-tune is active and can adjust."""
        from aip.orchestration.actors.sexton import Sexton

        config = SextonConfig()  # Default: auto_tune_enabled=True
        sexton = Sexton(config=config)
        sexton._current_batch_size = 2

        # Simulate high failure rate
        sexton._batch_parse_results = [False, False, False, False, False]
        result = sexton._auto_tune_batch_size()

        assert result["enabled"] is True
        assert result["action"] == "decreased"
        assert sexton._current_batch_size == 1  # Decreased from 2 to 1

    def test_auto_tune_increases_on_success_by_default(self):
        """With default config, auto-tune increases batch size on sustained success."""
        from aip.orchestration.actors.sexton import Sexton

        config = SextonConfig()  # Default: auto_tune_enabled=True
        sexton = Sexton(config=config)
        sexton._current_batch_size = 2

        # Simulate consistent success
        sexton._batch_parse_results = [True, True, True, True, True]
        result = sexton._auto_tune_batch_size()

        assert result["enabled"] is True
        assert result["action"] == "increased"
        assert sexton._current_batch_size == 3


# ============================================================================
# Deliverable 5: Health Endpoint Auto-Tuning Status
# ============================================================================


class TestHealthEndpointAutoTuningStatus:
    """Tests for the auto_tuning_status section in the health endpoint."""

    def test_auto_sizer_get_status_structure(self):
        """get_status returns the expected structure for health endpoint."""
        sizer = ReadPoolAutoSizer(auto_apply_enabled=True)

        health: ReadPoolHealth = {
            "pool_size": 3, "pool_active": 1,
            "checkout_count": 100, "fallback_count": 5,
            "exhaustion_count": 5, "exhaustion_rate": 0.05,
            "avg_checkout_latency_ms": 1.0,
            "p95_checkout_latency_ms": 2.0,
            "recommendation": "",
        }
        sizer.observe("graph_store", health)

        status = sizer.get_status()
        assert "auto_apply_enabled" in status
        assert "auto_apply_consecutive_threshold" in status
        assert "auto_apply_max_increase" in status
        assert "auto_apply_max_pool" in status
        assert "stores" in status
        assert "recent_adjustments" in status
        assert isinstance(status["recent_adjustments"], list)

    def test_auto_sizer_status_shows_store_details(self):
        """get_status shows per-store configured vs current pool sizes."""
        sizer = ReadPoolAutoSizer()
        store = FakeReadPoolMixin(pool_size=3)

        health: ReadPoolHealth = {
            "pool_size": 3, "pool_active": 1,
            "checkout_count": 100, "fallback_count": 5,
            "exhaustion_count": 5, "exhaustion_rate": 0.05,
            "avg_checkout_latency_ms": 1.0,
            "p95_checkout_latency_ms": 2.0,
            "recommendation": "",
        }
        sizer.observe("graph_store", health, store=store)

        status = sizer.get_status()
        assert "graph_store" in status["stores"]
        store_status = status["stores"]["graph_store"]
        assert store_status["configured_pool_size"] == 3
        assert store_status["current_pool_size"] == 3
        assert store_status["auto_applied_increase"] == 0
        assert store_status["recent_exhaustion_rate"] == 0.05

    def test_pool_size_suggestion_has_auto_applied_field(self):
        """PoolSizeSuggestion includes auto_applied field in to_dict()."""
        suggestion = PoolSizeSuggestion(
            store_name="graph_store",
            current_pool_size=3,
            suggested_pool_size=5,
            exhaustion_rate=0.5,
            reason="Test",
            auto_applied=True,
        )
        d = suggestion.to_dict()
        assert "auto_applied" in d
        assert d["auto_applied"] is True

    def test_pool_size_adjustment_to_dict(self):
        """PoolSizeAdjustment serializes correctly."""
        adj = PoolSizeAdjustment(
            store_name="graph_store",
            configured_pool_size=3,
            previous_pool_size=3,
            new_pool_size=4,
            exhaustion_rate=0.5,
            reason="Auto-applied increase",
        )
        d = adj.to_dict()
        assert d["store_name"] == "graph_store"
        assert d["configured_pool_size"] == 3
        assert d["previous_pool_size"] == 3
        assert d["new_pool_size"] == 4
        assert "applied_at" in d

    def test_adjustment_history_filterable_by_store(self):
        """get_adjustment_history can be filtered by store name."""
        sizer = ReadPoolAutoSizer(auto_apply_consecutive_threshold=3)

        # Manually add adjustments
        sizer._adjustment_history.append(PoolSizeAdjustment(
            store_name="graph_store", configured_pool_size=3,
            previous_pool_size=3, new_pool_size=4,
            exhaustion_rate=0.5, reason="test",
        ))
        sizer._adjustment_history.append(PoolSizeAdjustment(
            store_name="vector_store", configured_pool_size=3,
            previous_pool_size=3, new_pool_size=5,
            exhaustion_rate=0.6, reason="test",
        ))

        graph_history = sizer.get_adjustment_history("graph_store")
        assert len(graph_history) == 1
        assert graph_history[0]["store_name"] == "graph_store"

        all_history = sizer.get_adjustment_history()
        assert len(all_history) == 2
