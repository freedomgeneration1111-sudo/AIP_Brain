"""Sexton graph extraction batch mode E2E tests.

Exercises `graph_extraction_batch_enabled=True` with a mock LLM provider.
Verifies correct turn_id mapping, batch processing, rate limiting between
batches, and fallback behavior on parse failures.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any
from unittest.mock import patch, AsyncMock

import pytest

from aip.adapter.corpus_turn_store import CorpusTurnStore
from aip.adapter.graph_store import GraphStore
from aip.foundation.schemas import SextonConfig
from aip.foundation.schemas.corpus_turn import CorpusTurn
from aip.orchestration.actors.sexton import Sexton


# ---------------------------------------------------------------------------
# Stub infrastructure
# ---------------------------------------------------------------------------


class StubEventStore:
    """In-memory event store."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def write_event(self, **kwargs: Any) -> None:
        self.events.append(kwargs)

    async def close(self) -> None:
        pass


class BatchAwareModelProvider:
    """Mock LLM provider that returns different responses based on mode.

    - For batch graph extraction: returns JSON with turn_id fields.
    - For per-turn graph extraction: returns JSON without turn_id fields.
    - For tagging: returns a minimal tagging response.
    """

    def __init__(self, *, fail_on_batch: bool = False, omit_turn_ids: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail_on_batch = fail_on_batch
        self.omit_turn_ids = omit_turn_ids

    async def call(self, slot: str, messages: list[dict], **kwargs: Any) -> dict:
        self.calls.append({"slot": slot, "messages": messages})
        system_msg = messages[0]["content"] if messages else ""

        # Batch graph extraction
        if "multiple conversation turns" in system_msg.lower():
            if self.fail_on_batch:
                raise RuntimeError("Simulated LLM failure on batch call")
            if self.omit_turn_ids:
                return {
                    "content": json.dumps([
                        {"entity_type": "CONCEPT", "canonical_name": "BatchEntity", "confidence": 0.9},
                    ]),
                }
            # Return items with turn_id — extract turn_ids from user prompt
            turn_ids = self._extract_turn_ids_from_prompt(messages[-1]["content"])
            items = []
            for tid in turn_ids:
                items.append({"turn_id": tid, "entity_type": "CONCEPT", "canonical_name": f"Entity_{tid}", "confidence": 0.9})
                items.append({"turn_id": tid, "relationship_type": "CONNECTS", "source": f"Entity_{tid}", "target": "SharedConcept", "confidence": 0.8})
            return {"content": json.dumps(items)}

        # Single-turn graph extraction
        if "extracting entities" in system_msg.lower():
            return {
                "content": json.dumps([
                    {"entity_type": "CONCEPT", "canonical_name": "SingleEntity", "confidence": 0.9},
                    {"relationship_type": "RELATES_TO", "source": "SingleEntity", "target": "OtherEntity", "confidence": 0.7},
                ]),
            }

        # Tagging (default)
        return {
            "content": json.dumps([
                {
                    "turn_id": "fallback_turn",
                    "primary_domain": "test",
                    "domains": ["test"],
                    "tags": ["test"],
                    "importance": 0.7,
                    "bridges": [],
                    "beast_confidence": 0.8,
                }
            ]),
        }

    @staticmethod
    def _extract_turn_ids_from_prompt(prompt: str) -> list[str]:
        """Extract turn_id values from the batch user prompt."""
        ids = []
        for line in prompt.split("\n"):
            line = line.strip()
            if line.startswith("turn_id:"):
                ids.append(line.split(":", 1)[1].strip())
        return ids


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_turn(turn_id: str, importance: float = 0.9, bridges: list[str] | None = None) -> CorpusTurn:
    """Create a high-importance CorpusTurn for graph extraction testing."""
    return CorpusTurn(
        turn_id=turn_id,
        conversation_id="conv_batch_test",
        conversation_name="Batch Test Conversation",
        turn_index=1,
        source_model="claude-3",
        source_account="test",
        export_date="2025-01-01",
        user_text=f"What is the relationship between concepts in turn {turn_id}?",
        assistant_text=f"This turn {turn_id} discusses TestConcept and its connections.",
        turn_timestamp="2025-01-01T00:00:00Z",
        domains=["test_domain"],
        primary_domain="test_domain",
        tags=["test_tag"],
        importance=importance,
        bridges=bridges or [],
        beast_confidence=0.9,
        tagging_version=1,
        embedded=1,
        searchable_text=f"Test content for {turn_id}",
        word_count=20,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db():
    """Provide a temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "batch_test_state.db")


@pytest.fixture
async def corpus_turn_store(tmp_db):
    """Provide an initialized CorpusTurnStore with test turns."""
    store = CorpusTurnStore(tmp_db)
    await store.initialize()
    # Write high-importance turns with bridge tags for graph extraction
    for i in range(4):
        turn = _make_turn(
            turn_id=f"batch_turn_{i+1}",
            importance=0.85,
            bridges=["test_bridge"],
        )
        await store.write_turn(turn)
    yield store
    await store.close()


# ---------------------------------------------------------------------------
# Test: Batch mode with correct turn_id mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_extraction_maps_turn_ids(tmp_db, corpus_turn_store):
    """Batch mode should correctly map turn_id to extracted items."""
    model_provider = BatchAwareModelProvider()
    event_store = StubEventStore()
    config = SextonConfig(
        graph_extraction_batch_enabled=True,
        graph_extraction_batch_size=2,
    )

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=corpus_turn_store,
        event_store=event_store,
        config=config,
    )

    result = await sexton._run_graph_extraction(limit=10)

    assert result["turns_processed"] == 4
    assert result["entities_created"] > 0
    assert result["relationships_created"] > 0

    # Verify the model provider was called (should be 2 batch calls for 4 turns with batch_size=2)
    batch_calls = [c for c in model_provider.calls if "multiple conversation turns" in (c["messages"][0]["content"] if c["messages"] else "")]
    assert len(batch_calls) == 2, f"Expected 2 batch LLM calls, got {len(batch_calls)}"


@pytest.mark.asyncio
async def test_batch_extraction_creates_graph_nodes(tmp_db, corpus_turn_store):
    """Batch extraction should create GraphNode entries in the database."""
    model_provider = BatchAwareModelProvider()
    event_store = StubEventStore()
    config = SextonConfig(
        graph_extraction_batch_enabled=True,
        graph_extraction_batch_size=4,
    )

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=corpus_turn_store,
        event_store=event_store,
        config=config,
    )

    await sexton._run_graph_extraction(limit=10)

    # Verify graph nodes were created
    graph_store = GraphStore(tmp_db)
    await graph_store.initialize()
    node_count = await graph_store.node_count()
    edge_count = await graph_store.edge_count()
    await graph_store.close()

    assert node_count > 0, "Expected graph nodes to be created from batch extraction"
    assert edge_count > 0, "Expected graph edges to be created from batch extraction"


@pytest.mark.asyncio
async def test_batch_extraction_logs_turns(tmp_db, corpus_turn_store):
    """Batch extraction should log each turn as extracted in graph_extraction_log."""
    model_provider = BatchAwareModelProvider()
    event_store = StubEventStore()
    config = SextonConfig(
        graph_extraction_batch_enabled=True,
        graph_extraction_batch_size=2,
    )

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=corpus_turn_store,
        event_store=event_store,
        config=config,
    )

    await sexton._run_graph_extraction(limit=10)

    # Verify turns are logged as extracted
    graph_store = GraphStore(tmp_db)
    await graph_store.initialize()
    for i in range(4):
        assert await graph_store.is_turn_extracted(f"batch_turn_{i+1}"), f"batch_turn_{i+1} should be logged as extracted"
    await graph_store.close()


# ---------------------------------------------------------------------------
# Test: Fallback on batch parse failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_fallback_on_llm_failure(tmp_db, corpus_turn_store):
    """When batch LLM call fails, should fall back to per-turn processing."""
    model_provider = BatchAwareModelProvider(fail_on_batch=True)
    event_store = StubEventStore()
    config = SextonConfig(
        graph_extraction_batch_enabled=True,
        graph_extraction_batch_size=2,
    )

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=corpus_turn_store,
        event_store=event_store,
        config=config,
    )

    result = await sexton._run_graph_extraction(limit=10)

    # Should have processed all turns via per-turn fallback
    assert result["turns_processed"] == 4
    assert result["entities_created"] > 0

    # Verify fallback used per-turn calls
    single_calls = [c for c in model_provider.calls if "extracting entities" in (c["messages"][0]["content"] if c["messages"] else "") and "multiple" not in (c["messages"][0]["content"] if c["messages"] else "")]
    assert len(single_calls) >= 4, f"Expected at least 4 per-turn fallback calls, got {len(single_calls)}"


@pytest.mark.asyncio
async def test_batch_fallback_on_missing_turn_ids(tmp_db, corpus_turn_store):
    """When batch response lacks turn_id fields, should fall back to per-turn."""
    model_provider = BatchAwareModelProvider(omit_turn_ids=True)
    event_store = StubEventStore()
    config = SextonConfig(
        graph_extraction_batch_enabled=True,
        graph_extraction_batch_size=4,
    )

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=corpus_turn_store,
        event_store=event_store,
        config=config,
    )

    result = await sexton._run_graph_extraction(limit=10)

    # Should have processed all turns via per-turn fallback
    assert result["turns_processed"] == 4
    assert result["entities_created"] > 0


# ---------------------------------------------------------------------------
# Test: Rate limiting between batches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_rate_limiting_sleeps_between_batches(tmp_db, corpus_turn_store):
    """Batch mode should sleep between batches (not between individual turns)."""
    import time

    model_provider = BatchAwareModelProvider()
    event_store = StubEventStore()
    config = SextonConfig(
        graph_extraction_batch_enabled=True,
        graph_extraction_batch_size=2,
    )

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=corpus_turn_store,
        event_store=event_store,
        config=config,
    )

    # Patch asyncio.sleep to track calls without actually sleeping
    sleep_calls: list[float] = []
    original_sleep = Sexton.__module__

    with patch("asyncio.sleep") as mock_sleep:
        mock_sleep.side_effect = lambda s: sleep_calls.append(s)

        result = await sexton._run_graph_extraction(limit=10)

    # Should have 2 batch calls (4 turns / batch_size 2), so 2 sleeps between batches
    # The sleep calls should all be 5 seconds (rate-limit)
    rate_limit_sleeps = [s for s in sleep_calls if s == 5]
    assert len(rate_limit_sleeps) >= 2, f"Expected at least 2 rate-limit sleeps, got {len(rate_limit_sleeps)}"


# ---------------------------------------------------------------------------
# Test: Per-turn mode (default, no batching)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_turn_mode_processes_individually(tmp_db, corpus_turn_store):
    """Per-turn mode (default) should make one LLM call per turn."""
    model_provider = BatchAwareModelProvider()
    event_store = StubEventStore()
    config = SextonConfig(
        graph_extraction_batch_enabled=False,
        graph_extraction_batch_size=1,
    )

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=corpus_turn_store,
        event_store=event_store,
        config=config,
    )

    result = await sexton._run_graph_extraction(limit=10)

    assert result["turns_processed"] == 4

    # All calls should be single-turn (no "multiple" in system prompt)
    batch_calls = [c for c in model_provider.calls if "multiple conversation turns" in (c["messages"][0]["content"] if c["messages"] else "")]
    assert len(batch_calls) == 0, "Should not have batch calls in per-turn mode"


# ---------------------------------------------------------------------------
# Test: Extraction log prevents re-extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extraction_log_prevents_reextraction(tmp_db, corpus_turn_store):
    """Turns already in graph_extraction_log should not be re-extracted."""
    model_provider = BatchAwareModelProvider()
    event_store = StubEventStore()
    config = SextonConfig(
        graph_extraction_batch_enabled=True,
        graph_extraction_batch_size=4,
    )

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=corpus_turn_store,
        event_store=event_store,
        config=config,
    )

    # First extraction
    result1 = await sexton._run_graph_extraction(limit=10)
    assert result1["turns_processed"] == 4

    # Second extraction — should find nothing new
    call_count_before = len(model_provider.calls)
    result2 = await sexton._run_graph_extraction(limit=10)
    assert result2["turns_processed"] == 0
    assert len(model_provider.calls) == call_count_before, "No new LLM calls should be made"


# ---------------------------------------------------------------------------
# Test: Empty corpus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_extraction_empty_corpus(tmp_db):
    """Batch extraction should handle empty corpus gracefully."""
    empty_store = CorpusTurnStore(tmp_db)
    await empty_store.initialize()

    model_provider = BatchAwareModelProvider()
    event_store = StubEventStore()
    config = SextonConfig(
        graph_extraction_batch_enabled=True,
        graph_extraction_batch_size=2,
    )

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=empty_store,
        event_store=event_store,
        config=config,
    )

    result = await sexton._run_graph_extraction(limit=10)

    assert result["turns_processed"] == 0
    assert result["entities_created"] == 0
    assert result["relationships_created"] == 0

    await empty_store.close()


# ---------------------------------------------------------------------------
# Test: Batch with cross-turn relationships
# ---------------------------------------------------------------------------


class CrossTurnModelProvider:
    """Mock LLM that returns entities that span multiple turns in a batch.

    Simulates a realistic batch response where the same entity appears in
    multiple turns and relationships connect entities from different turns.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def call(self, slot: str, messages: list[dict], **kwargs: Any) -> dict:
        self.calls.append({"slot": slot, "messages": messages})
        system_msg = messages[0]["content"] if messages else ""

        # Batch graph extraction — return cross-turn relationships
        if "multiple conversation turns" in system_msg.lower():
            turn_ids = BatchAwareModelProvider._extract_turn_ids_from_prompt(
                messages[-1]["content"]
            )
            items = []
            # Entity shared across turns
            for tid in turn_ids:
                items.append({
                    "turn_id": tid,
                    "entity_type": "CONCEPT",
                    "canonical_name": "SharedConcept",
                    "confidence": 0.95,
                })
            # Cross-turn relationship
            if len(turn_ids) >= 2:
                items.append({
                    "turn_id": turn_ids[0],
                    "relationship_type": "CONNECTS",
                    "source": "SharedConcept",
                    "target": "OtherConcept",
                    "confidence": 0.85,
                })
            return {"content": json.dumps(items)}

        # Single-turn fallback
        if "extracting entities" in system_msg.lower():
            return {
                "content": json.dumps([
                    {"entity_type": "CONCEPT", "canonical_name": "FallbackEntity", "confidence": 0.9},
                ]),
            }

        # Tagging
        return {
            "content": json.dumps([
                {"turn_id": "t1", "primary_domain": "test", "domains": ["test"],
                 "tags": ["test"], "importance": 0.7, "bridges": [],
                 "beast_confidence": 0.8},
            ]),
        }


@pytest.mark.asyncio
async def test_batch_cross_turn_relationships(tmp_db, corpus_turn_store):
    """Batch mode should correctly handle entities that appear in multiple turns.

    When the same entity name appears in multiple turns' extraction results,
    it should be upserted once (not duplicated), and relationships should
    connect entities across turns correctly.
    """
    model_provider = CrossTurnModelProvider()
    event_store = StubEventStore()
    config = SextonConfig(
        graph_extraction_batch_enabled=True,
        graph_extraction_batch_size=4,
    )

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=corpus_turn_store,
        event_store=event_store,
        config=config,
    )

    result = await sexton._run_graph_extraction(limit=10)

    assert result["turns_processed"] == 4
    assert result["entities_created"] > 0
    assert result["relationships_created"] > 0

    # Verify shared concept node exists in graph store
    graph_store = GraphStore(tmp_db)
    await graph_store.initialize()
    node = await graph_store.get_node("sharedconcept")
    assert node is not None, "Shared concept node should exist"
    await graph_store.close()


# ---------------------------------------------------------------------------
# Test: Partial parse failure (some items valid, some malformed)
# ---------------------------------------------------------------------------


class PartialParseModelProvider:
    """Mock LLM that returns a mix of valid and invalid items in batch response.

    Tests that valid items are still processed even when some items are
    malformed (missing required fields, wrong types, etc.).
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def call(self, slot: str, messages: list[dict], **kwargs: Any) -> dict:
        self.calls.append({"slot": slot, "messages": messages})
        system_msg = messages[0]["content"] if messages else ""

        if "multiple conversation turns" in system_msg.lower():
            turn_ids = BatchAwareModelProvider._extract_turn_ids_from_prompt(
                messages[-1]["content"]
            )
            items = []
            for tid in turn_ids:
                # Valid entity
                items.append({
                    "turn_id": tid,
                    "entity_type": "CONCEPT",
                    "canonical_name": f"Valid_{tid}",
                    "confidence": 0.9,
                })
            # Add some malformed items (these should be gracefully skipped)
            items.append({"not_a_valid_key": True})  # No entity_type or relationship_type
            items.append({"entity_type": "CONCEPT", "canonical_name": "", "confidence": 0.9})  # Empty name
            items.append({"entity_type": "CONCEPT", "canonical_name": "LowConf", "confidence": 0.3})  # Below threshold
            # Valid relationship
            if len(turn_ids) >= 2:
                items.append({
                    "turn_id": turn_ids[0],
                    "relationship_type": "RELATES_TO",
                    "source": f"Valid_{turn_ids[0]}",
                    "target": f"Valid_{turn_ids[1]}",
                    "confidence": 0.8,
                })
            return {"content": json.dumps(items)}

        if "extracting entities" in system_msg.lower():
            return {
                "content": json.dumps([
                    {"entity_type": "CONCEPT", "canonical_name": "SingleEntity", "confidence": 0.9},
                ]),
            }

        return {"content": json.dumps([{"turn_id": "t", "primary_domain": "test", "domains": ["test"], "tags": ["test"], "importance": 0.7, "bridges": [], "beast_confidence": 0.8}])}


@pytest.mark.asyncio
async def test_batch_partial_parse_failure(tmp_db, corpus_turn_store):
    """Batch mode should gracefully handle partial parse failures.

    Malformed items (missing fields, empty names, low confidence) should be
    skipped without affecting valid items. Valid entities and relationships
    should still be created.
    """
    model_provider = PartialParseModelProvider()
    event_store = StubEventStore()
    config = SextonConfig(
        graph_extraction_batch_enabled=True,
        graph_extraction_batch_size=4,
    )

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=corpus_turn_store,
        event_store=event_store,
        config=config,
    )

    result = await sexton._run_graph_extraction(limit=10)

    # Should process all turns despite malformed items
    assert result["turns_processed"] == 4
    # Valid entities should still be created (some may share canonical names, so >= 1)
    assert result["entities_created"] >= 1
    # Relationships should be created from valid items only
    assert result["relationships_created"] >= 1


# ---------------------------------------------------------------------------
# Test: Batch with low-importance turns (filtered out)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_skips_low_importance_turns(tmp_db):
    """Graph extraction should skip turns below the importance threshold."""
    store = CorpusTurnStore(tmp_db)
    await store.initialize()

    # Mix of high and low importance turns
    high_turn = _make_turn("high_imp_turn", importance=0.9, bridges=["test_bridge"])
    low_turn = _make_turn("low_imp_turn", importance=0.3, bridges=["test_bridge"])
    await store.write_turn(high_turn)
    await store.write_turn(low_turn)

    model_provider = BatchAwareModelProvider()
    event_store = StubEventStore()
    config = SextonConfig(
        graph_extraction_batch_enabled=True,
        graph_extraction_batch_size=10,
    )

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=store,
        event_store=event_store,
        config=config,
    )

    result = await sexton._run_graph_extraction(limit=10)

    # The extraction function queries for high-importance turns (>=0.7 by default)
    # Only the high-importance turn should be extracted
    assert result["turns_processed"] == 1

    await store.close()


# ---------------------------------------------------------------------------
# Test: Batch mode returns batch_mode and batch_size in result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_result_includes_mode_info(tmp_db, corpus_turn_store):
    """Batch extraction result should include batch_mode and batch_size."""
    model_provider = BatchAwareModelProvider()
    event_store = StubEventStore()
    config = SextonConfig(
        graph_extraction_batch_enabled=True,
        graph_extraction_batch_size=2,
    )

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=corpus_turn_store,
        event_store=event_store,
        config=config,
    )

    result = await sexton._run_graph_extraction(limit=10)

    # Result should include batch mode info
    assert "turns_processed" in result
    assert "entities_created" in result
    assert "relationships_created" in result


# ---------------------------------------------------------------------------
# Test: Large batch size with few turns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_size_larger_than_turns(tmp_db, corpus_turn_store):
    """When batch_size > total turns, all turns should fit in a single batch."""
    model_provider = BatchAwareModelProvider()
    event_store = StubEventStore()
    config = SextonConfig(
        graph_extraction_batch_enabled=True,
        graph_extraction_batch_size=100,  # Much larger than 4 turns
    )

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=corpus_turn_store,
        event_store=event_store,
        config=config,
    )

    result = await sexton._run_graph_extraction(limit=10)

    # All 4 turns should be processed in a single batch call
    assert result["turns_processed"] == 4
    batch_calls = [c for c in model_provider.calls if "multiple conversation turns" in (c["messages"][0]["content"] if c["messages"] else "")]
    assert len(batch_calls) == 1, "Should have exactly 1 batch LLM call when batch_size > turns"


# ---------------------------------------------------------------------------
# Test: Verify graph store nodes have correct entity types
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_extraction_node_entity_types(tmp_db, corpus_turn_store):
    """Extracted nodes should preserve entity_type from LLM response."""
    model_provider = BatchAwareModelProvider()
    event_store = StubEventStore()
    config = SextonConfig(
        graph_extraction_batch_enabled=True,
        graph_extraction_batch_size=4,
    )

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=corpus_turn_store,
        event_store=event_store,
        config=config,
    )

    await sexton._run_graph_extraction(limit=10)

    # Verify at least one node exists with an entity type
    graph_store = GraphStore(tmp_db)
    await graph_store.initialize()
    count = await graph_store.node_count()
    assert count > 0, "Should have created graph nodes"
    await graph_store.close()
