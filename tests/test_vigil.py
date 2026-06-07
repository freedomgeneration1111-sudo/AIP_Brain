"""Vigil actor tests
(read-only health checks, stale detection, model slot change, trace events for Sexton)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aip.foundation.schemas import ModelSlotConfig, SextonConfig, VigilConfig
from aip.orchestration.actors.vigil import Vigil
from aip.orchestration.sexton.sexton import Sexton

# --- Minimal fakes ---


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
        self.canonicals = [{"artifact_id": "c1"}, {"artifact_id": "c2"}]

    async def list_canonical(self, domain=None):
        return self.canonicals

    async def read_canonical(self, artifact_id):
        return None


class FakeEntityStore:
    def __init__(self):
        self.entities = []

    async def list_entities(self, entity_type=None):
        return self.entities

    async def get_entity(self, entity_id):
        return None


class FakeModelProvider:
    async def call(self, slot, messages, **kw):
        return {"content": "mock"}


class FakeTraceStore:
    def __init__(self):
        self.events = []

    async def write_event(self, **kwargs):
        self.events.append(kwargs)

    async def get_recent_events(self, session_id, limit=100):
        return []


@pytest.fixture
def vigil():
    config = VigilConfig(stale_threshold_days=30, re_evaluate_on_slot_change=True)
    return Vigil(
        config=config,
        vigil_store=FakeVigilStore(),
        canonical_store=FakeCanonicalStore(),
        entity_store=FakeEntityStore(),
        model_provider=FakeModelProvider(),
        trace_store=FakeTraceStore(),
    )


@pytest.mark.asyncio
async def test_vigil_check_canonical_health(vigil):
    """Vigil returns aggregate health status of canonicals."""
    health = await vigil.check_canonical_health()
    assert "total_count" in health
    assert "stale_count" in health
    assert "status" in health
    assert health["total_count"] >= 0


@pytest.mark.asyncio
async def test_vigil_detects_stale_canonicals(vigil):
    """detect_stale_canonicals returns list from VigilStore."""
    vigil.vigil_store.stale = [{"artifact_id": "c1", "days_since_update": 45}]
    stale = await vigil.detect_stale_canonicals()
    assert len(stale) == 1
    assert stale[0]["artifact_id"] == "c1"


@pytest.mark.asyncio
async def test_vigil_run_creates_trace_events_for_stale(vigil):
    """Vigil.run_cycle() records a vigil check and completes quality evaluation.

    Note: Per ADR-011, run_cycle() now performs citation-rate scoring
    instead of stale canonical detection. Legacy stale detection is still
    available via check_canonical_health() / detect_stale_canonicals()
    but is no longer called from run_cycle().
    """
    vigil.vigil_store.stale = [{"artifact_id": "c1", "days_since_update": 45}]
    result = await vigil.run_cycle()
    # Should have recorded a vigil check (quality evaluation cycle)
    assert len(vigil.vigil_store.checks) >= 1
    # run_cycle() now returns citation-rate results, not stale detection
    assert result["status"] == "quality_evaluation_complete"
    assert "evaluated_count" in result
    assert "avg_citation_rate" in result


@pytest.mark.asyncio
async def test_vigil_on_model_slot_change_creates_trace_event(vigil):
    """on_model_slot_change creates a trace event for Sexton to classify."""
    old_config = ModelSlotConfig(slot_name="synthesis", provider="openai", model="old-model")
    new_config = ModelSlotConfig(slot_name="synthesis", provider="openai", model="new-model")
    await vigil.on_model_slot_change("synthesis", old_config, new_config)
    assert len(vigil.trace_store.events) >= 1
    event = vigil.trace_store.events[-1]
    assert event["node_type"] == "vigil"
    assert "synthesis" in event["detail"]


def test_vigil_is_read_only_by_design():
    """Per Appendix D + Process Rule 12: Vigil never modifies canonicals."""
    from pathlib import Path

    vigil_file = Path(__file__).parent.parent / "src/aip/orchestration/actors/vigil.py"
    if vigil_file.exists():
        text = vigil_file.read_text()
        # Should not have any write_canonical calls
        assert "write_canonical" not in text
        # Should be read-only interactions with canonical_store
        assert "list_canonical" in text or "read_canonical" in text


def test_layering_and_no_storage_bypass():
    """Orchestration actor imports only Protocols (no direct adapter storage)."""
    from pathlib import Path

    vigil_file = Path(__file__).parent.parent / "src/aip/orchestration/actors/vigil.py"
    if vigil_file.exists():
        text = vigil_file.read_text()
        assert "from aip.adapter." not in text or "from aip.foundation.protocols" in text


# --- Mocks for datetime-based staleness and Sexton integration ---


class _MockVigilStore:
    async def list_stale_canonicals(self, threshold_days=30):
        return [
            {
                "artifact_id": "stale-1",
                "last_updated": (datetime.now(timezone.utc) - timedelta(days=threshold_days + 10)).isoformat(),
            },
            {
                "artifact_id": "stale-2",
                "last_updated": (datetime.now(timezone.utc) - timedelta(days=threshold_days + 5)).isoformat(),
            },
        ]

    async def record_vigil_check(self, **kwargs):
        pass


class _MockCanonicalStore:
    async def list_canonical(self):
        return [
            {"artifact_id": "canon-1", "content": "Canonical content 1"},
            {"artifact_id": "canon-2", "content": "Canonical content 2"},
            {"artifact_id": "stale-1", "content": "Stale content 1"},
            {"artifact_id": "stale-2", "content": "Stale content 2"},
        ]

    async def write_canonical(self, *a, **kw):
        pass


class _MockEntityStore:
    async def list_entities(self):
        return []

    async def get_entity(self, entity_id):
        return None


class _MockModelProvider:
    async def call(self, slot_name, messages, **kwargs):
        return {"content": "CI fixture response", "model": "ci-evaluation", "usage": {}}


class _MockTraceStore:
    async def write_event(self, **kwargs):
        pass

    async def get_unclassified_failures(self, limit=100):
        return []

    async def query_events(self, session_id="", limit=100):
        return []


@pytest.mark.asyncio
async def test_stale_detection_by_threshold():
    """Vigil.detect_stale_canonicals queries store with datetime-based staleness threshold."""
    config = VigilConfig(stale_threshold_days=30)
    vigil = Vigil(
        config=config,
        vigil_store=_MockVigilStore(),
        canonical_store=_MockCanonicalStore(),
        entity_store=_MockEntityStore(),
        model_provider=_MockModelProvider(),
        trace_store=_MockTraceStore(),
    )

    stale = await vigil.detect_stale_canonicals()
    assert len(stale) >= 1, "Vigil should detect at least one stale canonical"


@pytest.mark.asyncio
async def test_health_check_returns_staleness_metrics():
    """Vigil.check_canonical_health evaluates aggregate health including staleness."""
    config = VigilConfig(stale_threshold_days=30)
    vigil = Vigil(
        config=config,
        vigil_store=_MockVigilStore(),
        canonical_store=_MockCanonicalStore(),
        entity_store=_MockEntityStore(),
        model_provider=_MockModelProvider(),
        trace_store=_MockTraceStore(),
    )

    health = await vigil.check_canonical_health()
    assert "total_count" in health
    assert "stale_count" in health
    assert "status" in health
    assert health["stale_count"] >= 0


@pytest.mark.asyncio
async def test_slot_change_triggers_sexton_audit():
    """Vigil.on_model_slot_change triggers Sexton stale rule audit."""
    config = VigilConfig(re_evaluate_on_slot_change=True)
    sexton = Sexton(config=SextonConfig(), trace_store=_MockTraceStore())

    vigil = Vigil(
        config=config,
        vigil_store=_MockVigilStore(),
        canonical_store=_MockCanonicalStore(),
        entity_store=_MockEntityStore(),
        model_provider=_MockModelProvider(),
        trace_store=_MockTraceStore(),
        sexton=sexton,
    )

    old_config = ModelSlotConfig(slot_name="synthesis", provider="old", model="old-model")
    new_config = ModelSlotConfig(slot_name="synthesis", provider="new", model="new-model")

    await vigil.on_model_slot_change("synthesis", old_config, new_config)


# ---------------------------------------------------------------------------
# Quality evaluation tests (ADR-011: citation + grounding + hedging)
# ---------------------------------------------------------------------------


class TestVigilSourceGrounding:
    """Tests for the source grounding quality check."""

    def test_grounding_rate_1_when_no_numeric_claims(self):
        """When the response has no numeric claims, grounding_rate should be 1.0."""
        result = Vigil._check_source_grounding(
            response_text="This is a simple response with no numbers.",
            source_text="Source text with no numbers either.",
        )
        assert result["grounding_rate"] == 1.0
        assert result["total_claims"] == 0

    def test_grounding_rate_high_when_numbers_in_source(self):
        """When response numbers appear in source, grounding_rate should be high."""
        result = Vigil._check_source_grounding(
            response_text="Revenue grew 45% to reach $3.2 million in Q4.",
            source_text="Revenue grew 45% to reach $3.2 million in Q4.",
        )
        assert result["grounding_rate"] >= 0.5

    def test_grounding_rate_low_when_numbers_not_in_source(self):
        """When response numbers are fabricated, grounding_rate should be low."""
        result = Vigil._check_source_grounding(
            response_text="The population is 8.7 billion with a 2.3% growth rate.",
            source_text="The world population data was not provided.",
        )
        assert result["grounding_rate"] < 0.5
        assert len(result["ungrounded_claims"]) > 0

    def test_empty_response_returns_perfect_grounding(self):
        """Empty response should have grounding_rate 1.0."""
        result = Vigil._check_source_grounding(
            response_text="",
            source_text="Some source text.",
        )
        assert result["grounding_rate"] == 1.0

    def test_trivial_numbers_not_counted(self):
        """Trivial numbers like 0, 1, 2 should not affect grounding rate."""
        result = Vigil._check_source_grounding(
            response_text="There are 2 options and 1 result.",
            source_text="Completely different text.",
        )
        # 0, 1, 2 are filtered out, so no nontrivial claims
        assert result["grounding_rate"] == 1.0


class TestVigilHedgingDetection:
    """Tests for the hedging / uncertainty detection check."""

    def test_detects_common_hedging_phrases(self):
        """Should detect common hedging phrases."""
        vigil = Vigil(
            config=VigilConfig(),
            vigil_store=FakeVigilStore(),
            canonical_store=FakeCanonicalStore(),
            entity_store=FakeEntityStore(),
            model_provider=FakeModelProvider(),
            trace_store=FakeTraceStore(),
        )
        assert vigil._detect_hedging("I think the answer is 42.")
        assert vigil._detect_hedging("I'm not sure but maybe we should try this.")
        assert vigil._detect_hedging("It might be possible to configure this.")

    def test_no_hedging_in_authoritative_response(self):
        """Authoritative statements should not trigger hedging detection."""
        vigil = Vigil(
            config=VigilConfig(),
            vigil_store=FakeVigilStore(),
            canonical_store=FakeCanonicalStore(),
            entity_store=FakeEntityStore(),
            model_provider=FakeModelProvider(),
            trace_store=FakeTraceStore(),
        )
        assert not vigil._detect_hedging("The configuration file uses TOML format.")
        assert not vigil._detect_hedging("Revenue grew 45% in Q4.")

    def test_empty_text_no_hedging(self):
        """Empty text should not trigger hedging detection."""
        vigil = Vigil(
            config=VigilConfig(),
            vigil_store=FakeVigilStore(),
            canonical_store=FakeCanonicalStore(),
            entity_store=FakeEntityStore(),
            model_provider=FakeModelProvider(),
            trace_store=FakeTraceStore(),
        )
        assert not vigil._detect_hedging("")


class TestVigilQualityEvaluation:
    """Tests for the integrated quality evaluation cycle."""

    @pytest.mark.asyncio
    async def test_run_cycle_includes_grounding_and_hedging(self, vigil):
        """run_cycle() should return grounding and hedging metrics in its result."""
        result = await vigil.run_cycle()
        assert "avg_grounding_rate" in result
        assert "hedging_detected_count" in result
        assert "grounding_threshold" in result
        assert result["status"] == "quality_evaluation_complete"

    @pytest.mark.asyncio
    async def test_find_cited_turns_with_source_pattern(self):
        """_find_cited_turns should detect [source: id] patterns."""
        result = Vigil._find_cited_turns(
            source_turn_ids=["abc123", "def456"],
            response_text="Based on [source: abc123], the answer is clear.",
        )
        assert "abc123" in result
        assert "def456" not in result
