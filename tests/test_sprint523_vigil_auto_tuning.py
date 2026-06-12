"""Sprint 5.23 tests — LLM Vigil, auto-sizing, batch telemetry, auto-tuning, E2E.

Deliverable 1: LLM-Powered Vigil Evaluation
Deliverable 2: Read Pool Auto-Sizing
Deliverable 3: Batch Telemetry Visibility
Deliverable 4: Graph Extraction Batch Size Auto-Tuning
Deliverable 5: End-to-End Vigil + Sexton Integration Test
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass

import pytest

from aip.adapter.read_pool import ReadPoolAutoSizer, ReadPoolHealth
from aip.foundation.schemas import SextonConfig, VigilConfig
from aip.orchestration.actors.vigil import Vigil

# ============================================================================
# Shared fakes
# ============================================================================


class FakeVigilStore:
    def __init__(self):
        self.checks = []
        self.stale = []

    async def record_vigil_check(self, canonical_count=0, stale_count=0, status="healthy"):
        self.checks.append({"canonical_count": canonical_count, "stale_count": stale_count, "status": status})

    async def list_stale_canonicals(self, threshold_days=30):
        return self.stale


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
    """Fake corpus turn store for Vigil + Sexton integration."""

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
        self.transitions.append(
            {
                "artifact_id": artifact_id,
                "to_state": to_state,
                "actor": actor,
                "detail": detail,
            }
        )


class FakeEventStore:
    """Fake event store that captures emitted events."""

    def __init__(self):
        self.events = []

    async def emit(self, event_type, artifact_id, metadata=None):
        self.events.append({"event_type": event_type, "artifact_id": artifact_id, "metadata": metadata})

    async def write_event(self, **kwargs):
        self.events.append(kwargs)


# ============================================================================
# Deliverable 1: LLM-Powered Vigil Evaluation
# ============================================================================


class TestLLMFaithfulnessEvaluation:
    """Tests for LLM-powered faithfulness evaluation in Vigil."""

    def _make_vigil_with_llm(self, llm_enabled=True, model_response=None):
        """Create a Vigil instance with LLM faithfulness enabled."""
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
        return vigil, model_provider, corpus_turns, artifacts

    @pytest.mark.asyncio
    async def test_llm_faithfulness_enabled_by_default(self):
        """LLM faithfulness is enabled by default (graduated Sprint 5.24)."""
        config = VigilConfig()
        assert config.llm_faithfulness_enabled is True
        assert config.llm_faithfulness_model_slot == "evaluation"

    @pytest.mark.asyncio
    async def test_llm_faithfulness_not_called_when_disabled(self):
        """When llm_faithfulness_enabled is False, no LLM calls are made."""
        vigil, model_provider, _, _ = self._make_vigil_with_llm(llm_enabled=False)

        result = await vigil.run_cycle()
        assert result["llm_faithfulness_enabled"] is False
        assert result["llm_eval_count"] == 0
        # No LLM calls should have been made
        assert len(model_provider.calls) == 0

    @pytest.mark.asyncio
    async def test_llm_faithfulness_called_when_enabled_with_flagged_turns(self):
        """When enabled and there are flagged turns, LLM evaluation is attempted."""
        vigil, model_provider, corpus_turns, _ = self._make_vigil_with_llm(
            llm_enabled=True,
            model_response=json.dumps(
                {
                    "faithfulness_score": 0.85,
                    "hallucination_flags": [],
                    "grounding_assessment": "mostly_grounded",
                    "explanation": "Response accurately reflects sources.",
                }
            ),
        )

        # Add a turn with low citation rate (will be flagged)
        turn = FakeTurn(
            turn_id="turn-flagged",
            assistant_text="The answer is 42.",  # No source citations
            metadata_json=json.dumps({"source_turn_ids": ["src-001", "src-002"]}),
        )
        corpus_turns._turns = [turn]

        result = await vigil.run_cycle()
        assert result["llm_faithfulness_enabled"] is True
        # The LLM evaluation should have been attempted
        assert len(model_provider.calls) > 0
        assert model_provider.calls[0]["slot"] == "evaluation"

    @pytest.mark.asyncio
    async def test_llm_faithfulness_graceful_fallback_on_error(self):
        """When the model provider returns an error, Vigil falls back gracefully."""
        model_provider = FakeModelProvider(slot_error=True)
        config = VigilConfig(llm_faithfulness_enabled=True)
        corpus_turns = FakeCorpusTurnStore()
        turn = FakeTurn(
            turn_id="turn-fallback",
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
            artifact_store=FakeArtifactStore(),
            corpus_turn_store=corpus_turns,
        )

        result = await vigil.run_cycle()
        # Should complete without error, even though LLM failed
        assert result["status"] == "quality_evaluation_complete"
        # Should record the LLM failure in telemetry
        assert vigil._llm_faithfulness_telemetry["total_llm_evaluations_failed"] > 0

    @pytest.mark.asyncio
    async def test_parse_faithfulness_response_handles_valid_json(self):
        """_parse_faithfulness_response correctly parses valid JSON."""
        response = json.dumps(
            {
                "faithfulness_score": 0.9,
                "hallucination_flags": ["Claim about X not supported"],
                "explanation": "Mostly faithful.",
            }
        )
        result = Vigil._parse_faithfulness_response(response)
        assert result is not None
        assert result["faithfulness_score"] == 0.9
        assert len(result["hallucination_flags"]) == 1

    @pytest.mark.asyncio
    async def test_parse_faithfulness_response_handles_markdown_fences(self):
        """_parse_faithfulness_response handles markdown code fences."""
        response = '```json\n{"faithfulness_score": 0.7, "hallucination_flags": [], "explanation": "OK"}\n```'
        result = Vigil._parse_faithfulness_response(response)
        assert result is not None
        assert result["faithfulness_score"] == 0.7

    @pytest.mark.asyncio
    async def test_parse_faithfulness_response_returns_none_on_invalid(self):
        """_parse_faithfulness_response returns None for invalid input."""
        assert Vigil._parse_faithfulness_response("") is None
        assert Vigil._parse_faithfulness_response("not json at all") is None
        assert Vigil._parse_faithfulness_response('{"no_faithfulness_score": true}') is None

    @pytest.mark.asyncio
    async def test_vigil_result_includes_llm_telemetry(self):
        """run_cycle result includes LLM faithfulness telemetry."""
        vigil, _, _, _ = self._make_vigil_with_llm(llm_enabled=True)
        result = await vigil.run_cycle()
        assert "llm_faithfulness_telemetry" in result
        assert "total_llm_evaluations" in result["llm_faithfulness_telemetry"]


# ============================================================================
# Deliverable 2: Read Pool Auto-Sizing
# ============================================================================


class TestReadPoolAutoSizing:
    """Tests for ReadPoolAutoSizer."""

    def test_no_suggestion_when_exhaustion_low(self):
        """Auto-sizer does not suggest when exhaustion rate is low."""
        sizer = ReadPoolAutoSizer(consecutive_threshold=3)
        health: ReadPoolHealth = {
            "pool_size": 3,
            "pool_active": 1,
            "checkout_count": 100,
            "fallback_count": 5,
            "exhaustion_count": 5,
            "exhaustion_rate": 0.05,
            "avg_checkout_latency_ms": 1.0,
            "p95_checkout_latency_ms": 2.0,
            "recommendation": "",
        }
        result = sizer.observe("graph_store", health)
        assert result is None
        assert len(sizer.get_suggestions()) == 0

    def test_suggestion_after_consecutive_high_exhaustion(self):
        """Auto-sizer generates a suggestion after consecutive high exhaustion."""
        sizer = ReadPoolAutoSizer(consecutive_threshold=3)
        high_health: ReadPoolHealth = {
            "pool_size": 3,
            "pool_active": 3,
            "checkout_count": 100,
            "fallback_count": 50,
            "exhaustion_count": 50,
            "exhaustion_rate": 0.5,
            "avg_checkout_latency_ms": 5.0,
            "p95_checkout_latency_ms": 10.0,
            "recommendation": "",
        }
        # First two observations — no suggestion yet
        sizer.observe("graph_store", high_health)
        sizer.observe("graph_store", high_health)
        assert len(sizer.get_suggestions()) == 0

        # Third observation — suggestion generated
        suggestion = sizer.observe("graph_store", high_health)
        assert suggestion is not None
        assert suggestion.suggested_pool_size > 3
        suggestions = sizer.get_suggestions()
        assert len(suggestions) == 1
        assert suggestions[0]["store_name"] == "graph_store"

    def test_critical_exhaustion_doubles_pool_size(self):
        """Critical exhaustion rate (>0.6) suggests doubling pool size."""
        sizer = ReadPoolAutoSizer(consecutive_threshold=2)
        critical_health: ReadPoolHealth = {
            "pool_size": 3,
            "pool_active": 3,
            "checkout_count": 100,
            "fallback_count": 80,
            "exhaustion_count": 80,
            "exhaustion_rate": 0.8,
            "avg_checkout_latency_ms": 10.0,
            "p95_checkout_latency_ms": 20.0,
            "recommendation": "",
        }
        sizer.observe("graph_store", critical_health)
        suggestion = sizer.observe("graph_store", critical_health)
        assert suggestion is not None
        # Critical: double from 3 to 6
        assert suggestion.suggested_pool_size == 6

    def test_suggestion_cleared_when_exhaustion_recovers(self):
        """Suggestion is cleared when exhaustion rate drops below threshold."""
        sizer = ReadPoolAutoSizer(consecutive_threshold=2)
        high_health: ReadPoolHealth = {
            "pool_size": 3,
            "pool_active": 3,
            "checkout_count": 100,
            "fallback_count": 50,
            "exhaustion_count": 50,
            "exhaustion_rate": 0.5,
            "avg_checkout_latency_ms": 5.0,
            "p95_checkout_latency_ms": 10.0,
            "recommendation": "",
        }
        sizer.observe("graph_store", high_health)
        sizer.observe("graph_store", high_health)
        assert len(sizer.get_suggestions()) == 1

        # Recovery
        low_health: ReadPoolHealth = {
            "pool_size": 3,
            "pool_active": 1,
            "checkout_count": 200,
            "fallback_count": 10,
            "exhaustion_count": 10,
            "exhaustion_rate": 0.05,
            "avg_checkout_latency_ms": 1.0,
            "p95_checkout_latency_ms": 2.0,
            "recommendation": "",
        }
        sizer.observe("graph_store", low_health)
        assert len(sizer.get_suggestions()) == 0

    def test_suggestion_respects_max_pool_size(self):
        """Suggested size never exceeds _AUTO_SIZE_MAX_POOL."""
        sizer = ReadPoolAutoSizer(consecutive_threshold=2)
        # Start with a high pool size already
        critical_health: ReadPoolHealth = {
            "pool_size": 8,
            "pool_active": 8,
            "checkout_count": 100,
            "fallback_count": 90,
            "exhaustion_count": 90,
            "exhaustion_rate": 0.9,
            "avg_checkout_latency_ms": 10.0,
            "p95_checkout_latency_ms": 20.0,
            "recommendation": "",
        }
        sizer.observe("graph_store", critical_health)
        suggestion = sizer.observe("graph_store", critical_health)
        # Doubling 8 would be 16, but max is 10
        assert suggestion.suggested_pool_size == 10

    def test_clear_suggestion_manual(self):
        """clear_suggestion removes a specific store's suggestion."""
        sizer = ReadPoolAutoSizer(consecutive_threshold=2)
        high_health: ReadPoolHealth = {
            "pool_size": 3,
            "pool_active": 3,
            "checkout_count": 100,
            "fallback_count": 50,
            "exhaustion_count": 50,
            "exhaustion_rate": 0.5,
            "avg_checkout_latency_ms": 5.0,
            "p95_checkout_latency_ms": 10.0,
            "recommendation": "",
        }
        sizer.observe("graph_store", high_health)
        sizer.observe("graph_store", high_health)
        assert len(sizer.get_suggestions()) == 1

        sizer.clear_suggestion("graph_store")
        assert len(sizer.get_suggestions()) == 0


# ============================================================================
# Deliverable 3: Batch Telemetry Visibility
# ============================================================================


class TestBatchTelemetryVisibility:
    """Tests for batch telemetry summary in the health endpoint."""

    def test_batch_telemetry_summary_computation(self):
        """Batch telemetry summary computes efficiency ratio correctly."""
        # Simulate what the health endpoint computes
        sexton_batch_telemetry = {
            "total_batch_extractions": 10,
            "total_per_turn_extractions": 5,
            "total_turns_via_batch": 20,
            "total_turns_via_per_turn": 5,
            "total_estimated_tokens_saved": 8000,
        }

        batch_extractions = sexton_batch_telemetry.get("total_batch_extractions", 0)
        per_turn_extractions = sexton_batch_telemetry.get("total_per_turn_extractions", 0)
        total_extractions = batch_extractions + per_turn_extractions

        efficiency_ratio = round(batch_extractions / total_extractions, 3) if total_extractions > 0 else 0.0

        assert efficiency_ratio == 0.667  # 10/15

    def test_batch_telemetry_summary_with_no_data(self):
        """Batch telemetry summary handles no-data case."""
        sexton_batch_telemetry = {}
        batch_extractions = sexton_batch_telemetry.get("total_batch_extractions", 0)
        per_turn_extractions = sexton_batch_telemetry.get("total_per_turn_extractions", 0)
        total = batch_extractions + per_turn_extractions
        assert total == 0

    def test_vigil_llm_telemetry_available_on_vigil(self):
        """Vigil instance has _llm_faithfulness_telemetry attribute."""
        vigil, _, _, _ = TestLLMFaithfulnessEvaluation()._make_vigil_with_llm()
        assert hasattr(vigil, "_llm_faithfulness_telemetry")
        assert "total_llm_evaluations" in vigil._llm_faithfulness_telemetry


# ============================================================================
# Deliverable 4: Graph Extraction Batch Size Auto-Tuning
# ============================================================================


class TestBatchSizeAutoTuning:
    """Tests for graph extraction batch size auto-tuning."""

    def test_auto_tune_enabled_by_default(self):
        """Auto-tuning is enabled by default in SextonConfig (graduated Sprint 5.24)."""
        config = SextonConfig()
        assert config.graph_extraction_batch_auto_tune_enabled is True

    def test_auto_tune_decreases_on_high_failure_rate(self):
        """Auto-tune decreases batch size when failure rate is high."""
        from aip.orchestration.actors.sexton import Sexton

        config = SextonConfig(
            graph_extraction_batch_auto_tune_enabled=True,
            graph_extraction_batch_size=4,
            graph_extraction_auto_tune_window=3,
        )
        sexton = Sexton(config=config)
        sexton._current_batch_size = 4

        # Simulate high failure rate
        sexton._batch_parse_results = [False, False, False]

        result = sexton._auto_tune_batch_size()
        assert result["action"] == "decreased"
        assert result["new_batch_size"] == 3
        assert sexton._current_batch_size == 3

    def test_auto_tune_increases_on_low_failure_rate(self):
        """Auto-tune increases batch size when failure rate is low."""
        from aip.orchestration.actors.sexton import Sexton

        config = SextonConfig(
            graph_extraction_batch_auto_tune_enabled=True,
            graph_extraction_batch_size=2,
            graph_extraction_auto_tune_window=3,
        )
        sexton = Sexton(config=config)
        sexton._current_batch_size = 2

        # Simulate success
        sexton._batch_parse_results = [True, True, True]

        result = sexton._auto_tune_batch_size()
        assert result["action"] == "increased"
        assert result["new_batch_size"] == 3
        assert sexton._current_batch_size == 3

    def test_auto_tune_no_change_in_moderate_range(self):
        """Auto-tune does not change batch size in the moderate failure range."""
        from aip.orchestration.actors.sexton import Sexton

        config = SextonConfig(
            graph_extraction_batch_auto_tune_enabled=True,
            graph_extraction_batch_size=3,
            graph_extraction_auto_tune_window=4,
        )
        sexton = Sexton(config=config)
        sexton._current_batch_size = 3

        # Moderate failure rate (1/4 = 0.25, between 0.1 and 0.3)
        sexton._batch_parse_results = [True, True, True, False]

        result = sexton._auto_tune_batch_size()
        assert result["action"] == "no_change"
        assert sexton._current_batch_size == 3

    def test_auto_tune_respects_min_max_bounds(self):
        """Auto-tune respects min and max batch size bounds."""
        from aip.orchestration.actors.sexton import Sexton

        config = SextonConfig(
            graph_extraction_batch_auto_tune_enabled=True,
            graph_extraction_batch_size=1,
            graph_extraction_batch_size_min=1,
            graph_extraction_batch_size_max=4,
            graph_extraction_auto_tune_window=3,
        )
        sexton = Sexton(config=config)
        sexton._current_batch_size = 1

        # Can't decrease below min
        sexton._batch_parse_results = [False, False, False]
        result = sexton._auto_tune_batch_size()
        assert result["action"] == "none"  # Already at min
        assert sexton._current_batch_size == 1

        # Can increase on success
        sexton._batch_parse_results = [True, True, True]
        result = sexton._auto_tune_batch_size()
        assert result["action"] == "increased"
        assert sexton._current_batch_size == 2

    def test_auto_tune_respects_max_bound(self):
        """Auto-tune respects maximum batch size bound."""
        from aip.orchestration.actors.sexton import Sexton

        config = SextonConfig(
            graph_extraction_batch_auto_tune_enabled=True,
            graph_extraction_batch_size=4,
            graph_extraction_batch_size_max=4,
            graph_extraction_auto_tune_window=3,
        )
        sexton = Sexton(config=config)
        sexton._current_batch_size = 4

        # Can't increase beyond max
        sexton._batch_parse_results = [True, True, True]
        result = sexton._auto_tune_batch_size()
        assert result["action"] == "none"  # Already at max
        assert sexton._current_batch_size == 4

    def test_auto_tune_returns_disabled_result_when_not_enabled(self):
        """Auto-tune returns early when not enabled."""
        from aip.orchestration.actors.sexton import Sexton

        config = SextonConfig(graph_extraction_batch_auto_tune_enabled=False)
        sexton = Sexton(config=config)

        result = sexton._auto_tune_batch_size()
        assert result["enabled"] is False
        assert result["action"] == "none"

    def test_auto_tune_trims_history(self):
        """Auto-tune trims parse results history to prevent unbounded growth."""
        from aip.orchestration.actors.sexton import Sexton

        config = SextonConfig(graph_extraction_batch_auto_tune_enabled=True)
        sexton = Sexton(config=config)

        # Add 60 results
        sexton._batch_parse_results = [True] * 60
        sexton._auto_tune_batch_size()
        assert len(sexton._batch_parse_results) <= 50


# ============================================================================
# Deliverable 5: End-to-End Vigil + Sexton Integration Test
# ============================================================================


class TestVigilSextonIntegration:
    """End-to-end test: Sexton extraction → Vigil evaluation → Review queue."""

    @pytest.mark.asyncio
    async def test_vigil_flags_quality_issue_creates_review_artifact(self):
        """When Vigil flags a quality issue, an artifact is created for review.

        This tests the full flow:
        1. A corpus turn with low citation rate exists
        2. Vigil evaluates it
        3. Vigil writes a GENERATED artifact
        4. The artifact is in the review pipeline (ECS transition)
        """
        config = VigilConfig()
        model_provider = FakeModelProvider()
        corpus_turns = FakeCorpusTurnStore()
        artifacts = FakeArtifactStore()
        ecs = FakeECSStore()
        events = FakeEventStore()

        # Create a turn with low citation rate (sources exist but not cited)
        turn = FakeTurn(
            turn_id="turn-low-cite",
            conversation_id="conv-test",
            user_text="What are the key findings?",
            assistant_text="The key findings show significant results.",  # No [source: ...] citations
            metadata_json=json.dumps({"source_turn_ids": ["src-001", "src-002", "src-003"]}),
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

        # Vigil should have evaluated the turn
        assert result["status"] == "quality_evaluation_complete"
        assert result["evaluated_count"] == 1
        assert result["flagged_count"] == 1  # Low citation rate

        # A vigil-flag artifact should have been created
        assert "vigil-flag-turn-low-cite" in artifacts.artifacts

        # The artifact should have been transitioned to GENERATED state
        assert len(ecs.transitions) > 0
        transition = ecs.transitions[0]
        assert transition["to_state"] == "GENERATED"
        assert transition["actor"] == "vigil"

        # The artifact content should contain quality analysis
        artifact = artifacts.artifacts["vigil-flag-turn-low-cite"]
        content = json.loads(artifact["content"])
        assert "flag_reasons" in content
        assert "low_citation_rate" in content["flag_reasons"]
        assert content["citation_rate"] < Vigil._CITATION_THRESHOLD

    @pytest.mark.asyncio
    async def test_vigil_llm_flag_adds_hallucination_to_review(self):
        """When Vigil LLM detects hallucination, an additional review artifact is created."""
        config = VigilConfig(
            llm_faithfulness_enabled=True,
            llm_faithfulness_model_slot="evaluation",
        )
        llm_response = json.dumps(
            {
                "faithfulness_score": 0.3,
                "hallucination_flags": ["Claim about 99.9% success rate not in sources"],
                "grounding_assessment": "poorly_grounded",
                "explanation": "Response contains unsupported statistical claims.",
            }
        )
        model_provider = FakeModelProvider(response_content=llm_response)
        corpus_turns = FakeCorpusTurnStore()
        artifacts = FakeArtifactStore()
        ecs = FakeECSStore()
        events = FakeEventStore()

        # Create a borderline turn (citation rate near threshold, might have hallucination)
        # Use source IDs that won't match in the response text to simulate moderate citation
        turn = FakeTurn(
            turn_id="turn-hallucination",
            conversation_id="conv-test",
            user_text="What are the results?",
            assistant_text=(
                "The results show a 99.9% success rate which is unprecedented."
            ),  # No [source: ...] citations
            metadata_json=json.dumps({"source_turn_ids": ["abc12345", "def67890"]}),  # IDs not in resp
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

        # LLM evaluation should have been attempted
        assert result["llm_eval_count"] > 0
        # Hallucination should be detected
        assert result["llm_hallucinations_detected"] > 0

    @pytest.mark.asyncio
    async def test_review_queue_integration(self):
        """Test that Vigil-flagged artifacts can be approved/rejected through the review queue.

        This tests the integration between Vigil's artifact creation and the
        ReviewQueueStore's approve/reject flow.
        """
        from aip.adapter.review_queue_store import ReviewQueueStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/test_state.db"
            rq = ReviewQueueStore(db_path=db_path)
            await rq.initialize()

            # Simulate a Vigil flag creating a review item
            item_id = await rq.enqueue(
                artifact_id="vigil-flag-turn-001",
                ecs_state="GENERATED",
                domain="quality_evaluation",
                review_type="definer",
                reason="Low citation rate detected by Vigil",
                context={
                    "citation_rate": 0.15,
                    "flag_reasons": ["low_citation_rate", "poor_source_grounding"],
                },
            )
            assert item_id > 0

            # Verify the item is in the pending queue
            pending = await rq.list_pending()
            assert len(pending) >= 1
            assert pending[0]["artifact_id"] == "vigil-flag-turn-001"

            # DEFINER can approve it
            result = await rq.decide(
                item_id, decision="approved", decided_by="definer", notes="Reviewed and acceptable"
            )
            assert result["ok"] is True
            assert result["decision"] == "approved"

            # Or reject another
            item_id2 = await rq.enqueue(
                artifact_id="vigil-flag-turn-002",
                reason="Hallucination detected",
            )
            result2 = await rq.decide(
                item_id2, decision="rejected", decided_by="definer", notes="Unacceptable hallucination"
            )
            assert result2["ok"] is True
            assert result2["decision"] == "rejected"

            await rq.close()

    @pytest.mark.asyncio
    async def test_vigil_high_quality_turn_not_flagged(self):
        """A turn with good citation rate, grounding, and no hedging is not flagged."""
        config = VigilConfig()
        model_provider = FakeModelProvider()
        corpus_turns = FakeCorpusTurnStore()
        artifacts = FakeArtifactStore()
        ecs = FakeECSStore()

        # Create a well-cited turn
        turn = FakeTurn(
            turn_id="turn-good",
            assistant_text=(
                "Based on [source: src-001], the population is 13.9 million. [source: src-002] confirms this."
            ),
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
            corpus_turn_store=corpus_turns,
        )

        result = await vigil.run_cycle()
        # Good citation rate — should not be flagged
        assert result["flagged_count"] == 0
        # No vigil-flag artifacts should exist
        assert len(artifacts.artifacts) == 0
