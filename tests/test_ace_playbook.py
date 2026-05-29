"""Tests for ACE Playbook."""

import os
import tempfile

import pytest

from aip.foundation.schemas import AcePlaybookEntry, FailureClassification
from aip.orchestration.ace_playbook import AcePlaybook


def test_ace_playbook_entry_carries_model_gen_assumption():
    entry = AcePlaybookEntry(
        entry_id="ace-001",
        domain="test",
        failure_type="B",
        intervention="add playbook entry",
        condition="failure_type == 'B'",
        model_gen_assumption="Models benefit from explicit procedural rules (per §1.8)",
        created_at="2026-05-28T00:00:00Z",
    )
    assert entry.model_gen_assumption is not None


@pytest.mark.asyncio
async def test_ace_playbook_crud_and_load():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "test_ace.db")
        pb = AcePlaybook(db, {"auto_derive": False})
        entry = AcePlaybookEntry(
            entry_id="ace-test-1",
            domain="sw_arch",
            failure_type="A",
            intervention="strengthen contract",
            condition="domain == 'sw_arch'",
            model_gen_assumption="Test assumption",
            created_at="2026-05-28T00:00:00Z",
        )
        await pb.add_entry(entry)
        loaded = await pb.load_playbook(domain="sw_arch")
        assert len(loaded) == 1
        assert loaded[0].failure_type == "A"

        await pb.deprecate_entry("ace-test-1", "test deprecation")
        loaded2 = await pb.load_playbook(domain="sw_arch")
        assert len(loaded2) == 0  # deprecated excluded


@pytest.mark.asyncio
async def test_derive_from_classification_7_1_output():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "test_ace.db")
        pb = AcePlaybook(db, {"auto_derive": True, "min_confidence": 0.5})
        fc = FailureClassification(
            trace_event_id=99,
            failure_type="C",
            confidence=0.92,
            rationale="malformed output",
            model_slot_used="sexton",
            tokens_consumed=10,
            model_gen_assumption="Models may produce invalid JSON (per §1.8)",
            classified_at="2026-05-28T00:00:00Z",
        )
        trace_event = {"domain": "code_gen", "node_type": "L2"}
        derived = await pb.derive_from_classification(fc, trace_event)
        assert derived is not None
        assert derived.failure_type == "C"
        assert "JSON" in (derived.model_gen_assumption or "")
        # auto-promoted
        active = await pb.get_active_entries("code_gen", "C")
        assert len(active) >= 1
