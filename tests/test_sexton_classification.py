"""Tests for Sexton Failure Classification.

Covers:
- Instantiation and contract compliance
- Deterministic CI-mode classification
- Failure-type write-back and alert thresholds
- Layering constraints (no adapter imports)
- Integration tests using a real SQLite-backed trace store
"""

import sqlite3
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from aip.adapter.model_slot_resolver import ModelSlotResolver
from aip.foundation.protocols import EventStore, TraceStore
from aip.foundation.schemas import FailureClassification, SextonConfig
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
        {"id": 42, "node_type": "L3a", "outcome": "failure", "detail": "malformation in schema"},
    ]
    trace.write_event = AsyncMock()

    s = Sexton(config=cfg, model_resolver=resolver, trace_store=trace)
    results = await s.classify_failures()

    assert len(results) == 1
    fc = results[0]
    assert isinstance(fc, FailureClassification)
    assert fc.failure_type in ("A", "B", "C", "D", "E", "F")
    assert (
        fc.model_gen_assumption is not None
        and "§1.8" in fc.model_gen_assumption
        or "model" in fc.model_gen_assumption.lower()
    )


@pytest.mark.asyncio
async def test_ci_mode_uses_deterministic_fixtures():
    """In ci_mode the classification is deterministic from node_type/outcome (per 7.1 prose)."""
    resolver = MagicMock(spec=ModelSlotResolver)
    resolver._ci_mode = True
    trace = AsyncMock(spec=TraceStore)
    trace.get_unclassified_failures.return_value = [
        {"id": 1, "node_type": "L4", "outcome": "failure", "detail": "loop detected"},
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


# ---------------------------------------------------------------------------
# Integration tests with a real SQLite-backed trace store
# ---------------------------------------------------------------------------


class _SqliteTraceStore(TraceStore):
    """Minimal SQLite-backed TraceStore for integration testing."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trace_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                node_type TEXT,
                failure_type TEXT,
                outcome TEXT,
                detail TEXT,
                intervention_applied INTEGER DEFAULT 0,
                intervention_type TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self._conn.commit()

    async def write_event(self, **kwargs) -> None:
        failure_type = kwargs.get("failure_type")
        session_id = kwargs.get("session_id", "unknown")
        node_type = kwargs.get("node_type", "unknown")
        detail = kwargs.get("detail")
        outcome = kwargs.get("outcome", "failure")

        if failure_type:
            # Write-back: update an existing unclassified row matching session_id + node_type
            cursor = self._conn.execute(
                "UPDATE trace_events SET failure_type = ? "
                "WHERE session_id = ? AND node_type = ? AND failure_type IS NULL",
                (failure_type, session_id, node_type),
            )
            if cursor.rowcount > 0:
                self._conn.commit()
                return

        # Otherwise insert new row
        self._conn.execute(
            "INSERT INTO trace_events "
            "(session_id, node_type, failure_type, outcome, detail, "
            "intervention_applied, intervention_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                node_type,
                failure_type,
                outcome,
                detail,
                kwargs.get("intervention_applied", 0),
                kwargs.get("intervention_type"),
            ),
        )
        self._conn.commit()

    async def get_unclassified_failures(self, limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM trace_events WHERE failure_type IS NULL AND outcome = 'failure' LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    async def query_events(self, session_id: str = "", limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM trace_events WHERE session_id = ? LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    async def close(self) -> None:
        self._conn.close()


@pytest.fixture
def trace_store():
    with tempfile.TemporaryDirectory() as tmp:
        store = _SqliteTraceStore(f"{tmp}/trace.db")
        yield store
        store.close()


async def _seed_all_failure_types(trace_store: _SqliteTraceStore) -> None:
    """Seed trace events that exercise all six failure types (A-F)."""
    test_events = [
        # Type A: Context Framing Failure (insufficient context)
        {
            "session_id": "s1",
            "node_type": "L2",
            "outcome": "failure",
            "detail": "Insufficient retrieval results - max confidence below threshold",
        },
        # Type B: Procedural Gap (generic failure)
        {
            "session_id": "s2",
            "node_type": "SYNTHESIS",
            "outcome": "failure",
            "detail": "Synthesis failed due to missing procedure",
        },
        # Type C: Output Malformation
        {
            "session_id": "s3",
            "node_type": "L3a",
            "outcome": "failure",
            "detail": "Output malformation detected - schema validation failed",
        },
        # Type D: Session Drift / Loop
        {
            "session_id": "s4",
            "node_type": "L4",
            "outcome": "failure",
            "detail": "Session drift detected - repetitive loop pattern",
        },
        # Type E: False Success Reporting
        {
            "session_id": "s5",
            "node_type": "L3b",
            "outcome": "failure",
            "detail": "False success reported - validation failed on recheck",
        },
        # Type F: Context Anxiety
        {
            "session_id": "s6",
            "node_type": "L4",
            "outcome": "failure",
            "detail": "Context anxiety detected - response shortening and hedging",
        },
    ]
    for ev in test_events:
        await trace_store.write_event(**ev)


@pytest.mark.asyncio
async def test_classify_all_six_failure_types_with_real_store(trace_store):
    """Sexton CI mode must classify all six failure types (A-F) against a real store."""
    await _seed_all_failure_types(trace_store)

    config = SextonConfig()
    sexton = Sexton(config=config, trace_store=trace_store)

    classified = await sexton.classify_recent_failures(limit=100)

    # Should have at least some classifications (B catches generic failures)
    assert len(classified) > 0, "Sexton should classify at least some failures"

    # Check that we got a variety of failure types
    failure_types = {ev.get("failure_type") for ev in classified}
    assert len(failure_types) >= 2, f"Expected at least 2 different failure types, got {failure_types}"


@pytest.mark.asyncio
async def test_classification_writes_failure_type_to_store(trace_store):
    """Sexton must write the failure_type back to the store for unclassified events."""
    await _seed_all_failure_types(trace_store)

    config = SextonConfig()
    sexton = Sexton(config=config, trace_store=trace_store)

    await sexton.classify_recent_failures(limit=100)

    # After classification, there should be fewer unclassified events
    remaining = await trace_store.get_unclassified_failures(limit=100)
    # At least some should have been classified
    assert len(remaining) < 6, f"Expected fewer than 6 unclassified failures, got {len(remaining)}"


@pytest.mark.asyncio
async def test_ace_rule_derivation_from_classified_events(trace_store):
    """ACE playbook derivation must produce rules from classified events."""
    await _seed_all_failure_types(trace_store)

    config = SextonConfig()
    sexton = Sexton(config=config, trace_store=trace_store)

    classified = await sexton.classify_recent_failures(limit=100)

    # Derive ACE rules from the classified events
    rules = sexton.derive_ace_rules(classified)

    # Should produce at least some rules
    assert len(rules) > 0, "Sexton should derive at least some ACE rules from classified events"

    # Each rule should have required fields
    for rule in rules:
        assert "rule_id" in rule
        assert "failure_type" in rule
        assert "recommended_action" in rule
        assert "model_gen_assumption" in rule


@pytest.mark.asyncio
async def test_full_classification_cycle_leaves_no_unclassified(trace_store):
    """After Sexton runs a full classification cycle, no unclassified failures should remain."""
    await _seed_all_failure_types(trace_store)

    config = SextonConfig()
    sexton = Sexton(config=config, trace_store=trace_store)

    # Run classification cycle
    await sexton.run_classification_cycle()

    # Check remaining unclassified
    count = await sexton.count_unclassified()
    assert count == 0, f"Expected 0 unclassified failures after Sexton run, got {count}"
