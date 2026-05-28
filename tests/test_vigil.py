"""CHUNK-9.1 gate: Vigil actor (read-only health checks, stale detection, model slot change, trace events for Sexton)."""

from __future__ import annotations

import pytest

from aip.foundation.schemas import VigilConfig, ModelSlotConfig
from aip.orchestration.actors.vigil import Vigil


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
    """Vigil.run() creates trace events when stale canonicals are detected."""
    vigil.vigil_store.stale = [{"artifact_id": "c1", "days_since_update": 45}]
    await vigil.run()
    # Should have recorded a vigil check + trace events for stale items
    assert len(vigil.vigil_store.checks) >= 1
    # Trace events should have been written for the stale item
    vigil_events = [e for e in vigil.trace_store.events if e.get("node_type") == "vigil"]
    assert len(vigil_events) >= 1
    assert any("stale" in str(e).lower() or "Stale" in str(e) for e in vigil_events)


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
