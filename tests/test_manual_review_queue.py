"""Tests for MANUAL review queue store.

Verifies that:
1. MANUAL mode creates pending review queue item.
2. Pending items can be listed.
3. Approval records actor, timestamp, notes, and decision.
4. Rejection blocks promotion/commit.
5. Restart preserves pending review item.
6. Non-definer/unauthorized actor cannot approve.
7. No always-approve production path remains.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from aip.adapter.review_queue_store import ReviewQueueStore


@pytest.fixture
def db_path():
    """Provide a temporary database path for each test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


# --- Test: MANUAL mode creates pending review queue item ---


async def test_enqueue_creates_pending_item(db_path):
    """Enqueuing a review item creates a pending entry."""
    store = ReviewQueueStore(db_path)
    await store.initialize()

    item_id = await store.enqueue(
        artifact_id="art-001",
        reason="Manual review required — evaluation passed but ci_fixture=True in production",
        context={"validation_passed": True, "eval_passed": True, "eval_is_fixture": True},
    )
    assert item_id > 0

    item = await store.get_item(item_id)
    assert item is not None
    assert item["status"] == "pending"
    assert item["artifact_id"] == "art-001"
    assert item["reason"] != ""
    await store.close()


# --- Test: Pending items can be listed ---


async def test_list_pending_returns_all_pending(db_path):
    """List pending returns all pending items."""
    store = ReviewQueueStore(db_path)
    await store.initialize()

    await store.enqueue(artifact_id="art-001", reason="Review 1")
    await store.enqueue(artifact_id="art-002", reason="Review 2")
    await store.enqueue(artifact_id="art-003", reason="Review 3")

    pending = await store.list_pending()
    assert len(pending) == 3
    assert all(item["status"] == "pending" for item in pending)
    await store.close()


# --- Test: Approval records actor, timestamp, notes, and decision ---


async def test_approval_records_decision(db_path):
    """Approving a review item records actor, timestamp, notes, and decision."""
    store = ReviewQueueStore(db_path)
    await store.initialize()

    item_id = await store.enqueue(artifact_id="art-001", reason="Manual review")
    result = await store.decide(
        item_id=item_id,
        decision="approved",
        decided_by="definer",
        notes="Verified evaluation results are genuine",
    )

    assert result["ok"] is True
    assert result["decision"] == "approved"
    assert result["decided_by"] == "definer"
    assert result["decided_at"] != ""

    # Verify the item is no longer pending
    item = await store.get_item(item_id)
    assert item["status"] == "approved"
    assert item["decided_by"] == "definer"
    assert item["decision_notes"] == "Verified evaluation results are genuine"
    await store.close()


# --- Test: Rejection blocks promotion ---


async def test_rejection_blocks_promotion(db_path):
    """Rejecting a review item records the rejection and blocks promotion."""
    store = ReviewQueueStore(db_path)
    await store.initialize()

    item_id = await store.enqueue(artifact_id="art-001", reason="Suspicious eval scores")
    result = await store.decide(
        item_id=item_id,
        decision="rejected",
        decided_by="definer",
        notes="CI fixture scores detected in production — cannot promote",
    )

    assert result["ok"] is True
    assert result["decision"] == "rejected"

    # Verify the item is rejected
    item = await store.get_item(item_id)
    assert item["status"] == "rejected"

    # Pending list should not include rejected items
    pending = await store.list_pending()
    assert all(p["artifact_id"] != "art-001" for p in pending)
    await store.close()


# --- Test: Restart preserves pending review item ---


async def test_restart_preserves_pending_item(db_path):
    """Pending review items survive a process restart (store close + reopen)."""
    store1 = ReviewQueueStore(db_path)
    await store1.initialize()
    await store1.enqueue(artifact_id="art-001", reason="Needs review")
    await store1.close()

    store2 = ReviewQueueStore(db_path)
    await store2.initialize()
    pending = await store2.list_pending()
    assert len(pending) == 1
    assert pending[0]["artifact_id"] == "art-001"
    assert pending[0]["reason"] == "Needs review"
    await store2.close()


# --- Test: Non-definer cannot approve ---


async def test_non_definer_cannot_approve(db_path):
    """Non-DEFINER actors are denied approval/rejection."""
    store = ReviewQueueStore(db_path)
    await store.initialize()

    item_id = await store.enqueue(artifact_id="art-001", reason="Review needed")

    with pytest.raises(PermissionError, match="Only DEFINER"):
        await store.decide(item_id=item_id, decision="approved", decided_by="collaborator")

    # Item should still be pending
    item = await store.get_item(item_id)
    assert item["status"] == "pending"
    await store.close()


# --- Test: No always-approve path ---


async def test_no_auto_approve(db_path):
    """There is no auto-approve path — every approval requires explicit DEFINER action."""
    store = ReviewQueueStore(db_path)
    await store.initialize()

    item_id = await store.enqueue(artifact_id="art-001", reason="Manual review")

    # Without explicit decision, item stays pending
    item = await store.get_item(item_id)
    assert item["status"] == "pending"

    # It does NOT auto-approve
    pending = await store.list_pending()
    assert len(pending) == 1
    await store.close()


# --- Test: Invalid decision rejected ---


async def test_invalid_decision_rejected(db_path):
    """Only 'approved' or 'rejected' decisions are accepted."""
    store = ReviewQueueStore(db_path)
    await store.initialize()

    item_id = await store.enqueue(artifact_id="art-001", reason="Review")

    with pytest.raises(ValueError, match="Invalid decision"):
        await store.decide(item_id=item_id, decision="maybe", decided_by="definer")

    await store.close()


# --- Test: Cannot decide already-decided item ---


async def test_cannot_decide_twice(db_path):
    """Cannot approve/reject an item that already has a decision."""
    store = ReviewQueueStore(db_path)
    await store.initialize()

    item_id = await store.enqueue(artifact_id="art-001", reason="Review")
    await store.decide(item_id=item_id, decision="approved", decided_by="definer")

    result = await store.decide(item_id=item_id, decision="rejected", decided_by="definer")
    assert result["ok"] is False
    assert result["error"]["code"] == "ALREADY_DECIDED"
    await store.close()


# --- Test: Context is preserved ---


async def test_context_preserved(db_path):
    """Context dict is stored and retrieved correctly."""
    store = ReviewQueueStore(db_path)
    await store.initialize()

    context = {
        "validation_passed": True,
        "eval_passed": True,
        "eval_is_fixture": True,
        "artifact_summary": "Test artifact",
    }

    item_id = await store.enqueue(
        artifact_id="art-001",
        reason="Manual review required",
        context=context,
    )

    item = await store.get_item(item_id)
    assert item["context_json"]["validation_passed"] is True
    assert item["context_json"]["eval_is_fixture"] is True
    await store.close()
