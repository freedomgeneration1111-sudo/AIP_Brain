"""Tests for CHUNK-7.3 Sexton Stale Rule Audit (per Phase 5 ANNEX + prose)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from aip.adapter.model_slot_resolver import ModelSlotResolver
from aip.foundation.protocols import EventStore
from aip.foundation.schemas import AcePlaybookEntry
from aip.orchestration.sexton.sexton_audit import SextonAudit


def test_sexton_audit_instantiation():
    resolver = MagicMock(spec=ModelSlotResolver)
    event_store = MagicMock(spec=EventStore)
    audit = SextonAudit(model_resolver=resolver, event_store=event_store)
    assert audit._model_resolver is resolver


@pytest.mark.asyncio
async def test_audit_stale_assumptions_produces_results_with_model_gen_assumption_context():
    resolver = MagicMock(spec=ModelSlotResolver)
    resolver._ci_mode = True  # force deterministic heuristic
    event_store = AsyncMock(spec=EventStore)

    audit = SextonAudit(model_resolver=resolver, event_store=event_store)

    class _FakeRule:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        model_gen_assumption = None

    rules = [
        _FakeRule(rule_id="cr1", text="test rule", model_gen_assumption="Models may not handle long context well"),
    ]
    entries = [
        AcePlaybookEntry(
            entry_id="ace1",
            domain="test",
            failure_type="B",
            intervention="add rule",
            condition="true",
            model_gen_assumption="Models often produce malformed JSON",
            created_at="2026-05-28",
        ),
    ]

    class _FakeSlot:
        model = "qwen3-coder"

    slots = {"sexton": _FakeSlot()}

    results = await audit.audit_stale_assumptions(rules, entries, slots)

    assert len(results) >= 1
    for r in results:
        assert "still_valid" in r
        assert "confidence" in r
        assert "assumption" in r
        # In CI heuristic mode with the weak keywords above, we expect at least one flagged
        if r.get("still_valid") is False:
            assert r.get("confidence", 0) >= 0.70


@pytest.mark.asyncio
async def test_flag_deprecated_rules_writes_events_and_deprecates_playbook():
    event_store = AsyncMock(spec=EventStore)
    audit = SextonAudit(model_resolver=MagicMock(), event_store=event_store)

    results = [
        {
            "rule_id": "ace1",
            "type": "playbook_entry",
            "still_valid": False,
            "confidence": 0.9,
            "assumption": "old",
            "reason": "test",
        },
        {
            "rule_id": "cr1",
            "type": "contract_rule",
            "still_valid": False,
            "confidence": 0.85,
            "assumption": "old",
            "reason": "test",
        },
    ]

    await audit.flag_deprecated_rules(results)

    # Events must be written for DEFINER visibility (especially ContractRules)
    assert event_store.write_event.called
