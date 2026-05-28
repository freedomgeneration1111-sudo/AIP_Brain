"""Tests for queryable event store (CHUNK-4.4)."""
import pytest

from aip.adapter.event_store_queryable import QueryableEventStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test_events.db")
    s = QueryableEventStore(db_path)
    yield s
    s.close()


@pytest.mark.asyncio
async def test_write_and_query_by_artifact(store):
    await store.write_event("ecs_transition", "actor1", "a1", from_state="SPECIFIED", to_state="GENERATED")
    await store.write_event("ecs_transition", "actor2", "a2", from_state="SPECIFIED", to_state="GENERATED")

    results = await store.query(artifact_id="a1")
    assert len(results) == 1
    assert results[0].artifact_id == "a1"
    assert results[0].from_state == "SPECIFIED"


@pytest.mark.asyncio
async def test_query_by_event_type(store):
    await store.write_event("ecs_transition", "actor1", "a1")
    await store.write_event("review_verdict", "actor2", "a1")

    results = await store.query(event_type="review_verdict")
    assert len(results) == 1
    assert results[0].event_type == "review_verdict"


@pytest.mark.asyncio
async def test_combined_filters(store):
    await store.write_event("ecs_transition", "a", "a1")
    await store.write_event("review_verdict", "b", "a1")
    await store.write_event("review_verdict", "c", "a2")

    results = await store.query(artifact_id="a1", event_type="review_verdict")
    assert len(results) == 1
    assert results[0].actor == "b"


@pytest.mark.asyncio
async def test_limit(store):
    for i in range(5):
        await store.write_event("test", f"actor{i}", "a1")
    results = await store.query(artifact_id="a1", limit=3)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_descending_order(store):
    await store.write_event("test", "first", "a1")
    await store.write_event("test", "second", "a1")
    results = await store.query(artifact_id="a1")
    assert results[0].actor == "second"


@pytest.mark.asyncio
async def test_empty_result(store):
    results = await store.query(artifact_id="nonexistent")
    assert results == []
