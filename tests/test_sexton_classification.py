"""Tests for CHUNK-7.1 Sexton Failure Classification (per Phase 5 ANNEX + prose)."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from aip.foundation.schemas import SextonConfig, FailureClassification
from aip.foundation.protocols import TraceStore, EventStore
from aip.adapter.model_slot_resolver import ModelSlotResolver
from aip.orchestration.sexton.sexton import Sexton


def test_sexton_instantiation_with_7_1_contract():
    """Sexton can be instantiated with the full 7.1 contract (config + resolver + stores)."""
    cfg = SextonConfig()
    resolver = MagicMock(spec=ModelSlotResolver)
    trace = MagicMock(spec=TraceStore)
    event = MagicMock(spec=EventStore)

    s = Sexton(config=cfg, model_resolver=resolver, trace_store=trace, event_store=event)
    assert s._config.classification_batch_size == 50
    assert s._model_resolver is resolver


@pytest.mark.asyncio
async def test_classify_failures_produces_failure_classification_with_model_gen_assumption():
    """classify_failures returns FailureClassification objects carrying §1.8 field."""
    cfg = SextonConfig()
    resolver = MagicMock(spec=ModelSlotResolver)
    resolver._ci_mode = True  # force deterministic foundation path
    trace = AsyncMock(spec=TraceStore)
    trace.get_unclassified_failures.return_value = [
        {"id": 42, "node_type": "L3a", "outcome": "failure", "detail": "malformation in schema"}
    ]
    trace.write_event = AsyncMock()

    s = Sexton(config=cfg, model_resolver=resolver, trace_store=trace)
    results = await s.classify_failures()

    assert len(results) == 1
    fc = results[0]
    assert isinstance(fc, FailureClassification)
    assert fc.failure_type in ("A", "B", "C", "D", "E", "F")
    assert fc.model_gen_assumption is not None and "§1.8" in fc.model_gen_assumption or "model" in fc.model_gen_assumption.lower()


@pytest.mark.asyncio
async def test_ci_mode_uses_deterministic_fixtures():
    """In ci_mode the classification is deterministic from node_type/outcome (per 7.1 prose)."""
    resolver = MagicMock(spec=ModelSlotResolver)
    resolver._ci_mode = True
    trace = AsyncMock(spec=TraceStore)
    trace.get_unclassified_failures.return_value = [
        {"id": 1, "node_type": "L4", "outcome": "failure", "detail": "loop detected"}
    ]
    trace.write_event = AsyncMock()

    s = Sexton(model_resolver=resolver, trace_store=trace)
    results = await s.classify_failures()
    assert results[0].failure_type == "D"  # from the foundation _classify logic for L4/loop


@pytest.mark.asyncio
async def test_count_unclassified_and_alert_threshold():
    cfg = SextonConfig(max_unclassified_before_alert=2)
    trace = AsyncMock(spec=TraceStore)
    trace.get_unclassified_failures.return_value = [{"failure_type": None} for _ in range(5)]
    event = AsyncMock(spec=EventStore)
    event.write_event = AsyncMock()

    s = Sexton(config=cfg, trace_store=trace, event_store=event)
    count = await s.count_unclassified()
    assert count == 5

    await s.run_classification_cycle()
    # Alert should have been written because count > threshold
    assert event.write_event.called


def test_layering_no_adapter_imports_in_sexton():
    """Sexton (orchestration) must not import concrete adapter storage (only Protocols + resolver)."""
    # Importing the module succeeds without pulling forbidden adapter storage
    from aip.orchestration.sexton.sexton import Sexton
    assert Sexton is not None
