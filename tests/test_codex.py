"""Tests for the CODEX / Librarian system (Sprint 12).

Tests cover:
- CodexStore: SQLite-backed source registry, topic map, contradictions, duplicates
- CodexSource: Schema serialization/deserialization
- CodexTopic: Schema serialization/deserialization
- CodexContradiction: Schema serialization/deserialization
- CodexDashboard: Health score computation
- CodexConfig: Configuration defaults
- Librarian: Maintenance cycle (sync, classify, topics, contradictions, staleness, duplicates)
- CLI integration: aip codex commands
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aip.adapter.codex.codex_store import CodexStore
from aip.foundation.schemas.codex import (
    CodexConfig,
    CodexContradiction,
    CodexDashboard,
    CodexSource,
    CodexTopic,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    """Provide a temporary database path."""
    return str(tmp_path / "test_codex.db")


@pytest.fixture
async def store(db_path):
    """Provide an initialized CodexStore."""
    s = CodexStore(db_path=db_path)
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
def sample_source():
    """Provide a sample CodexSource."""
    return CodexSource(
        source_id="src-abc123",
        title="README.md",
        source_type="document",
        source_path="docs/README.md",
        domain="aip",
        topics=["architecture", "overview"],
        status="active",
        content_hash="abcd1234",
        word_count=500,
        turn_count=3,
        first_ingested_at="2026-01-01T00:00:00Z",
        last_updated_at="2026-06-01T00:00:00Z",
    )


@pytest.fixture
def sample_topic():
    """Provide a sample CodexTopic."""
    return CodexTopic(
        topic_id="aip:architecture",
        title="AIP Architecture",
        domain="aip",
        description="Architecture of the AI Poiesis system",
        source_ids=["src-abc123"],
        related_topics=["aip:retrieval"],
        contradiction_count=0,
        staleness_score=0.0,
        last_activity_at="2026-06-01T00:00:00Z",
        is_wiki_page=True,
    )


@pytest.fixture
def sample_contradiction():
    """Provide a sample CodexContradiction."""
    return CodexContradiction(
        contradiction_id="contra-xyz789",
        topic_id="aip:vector_search",
        claim_a="Vector search is not built yet",
        source_a_id="src-readme",
        source_a_title="README.md",
        claim_b="Vector search is working",
        source_b_id="src-status",
        source_b_title="STATUS.md",
        severity="critical",
        status="open",
        context="README says vector search is not built. STATUS says it is working. "
        "CONFIGURATION says sqlite_vss is active. Resolution required.",
    )


# ---------------------------------------------------------------------------
# Schema Tests
# ---------------------------------------------------------------------------


class TestCodexSource:
    """Tests for CodexSource schema."""

    def test_default_values(self):
        source = CodexSource()
        assert source.source_id == ""
        assert source.source_type == "document"
        assert source.status == "active"
        assert source.topics == []
        assert source.metadata == {}

    def test_to_dict_roundtrip(self, sample_source):
        d = sample_source.to_dict()
        restored = CodexSource.from_dict(d)
        assert restored.source_id == sample_source.source_id
        assert restored.title == sample_source.title
        assert restored.source_type == sample_source.source_type
        assert restored.domain == sample_source.domain
        assert restored.topics == sample_source.topics
        assert restored.status == sample_source.status
        assert restored.content_hash == sample_source.content_hash
        assert restored.word_count == sample_source.word_count

    def test_from_dict_missing_fields(self):
        d = {"source_id": "src-123"}
        source = CodexSource.from_dict(d)
        assert source.source_id == "src-123"
        assert source.status == "active"
        assert source.topics == []


class TestCodexTopic:
    """Tests for CodexTopic schema."""

    def test_default_values(self):
        topic = CodexTopic()
        assert topic.topic_id == ""
        assert topic.is_wiki_page is False
        assert topic.contradiction_count == 0
        assert topic.staleness_score == 0.0

    def test_to_dict_roundtrip(self, sample_topic):
        d = sample_topic.to_dict()
        restored = CodexTopic.from_dict(d)
        assert restored.topic_id == sample_topic.topic_id
        assert restored.domain == sample_topic.domain
        assert restored.source_ids == sample_topic.source_ids
        assert restored.related_topics == sample_topic.related_topics
        assert restored.is_wiki_page is True
        assert restored.contradiction_count == 0


class TestCodexContradiction:
    """Tests for CodexContradiction schema."""

    def test_default_values(self):
        c = CodexContradiction()
        assert c.severity == "major"
        assert c.status == "open"

    def test_to_dict_roundtrip(self, sample_contradiction):
        d = sample_contradiction.to_dict()
        restored = CodexContradiction.from_dict(d)
        assert restored.contradiction_id == sample_contradiction.contradiction_id
        assert restored.claim_a == sample_contradiction.claim_a
        assert restored.claim_b == sample_contradiction.claim_b
        assert restored.severity == "critical"
        assert restored.status == "open"


class TestCodexDashboard:
    """Tests for CodexDashboard health score."""

    def test_health_score_empty(self):
        dash = CodexDashboard()
        assert dash.health_score == 1.0  # No sources = perfect health

    def test_health_score_all_active(self):
        dash = CodexDashboard(total_sources=10, active_sources=10)
        assert dash.health_score == 1.0

    def test_health_score_degraded(self):
        dash = CodexDashboard(
            total_sources=10,
            active_sources=7,
            stale_sources=3,
        )
        score = dash.health_score
        assert 0.0 < score < 1.0
        # 0.3 * (3/10) stale penalty
        assert score < 0.95

    def test_health_score_contradictions(self):
        dash = CodexDashboard(
            total_sources=10,
            active_sources=10,
            total_topics=5,
            open_contradictions=3,
        )
        score = dash.health_score
        assert score < 1.0

    def test_health_score_unclassified(self):
        dash = CodexDashboard(
            total_sources=10,
            active_sources=10,
            unclassified_sources=5,
        )
        score = dash.health_score
        assert score < 1.0


class TestCodexConfig:
    """Tests for CodexConfig defaults."""

    def test_defaults(self):
        config = CodexConfig()
        assert config.stale_threshold_days == 90
        assert config.very_stale_threshold_days == 180
        assert config.auto_create_topics is True
        assert config.librarian_model_slot == "sexton"
        assert config.similarity_threshold == 0.85


# ---------------------------------------------------------------------------
# CodexStore Tests
# ---------------------------------------------------------------------------


class TestCodexStoreSourceOps:
    """Tests for CodexStore source operations."""

    @pytest.mark.asyncio
    async def test_upsert_and_get_source(self, store, sample_source):
        await store.upsert_source(sample_source)
        result = await store.get_source(sample_source.source_id)
        assert result is not None
        assert result.source_id == sample_source.source_id
        assert result.title == sample_source.title
        assert result.domain == sample_source.domain

    @pytest.mark.asyncio
    async def test_get_nonexistent_source(self, store):
        result = await store.get_source("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_sources(self, store, sample_source):
        await store.upsert_source(sample_source)
        # Add another
        source2 = CodexSource(
            source_id="src-def456",
            title="ARCHITECTURE.md",
            domain="aip",
            status="active",
        )
        await store.upsert_source(source2)

        all_sources = await store.list_sources()
        assert len(all_sources) == 2

    @pytest.mark.asyncio
    async def test_list_sources_domain_filter(self, store, sample_source):
        await store.upsert_source(sample_source)
        other = CodexSource(source_id="src-xyz", domain="theology_research")
        await store.upsert_source(other)

        aip_sources = await store.list_sources(domain="aip")
        assert len(aip_sources) == 1
        assert aip_sources[0].domain == "aip"

    @pytest.mark.asyncio
    async def test_list_sources_status_filter(self, store, sample_source):
        sample_source.status = "stale"
        await store.upsert_source(sample_source)
        other = CodexSource(source_id="src-active", status="active")
        await store.upsert_source(other)

        stale = await store.list_sources(status="stale")
        assert len(stale) == 1
        assert stale[0].status == "stale"

    @pytest.mark.asyncio
    async def test_find_source_by_hash(self, store, sample_source):
        await store.upsert_source(sample_source)
        result = await store.find_source_by_hash(sample_source.content_hash)
        assert result is not None
        assert result.source_id == sample_source.source_id

    @pytest.mark.asyncio
    async def test_find_source_by_hash_not_found(self, store):
        result = await store.find_source_by_hash("nonexistent_hash")
        assert result is None

    @pytest.mark.asyncio
    async def test_count_sources_by_status(self, store):
        await store.upsert_source(CodexSource(source_id="s1", status="active"))
        await store.upsert_source(CodexSource(source_id="s2", status="active"))
        await store.upsert_source(CodexSource(source_id="s3", status="stale"))

        counts = await store.count_sources_by_status()
        assert counts.get("active") == 2
        assert counts.get("stale") == 1

    @pytest.mark.asyncio
    async def test_count_sources_by_domain(self, store):
        await store.upsert_source(CodexSource(source_id="s1", domain="aip"))
        await store.upsert_source(CodexSource(source_id="s2", domain="aip"))
        await store.upsert_source(CodexSource(source_id="s3", domain="theology_research"))

        counts = await store.count_sources_by_domain()
        assert counts.get("aip") == 2
        assert counts.get("theology_research") == 1

    @pytest.mark.asyncio
    async def test_mark_source_stale(self, store, sample_source):
        await store.upsert_source(sample_source)
        assert sample_source.status == "active"

        await store.mark_source_stale(sample_source.source_id)
        result = await store.get_source(sample_source.source_id)
        assert result.status == "stale"

    @pytest.mark.asyncio
    async def test_mark_source_superseded(self, store, sample_source):
        await store.upsert_source(sample_source)
        await store.mark_source_superseded(sample_source.source_id, "src-new")
        result = await store.get_source(sample_source.source_id)
        assert result.status == "superseded"
        assert result.superseded_by == "src-new"

    @pytest.mark.asyncio
    async def test_get_unclassified_sources(self, store):
        await store.upsert_source(CodexSource(source_id="s1", domain="aip", status="active"))
        await store.upsert_source(CodexSource(source_id="s2", domain="", status="active"))
        await store.upsert_source(CodexSource(source_id="s3", domain="unclassified", status="active"))

        unclassified = await store.get_unclassified_sources()
        assert len(unclassified) == 2

    @pytest.mark.asyncio
    async def test_update_source_status(self, store, sample_source):
        await store.upsert_source(sample_source)
        await store.update_source_status(sample_source.source_id, "quarantined")
        result = await store.get_source(sample_source.source_id)
        assert result.status == "quarantined"


class TestCodexStoreTopicOps:
    """Tests for CodexStore topic operations."""

    @pytest.mark.asyncio
    async def test_upsert_and_get_topic(self, store, sample_topic):
        await store.upsert_topic(sample_topic)
        result = await store.get_topic(sample_topic.topic_id)
        assert result is not None
        assert result.topic_id == sample_topic.topic_id
        assert result.domain == sample_topic.domain
        assert result.is_wiki_page is True

    @pytest.mark.asyncio
    async def test_get_nonexistent_topic(self, store):
        result = await store.get_topic("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_topics(self, store, sample_topic):
        await store.upsert_topic(sample_topic)
        topic2 = CodexTopic(topic_id="aip:retrieval", domain="aip")
        await store.upsert_topic(topic2)

        topics = await store.list_topics()
        assert len(topics) == 2

    @pytest.mark.asyncio
    async def test_list_topics_domain_filter(self, store, sample_topic):
        await store.upsert_topic(sample_topic)
        other = CodexTopic(topic_id="nbcm:methodology", domain="nbcm")
        await store.upsert_topic(other)

        aip_topics = await store.list_topics(domain="aip")
        assert len(aip_topics) == 1

    @pytest.mark.asyncio
    async def test_search_topics(self, store, sample_topic):
        await store.upsert_topic(sample_topic)
        results = await store.search_topics("Architecture")
        assert len(results) >= 1
        assert results[0].topic_id == sample_topic.topic_id

    @pytest.mark.asyncio
    async def test_count_topics_by_domain(self, store):
        await store.upsert_topic(CodexTopic(topic_id="t1", domain="aip"))
        await store.upsert_topic(CodexTopic(topic_id="t2", domain="aip"))
        await store.upsert_topic(CodexTopic(topic_id="t3", domain="nbcm"))

        counts = await store.count_topics_by_domain()
        assert counts.get("aip") == 2
        assert counts.get("nbcm") == 1

    @pytest.mark.asyncio
    async def test_get_topics_with_contradictions(self, store):
        t1 = CodexTopic(topic_id="t1", domain="aip", contradiction_count=2)
        t2 = CodexTopic(topic_id="t2", domain="aip", contradiction_count=0)
        await store.upsert_topic(t1)
        await store.upsert_topic(t2)

        result = await store.get_topics_with_contradictions()
        assert len(result) == 1
        assert result[0].topic_id == "t1"

    @pytest.mark.asyncio
    async def test_get_stale_topics(self, store):
        t1 = CodexTopic(topic_id="t1", staleness_score=0.8)
        t2 = CodexTopic(topic_id="t2", staleness_score=0.1)
        await store.upsert_topic(t1)
        await store.upsert_topic(t2)

        stale = await store.get_stale_topics(staleness_threshold=0.5)
        assert len(stale) == 1
        assert stale[0].topic_id == "t1"

    @pytest.mark.asyncio
    async def test_add_source_to_topic(self, store, sample_topic):
        await store.upsert_topic(sample_topic)
        await store.add_source_to_topic(sample_topic.topic_id, "src-new")

        result = await store.get_topic(sample_topic.topic_id)
        assert "src-new" in result.source_ids

    @pytest.mark.asyncio
    async def test_add_related_topic(self, store):
        t1 = CodexTopic(topic_id="t1", domain="aip")
        t2 = CodexTopic(topic_id="t2", domain="aip")
        await store.upsert_topic(t1)
        await store.upsert_topic(t2)

        await store.add_related_topic("t1", "t2")

        r1 = await store.get_topic("t1")
        r2 = await store.get_topic("t2")
        assert "t2" in r1.related_topics
        assert "t1" in r2.related_topics  # Reverse link

    @pytest.mark.asyncio
    async def test_compute_staleness_scores(self, store):
        # Create a source with a recent update
        recent_source = CodexSource(
            source_id="s-recent",
            domain="aip",
            last_updated_at=datetime.now(timezone.utc).isoformat(),
        )
        old_source = CodexSource(
            source_id="s-old",
            domain="aip",
            last_updated_at=(datetime.now(timezone.utc) - timedelta(days=200)).isoformat(),
        )
        await store.upsert_source(recent_source)
        await store.upsert_source(old_source)

        # Create topics linked to these sources
        fresh_topic = CodexTopic(
            topic_id="t-fresh",
            domain="aip",
            source_ids=["s-recent"],
        )
        stale_topic = CodexTopic(
            topic_id="t-stale",
            domain="aip",
            source_ids=["s-old"],
        )
        empty_topic = CodexTopic(
            topic_id="t-empty",
            domain="aip",
            source_ids=[],
        )
        await store.upsert_topic(fresh_topic)
        await store.upsert_topic(stale_topic)
        await store.upsert_topic(empty_topic)

        config = CodexConfig(stale_threshold_days=90, very_stale_threshold_days=180)
        updated = await store.compute_staleness_scores(config)

        assert updated == 3

        fresh = await store.get_topic("t-fresh")
        stale = await store.get_topic("t-stale")
        empty = await store.get_topic("t-empty")

        assert fresh.staleness_score == 0.0
        assert stale.staleness_score == 1.0
        assert empty.staleness_score == 1.0


class TestCodexStoreContradictionOps:
    """Tests for CodexStore contradiction operations."""

    @pytest.mark.asyncio
    async def test_upsert_and_get_contradiction(self, store, sample_contradiction):
        await store.upsert_contradiction(sample_contradiction)
        result = await store.get_contradiction(sample_contradiction.contradiction_id)
        assert result is not None
        assert result.contradiction_id == sample_contradiction.contradiction_id
        assert result.severity == "critical"
        assert result.status == "open"

    @pytest.mark.asyncio
    async def test_contradiction_updates_topic_count(self, store):
        # Create topic first
        topic = CodexTopic(topic_id="aip:vector_search", domain="aip")
        await store.upsert_topic(topic)

        # Add a contradiction
        c = CodexContradiction(
            contradiction_id="contra-test1",
            topic_id="aip:vector_search",
            claim_a="X",
            source_a_id="s1",
            claim_b="Y",
            source_b_id="s2",
            severity="major",
            status="open",
        )
        await store.upsert_contradiction(c)

        result = await store.get_topic("aip:vector_search")
        assert result.contradiction_count == 1

    @pytest.mark.asyncio
    async def test_list_contradictions(self, store):
        c1 = CodexContradiction(contradiction_id="c1", topic_id="t1", severity="critical", status="open")
        c2 = CodexContradiction(contradiction_id="c2", topic_id="t2", severity="minor", status="open")
        c3 = CodexContradiction(contradiction_id="c3", topic_id="t1", severity="major", status="resolved_correct")
        await store.upsert_contradiction(c1)
        await store.upsert_contradiction(c2)
        await store.upsert_contradiction(c3)

        # All open
        open_c = await store.list_contradictions(status="open")
        assert len(open_c) == 2

        # By severity
        critical = await store.list_contradictions(severity="critical")
        assert len(critical) == 1

        # By topic
        t1_c = await store.list_contradictions(topic_id="t1")
        assert len(t1_c) == 2

    @pytest.mark.asyncio
    async def test_resolve_contradiction(self, store):
        topic = CodexTopic(topic_id="t1", domain="aip")
        await store.upsert_topic(topic)

        c = CodexContradiction(contradiction_id="c1", topic_id="t1", severity="critical", status="open")
        await store.upsert_contradiction(c)

        # Verify topic count
        t = await store.get_topic("t1")
        assert t.contradiction_count == 1

        # Resolve
        await store.resolve_contradiction(
            contradiction_id="c1",
            status="resolved_correct",
            resolved_by="definer",
            resolution_notes="Source A is correct; source B was outdated",
        )

        # Verify resolution
        result = await store.get_contradiction("c1")
        assert result.status == "resolved_correct"
        assert result.resolved_by == "definer"
        assert result.resolved_at != ""
        assert result.resolution_notes != ""

        # Topic count should be updated
        t = await store.get_topic("t1")
        assert t.contradiction_count == 0

    @pytest.mark.asyncio
    async def test_count_contradictions_by_status(self, store):
        topic = CodexTopic(topic_id="t1", domain="aip")
        await store.upsert_topic(topic)

        c1 = CodexContradiction(contradiction_id="c1", topic_id="t1", status="open")
        c2 = CodexContradiction(contradiction_id="c2", topic_id="t1", status="open")
        await store.upsert_contradiction(c1)
        await store.upsert_contradiction(c2)

        # Resolve one
        await store.resolve_contradiction("c1", "resolved_correct", "definer")

        counts = await store.count_contradictions_by_status()
        assert counts.get("open") == 1
        assert counts.get("resolved_correct") == 1

    @pytest.mark.asyncio
    async def test_count_contradictions_by_severity(self, store):
        topic = CodexTopic(topic_id="t1", domain="aip")
        await store.upsert_topic(topic)

        c1 = CodexContradiction(contradiction_id="c1", topic_id="t1", severity="critical", status="open")
        c2 = CodexContradiction(contradiction_id="c2", topic_id="t1", severity="major", status="open")
        await store.upsert_contradiction(c1)
        await store.upsert_contradiction(c2)

        counts = await store.count_contradictions_by_severity()
        assert counts.get("critical") == 1
        assert counts.get("major") == 1


class TestCodexStoreDuplicateOps:
    """Tests for CodexStore duplicate detection operations."""

    @pytest.mark.asyncio
    async def test_add_duplicate_candidate(self, store):
        await store.add_duplicate_candidate("src-a", "src-b", 0.95)
        candidates = await store.list_duplicate_candidates()
        assert len(candidates) == 1
        assert candidates[0]["source_a_id"] == "src-a"
        assert candidates[0]["similarity_score"] == 0.95

    @pytest.mark.asyncio
    async def test_add_duplicate_idempotent(self, store):
        await store.add_duplicate_candidate("src-a", "src-b", 0.95)
        await store.add_duplicate_candidate("src-a", "src-b", 0.90)  # Same pair
        candidates = await store.list_duplicate_candidates()
        assert len(candidates) == 1  # NOT two — UNIQUE constraint

    @pytest.mark.asyncio
    async def test_resolve_duplicate(self, store):
        await store.add_duplicate_candidate("src-a", "src-b", 0.95)
        candidates = await store.list_duplicate_candidates()
        cid = candidates[0]["id"]

        await store.resolve_duplicate(cid, "definer", "merged_into_a")

        resolved = await store.list_duplicate_candidates(status="resolved")
        assert len(resolved) == 1
        assert resolved[0]["resolution"] == "merged_into_a"


class TestCodexStoreDashboard:
    """Tests for CodexStore dashboard and summary."""

    @pytest.mark.asyncio
    async def test_get_dashboard(self, store):
        # Add some data
        await store.upsert_source(CodexSource(source_id="s1", domain="aip", status="active"))
        await store.upsert_source(CodexSource(source_id="s2", domain="aip", status="stale"))
        await store.upsert_topic(CodexTopic(topic_id="t1", domain="aip"))

        dash = await store.get_dashboard()
        assert dash.total_sources == 2
        assert dash.active_sources == 1
        assert dash.stale_sources == 1
        assert dash.total_topics == 1

    @pytest.mark.asyncio
    async def test_get_topic_summary(self, store, sample_source, sample_topic):
        await store.upsert_source(sample_source)
        await store.upsert_topic(sample_topic)

        summary = await store.get_topic_summary(sample_topic.topic_id)
        assert summary["topic_id"] == sample_topic.topic_id
        assert summary["domain"] == "aip"
        assert summary["source_count"] == 1
        assert summary["staleness_label"] in ("fresh", "aging", "stale", "very_stale")

    @pytest.mark.asyncio
    async def test_get_topic_summary_not_found(self, store):
        summary = await store.get_topic_summary("nonexistent")
        assert "error" in summary
        assert summary["error"] == "topic_not_found"

    @pytest.mark.asyncio
    async def test_get_stale_sources(self, store):
        # Add a stale source (last_updated 200 days ago)
        old_date = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        old_source = CodexSource(source_id="s-old", domain="aip", status="active", last_updated_at=old_date)
        recent_source = CodexSource(
            source_id="s-recent",
            domain="aip",
            status="active",
            last_updated_at=datetime.now(timezone.utc).isoformat(),
        )
        await store.upsert_source(old_source)
        await store.upsert_source(recent_source)

        stale = await store.get_stale_sources(threshold_days=90, limit=10)
        assert len(stale) == 1
        assert stale[0].source_id == "s-old"


# ---------------------------------------------------------------------------
# Librarian Tests
# ---------------------------------------------------------------------------


class TestLibrarian:
    """Tests for the Librarian orchestration module."""

    @pytest.mark.asyncio
    async def test_librarian_cycle_no_corpus(self, db_path):
        """Librarian should handle missing corpus_turn_store gracefully."""
        from aip.orchestration.codex.librarian import Librarian

        store = CodexStore(db_path=db_path)
        await store.initialize()

        librarian = Librarian(codex_store=store, corpus_turn_store=None)

        result = await librarian.run_cycle()

        # Sync should be skipped
        assert result["sync"].get("skipped") == "no_corpus_turn_store"
        assert "cycle_elapsed_seconds" in result
        await store.close()

    @pytest.mark.asyncio
    async def test_librarian_cycle_with_corpus(self, db_path):
        """Librarian should sync sources from corpus_turns."""
        from aip.adapter.corpus_turn_store import CorpusTurnStore
        from aip.foundation.schemas.corpus_turn import CorpusTurn
        from aip.orchestration.codex.librarian import Librarian

        # Set up corpus with some turns
        cts = CorpusTurnStore(db_path=db_path)
        await cts.initialize()

        turn = CorpusTurn(
            turn_id="turn-001",
            conversation_id="conv-001",
            conversation_name="Test Conversation",
            turn_index=0,
            source_model="claude",
            source_account="test",
            export_date="2026-06-01",
            source_path="docs/test.md",
            user_text="What is AIP?",
            assistant_text="AIP is an AI Poiesis system.",
            turn_timestamp="2026-06-01T00:00:00Z",
            searchable_text="What is AIP? AIP is an AI Poiesis system.",
            word_count=10,
            primary_domain="aip",
            tags=["architecture"],
            domains=["aip"],
            importance=0.8,
            tagging_version=1,
        )
        await cts.write_turn(turn)

        # Set up librarian
        store = CodexStore(db_path=db_path)
        await store.initialize()

        librarian = Librarian(codex_store=store, corpus_turn_store=cts)
        result = await librarian.run_cycle()

        # Should have synced at least one source
        assert result["sync"].get("new_sources", 0) >= 1
        assert result["cycle_elapsed_seconds"] > 0

        # Check source was created
        sources = await store.list_sources(domain="aip")
        assert len(sources) >= 1

        await store.close()
        await cts.close()

    @pytest.mark.asyncio
    async def test_librarian_staleness_detection(self, db_path):
        """Librarian should detect stale sources."""
        from aip.orchestration.codex.librarian import Librarian

        store = CodexStore(db_path=db_path)
        await store.initialize()

        # Add an old source
        old_date = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        old_source = CodexSource(
            source_id="s-old",
            domain="aip",
            status="active",
            last_updated_at=old_date,
        )
        await store.upsert_source(old_source)

        librarian = Librarian(codex_store=store, corpus_turn_store=None)

        # Run only staleness computation via the cycle
        result = await librarian._compute_staleness()
        assert result.get("sources_marked_stale", 0) >= 1

        # Verify source was marked stale
        src = await store.get_source("s-old")
        assert src.status == "stale"

        await store.close()

    @pytest.mark.asyncio
    async def test_librarian_duplicate_detection(self, db_path):
        """Librarian should detect duplicate sources."""
        from aip.orchestration.codex.librarian import Librarian

        store = CodexStore(db_path=db_path)
        await store.initialize()

        # Add two sources with same content hash
        s1 = CodexSource(source_id="s1", domain="aip", status="active", content_hash="abc123")
        s2 = CodexSource(source_id="s2", domain="aip", status="active", content_hash="abc123")
        await store.upsert_source(s1)
        await store.upsert_source(s2)

        librarian = Librarian(codex_store=store, corpus_turn_store=None)
        result = await librarian._detect_duplicates()

        assert result.get("new_candidates", 0) >= 1

        candidates = await store.list_duplicate_candidates()
        assert len(candidates) >= 1
        assert candidates[0]["similarity_score"] == 1.0  # Exact match

        await store.close()

    @pytest.mark.asyncio
    async def test_librarian_contradiction_detection_heuristic(self, db_path):
        """Librarian should detect contradictions between stale and active sources."""
        from aip.orchestration.codex.librarian import Librarian

        store = CodexStore(db_path=db_path)
        await store.initialize()

        # Create topic with active and stale sources
        topic = CodexTopic(
            topic_id="aip:vector_search",
            domain="aip",
            source_ids=["s-active", "s-stale"],
        )
        await store.upsert_topic(topic)

        active_src = CodexSource(
            source_id="s-active",
            domain="aip",
            status="active",
            title="Current README",
        )
        stale_src = CodexSource(
            source_id="s-stale",
            domain="aip",
            status="stale",
            title="Old README",
            last_updated_at=(datetime.now(timezone.utc) - timedelta(days=100)).isoformat(),
        )
        await store.upsert_source(active_src)
        await store.upsert_source(stale_src)

        librarian = Librarian(codex_store=store, corpus_turn_store=None)
        result = await librarian._detect_contradictions()

        assert result.get("new_contradictions", 0) >= 1

        # Verify contradiction was created
        contradictions = await store.list_contradictions(status="open")
        assert len(contradictions) >= 1

        await store.close()

    @pytest.mark.asyncio
    async def test_librarian_status_summary(self, db_path):
        """Librarian should provide a status summary."""
        from aip.orchestration.codex.librarian import Librarian

        store = CodexStore(db_path=db_path)
        await store.initialize()

        librarian = Librarian(codex_store=store, corpus_turn_store=None)

        status = librarian.get_status_summary()
        assert "cycle_count" in status
        assert "dependencies" in status
        assert status["dependencies"]["codex_store"] is True
        assert status["dependencies"]["corpus_turn_store"] is False

        await store.close()


# ---------------------------------------------------------------------------
# Contradiction Resolution Tests
# ---------------------------------------------------------------------------


class TestContradictionResolution:
    """Tests for the full contradiction lifecycle."""

    @pytest.mark.asyncio
    async def test_full_contradiction_lifecycle(self, store):
        """Test the full lifecycle: detect -> open -> resolve -> verify."""
        # 1. Set up topic and sources
        topic = CodexTopic(
            topic_id="aip:status",
            domain="aip",
            source_ids=["src-readme", "src-status"],
        )
        await store.upsert_topic(topic)
        await store.upsert_source(CodexSource(source_id="src-readme", title="README.md", domain="aip", status="active"))
        await store.upsert_source(CodexSource(source_id="src-status", title="STATUS.md", domain="aip", status="active"))

        # 2. Create contradiction
        c = CodexContradiction(
            contradiction_id="contra-test",
            topic_id="aip:status",
            claim_a="Vector search is not built",
            source_a_id="src-readme",
            source_a_title="README.md",
            claim_b="Vector search is working",
            source_b_id="src-status",
            source_b_title="STATUS.md",
            severity="critical",
            status="open",
            context="README says vector search is not built. STATUS says it is working.",
        )
        await store.upsert_contradiction(c)

        # 3. Verify it's open
        open_c = await store.list_contradictions(status="open")
        assert len(open_c) >= 1
        topic_check = await store.get_topic("aip:status")
        assert topic_check.contradiction_count >= 1

        # 4. Resolve it
        await store.resolve_contradiction(
            contradiction_id="contra-test",
            status="resolved_outdated",
            resolved_by="definer",
            resolution_notes="README was outdated; STATUS reflects current state. CONFIGURATION confirms sqlite_vss is active.",
        )

        # 5. Verify resolution
        resolved = await store.get_contradiction("contra-test")
        assert resolved.status == "resolved_outdated"
        assert resolved.resolved_by == "definer"
        assert "sqlite_vss" in resolved.resolution_notes

        # 6. Verify topic count updated
        topic_final = await store.get_topic("aip:status")
        assert topic_final.contradiction_count == 0


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case tests for the CODEX system."""

    @pytest.mark.asyncio
    async def test_source_with_no_domain(self, store):
        """Sources without domains should be handled gracefully."""
        source = CodexSource(source_id="s-nodomain", domain="")
        await store.upsert_source(source)
        result = await store.get_source("s-nodomain")
        assert result.domain == ""

    @pytest.mark.asyncio
    async def test_topic_with_no_sources(self, store):
        """Topics with no sources should have high staleness."""
        topic = CodexTopic(topic_id="t-empty", domain="aip", source_ids=[])
        await store.upsert_topic(topic)

        config = CodexConfig()
        await store.compute_staleness_scores(config)

        result = await store.get_topic("t-empty")
        assert result.staleness_score == 1.0  # Max staleness

    @pytest.mark.asyncio
    async def test_concurrent_source_updates(self, store):
        """Multiple upserts to the same source should be safe."""
        for i in range(10):
            source = CodexSource(
                source_id="s-concurrent",
                domain="aip",
                word_count=i * 100,
            )
            await store.upsert_source(source)

        result = await store.get_source("s-concurrent")
        assert result.word_count == 900  # Last write wins

    @pytest.mark.asyncio
    async def test_large_topic_source_list(self, store):
        """Topics with many sources should be handled efficiently."""
        source_ids = [f"src-{i}" for i in range(100)]
        topic = CodexTopic(
            topic_id="t-large",
            domain="aip",
            source_ids=source_ids,
        )
        await store.upsert_topic(topic)

        result = await store.get_topic("t-large")
        assert len(result.source_ids) == 100

    @pytest.mark.asyncio
    async def test_dashboard_empty_database(self, store):
        """Dashboard should work with an empty database."""
        dash = await store.get_dashboard()
        assert dash.total_sources == 0
        assert dash.total_topics == 0
        assert dash.health_score == 1.0  # Empty = healthy

    @pytest.mark.asyncio
    async def test_update_source_preserves_fields(self, store):
        """Updating a source should preserve fields not explicitly changed."""
        original = CodexSource(
            source_id="s-preserve",
            domain="aip",
            title="Original Title",
            word_count=100,
            first_ingested_at="2026-01-01T00:00:00Z",
        )
        await store.upsert_source(original)

        # Update only word_count
        updated = CodexSource(
            source_id="s-preserve",
            domain="aip",
            title="Original Title",
            word_count=200,
            first_ingested_at="2026-01-01T00:00:00Z",
        )
        await store.upsert_source(updated)

        result = await store.get_source("s-preserve")
        assert result.word_count == 200
        assert result.title == "Original Title"
