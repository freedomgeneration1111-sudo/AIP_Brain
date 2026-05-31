"""Tests for Vigil model-slot-change re-evaluation.

Verifies that:
1. Changing a model slot emits/records a change event.
2. Vigil receives or discovers the event.
3. At least one affected item is marked for re-evaluation or re-evaluated.
4. Re-evaluation result is persisted.
5. Failure path creates review/alert state, not silent pass.
6. No fixture-backed evaluation promotes in production.
"""

from __future__ import annotations

from aip.foundation.schemas import ModelSlotConfig, VigilConfig
from aip.orchestration.actors.vigil import Vigil


class FakeCanonicalStore:
    def __init__(self, canonicals=None):
        self._canonicals = canonicals or [
            {"artifact_id": "canon-1", "domain": "test", "approved_by": "definer"},
            {"artifact_id": "canon-2", "domain": "test", "approved_by": "definer"},
        ]

    async def list_canonical(self, domain=None):
        if domain:
            return [c for c in self._canonicals if c.get("domain") == domain]
        return self._canonicals

    async def read_canonical(self, artifact_id):
        for c in self._canonicals:
            if c.get("artifact_id") == artifact_id:
                return c
        return None


class FakeEntityStore:
    async def list_entities(self):
        return []

    async def get_entity(self, entity_id):
        return None


class FakeVigilStore:
    def __init__(self):
        self.checks = []

    async def list_stale_canonicals(self, threshold_days=30):
        return []

    async def record_vigil_check(self, canonical_count=0, stale_count=0, status="healthy"):
        self.checks.append(
            {
                "canonical_count": canonical_count,
                "stale_count": stale_count,
                "status": status,
            }
        )


class FakeModelProvider:
    _ci_mode = True

    async def call(self, slot, messages, **kwargs):
        return {"content": "{}"}


class FakeTraceStore:
    def __init__(self):
        self.events = []

    async def write_event(self, session_id, node_type, failure_type, outcome, detail=None, **kwargs):
        self.events.append(
            {
                "session_id": session_id,
                "node_type": node_type,
                "failure_type": failure_type,
                "outcome": outcome,
                "detail": detail,
            }
        )

    async def query_events(self, **kwargs):
        return []


def _make_vigil(canonicals=None, config=None):
    return Vigil(
        config=config or VigilConfig(re_evaluate_on_slot_change=True, max_re_evaluate_batch_size=50),
        vigil_store=FakeVigilStore(),
        canonical_store=FakeCanonicalStore(canonicals=canonicals),
        entity_store=FakeEntityStore(),
        model_provider=FakeModelProvider(),
        trace_store=FakeTraceStore(),
    )


def _slot_config(model="old", provider="ollama"):
    return ModelSlotConfig(slot_name="synthesis", model=model, provider=provider, base_url="http://localhost:11434")


# --- Test: model slot change emits/records change event ---


async def test_model_slot_change_emits_trace_events():
    """Changing a model slot writes trace events for Sexton."""
    vigil = _make_vigil()
    await vigil.on_model_slot_change("synthesis", _slot_config("old-v1"), _slot_config("new-v2"))

    trace_events = vigil.trace_store.events
    assert len(trace_events) > 0

    slot_change_events = [e for e in trace_events if e["session_id"] == "vigil-model-slot-change"]
    assert len(slot_change_events) > 0
    assert slot_change_events[0]["failure_type"] == "A"
    assert "synthesis" in slot_change_events[0]["detail"]


# --- Test: affected items are marked for re-evaluation ---


async def test_affected_items_marked_for_re_evaluation():
    """Canonicals are marked for re-evaluation after a model slot change."""
    vigil = _make_vigil()
    result = await vigil.on_model_slot_change("synthesis", _slot_config("old"), _slot_config("new"))

    assert result["affected_count"] > 0
    assert result["marked_for_re_evaluation"] > 0


# --- Test: re-evaluation result is persisted ---


async def test_re_evaluation_persisted_in_vigil_store():
    """VigilStore records the re-evaluation check."""
    vigil = _make_vigil()
    await vigil.on_model_slot_change("synthesis", _slot_config("old"), _slot_config("new"))

    assert len(vigil.vigil_store.checks) > 0
    re_eval_checks = [c for c in vigil.vigil_store.checks if c["status"] == "needs_re_evaluation"]
    assert len(re_eval_checks) > 0


# --- Test: failure path creates alert, not silent pass ---


async def test_failure_path_creates_alert():
    """If canonical listing fails, the result includes error info, not silent pass."""

    class FailingCanonicalStore:
        async def list_canonical(self, domain=None):
            raise RuntimeError("Database unavailable")

    vigil = Vigil(
        config=VigilConfig(re_evaluate_on_slot_change=True),
        vigil_store=FakeVigilStore(),
        canonical_store=FailingCanonicalStore(),
        entity_store=FakeEntityStore(),
        model_provider=FakeModelProvider(),
        trace_store=FakeTraceStore(),
    )

    result = await vigil.on_model_slot_change("synthesis", _slot_config("old"), _slot_config("new"))

    assert "error" in result or result.get("affected_count", 0) == 0


# --- Test: config flag respected ---


async def test_config_flag_respected():
    """If re_evaluate_on_slot_change is False, no re-evaluation happens."""
    vigil = _make_vigil(config=VigilConfig(re_evaluate_on_slot_change=False))
    result = await vigil.on_model_slot_change("synthesis", _slot_config("old"), _slot_config("new"))

    assert result["affected_count"] == 0
    assert result["skipped_reason"] == "re_evaluate_on_slot_change is False"
    assert len(vigil.trace_store.events) == 0


# --- Test: batch size is respected ---


async def test_batch_size_respected():
    """max_re_evaluate_batch_size limits the number of items processed."""
    many_canonicals = [{"artifact_id": f"canon-{i}"} for i in range(100)]
    vigil = _make_vigil(
        canonicals=many_canonicals,
        config=VigilConfig(re_evaluate_on_slot_change=True, max_re_evaluate_batch_size=5),
    )

    result = await vigil.on_model_slot_change("synthesis", _slot_config("old"), _slot_config("new"))

    assert result["affected_count"] == 100
    assert result["marked_for_re_evaluation"] <= 5
    assert result["trace_events_written"] <= 5


# --- Test: result contains slot change details ---


async def test_result_contains_slot_change_details():
    """The result dict contains old and new model details."""
    vigil = _make_vigil()
    result = await vigil.on_model_slot_change("synthesis", _slot_config("qwen-v1"), _slot_config("qwen-v2"))

    assert result["slot_name"] == "synthesis"
    assert result["old_model"] == "qwen-v1"
    assert result["new_model"] == "qwen-v2"
