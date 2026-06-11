"""Sexton Wiki + Graph E2E verification tests.

Deterministic, zero-token, no network, no LLM.
Uses stub stores and a stub model provider that returns canned responses
to exercise the full Sexton vigil cycle: tagging → embedding → wiki → graph.
Verifies wiki artifacts are written and graph nodes/edges are created.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any
from unittest.mock import patch

import pytest

from aip.adapter.corpus_turn_store import CorpusTurnStore
from aip.foundation.protocols import (
    ArtifactStore,
    EcsStore,
    EmbeddingProvider,
    LexicalStore,
    VectorStore,
)
from aip.foundation.schemas import Chunk, SextonConfig
from aip.orchestration.actors.domain_registry import (
    ConnectorEntry,
    DomainEntry,
    DomainRegistry,
)
from aip.orchestration.actors.sexton import Sexton

# ---------------------------------------------------------------------------
# Stub stores
# ---------------------------------------------------------------------------


class StubArtifactStore(ArtifactStore):
    """In-memory artifact store for testing wiki article creation."""

    def __init__(self) -> None:
        self._artifacts: dict[str, dict] = {}

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def write(self, artifact_id: str, content: str, metadata: dict | None = None) -> None:
        self._artifacts[artifact_id] = {
            "id": artifact_id,
            "content": content,
            "metadata": metadata or {},
            "created_at": "2025-01-01T00:00:00Z",
        }

    async def read(self, artifact_id: str) -> dict | None:
        return self._artifacts.get(artifact_id)

    async def list_artifacts_by_metadata(self, key: str, value: Any, limit: int = 100) -> list[dict]:
        return [a for a in self._artifacts.values() if a.get("metadata", {}).get(key) == value][:limit]

    async def list_artifacts_by_domain(self, domain: str, limit: int = 100) -> list[dict]:
        return [a for a in self._artifacts.values() if a.get("metadata", {}).get("domain") == domain][:limit]

    async def delete(self, artifact_id: str) -> None:
        self._artifacts.pop(artifact_id, None)

    def get_all(self) -> dict[str, dict]:
        return dict(self._artifacts)


class StubEcsStore(EcsStore):
    """In-memory ECS store for testing transitions."""

    def __init__(self) -> None:
        self._states: dict[str, str] = {}

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def transition(
        self,
        artifact_id: str,
        from_state: str | None,
        to_state: str,
        actor: str = "",
        reason: str = "",
    ) -> None:
        self._states[artifact_id] = to_state

    async def current_state(self, artifact_id: str) -> str | None:
        return self._states.get(artifact_id)


class StubEmbeddingProvider(EmbeddingProvider):
    """Deterministic embedding provider that returns a fixed vector."""

    def __init__(self, dim: int = 8) -> None:
        self._dim = dim

    async def embed(self, text: str) -> list[float]:
        return [0.1] * self._dim


class StubVectorStore(VectorStore):
    """In-memory vector store for testing embedding pass."""

    def __init__(self) -> None:
        self._vectors: dict[str, dict] = {}

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def upsert(
        self,
        id: str,
        embedding: list[float],
        content: str,
        metadata: dict[str, Any] | None = None,
        domain: str | None = None,
    ) -> None:
        self._vectors[id] = {
            "embedding": embedding,
            "content": content,
            "metadata": metadata or {},
            "domain": domain,
        }

    async def retrieve(
        self,
        query_vector: list[float],
        domain: str | None = None,
        top_k: int = 10,
    ) -> list[Chunk]:
        results = []
        for vid, v in self._vectors.items():
            if domain and v["domain"] != domain:
                continue
            results.append(
                Chunk(
                    id=vid,
                    content=v["content"],
                    score=0.9,
                    metadata=v["metadata"],
                    domain=v["domain"] or "",
                )
            )
        return results[:top_k]

    async def store(self, chunk: Chunk) -> str:
        self._vectors[chunk.id] = {
            "embedding": [],
            "content": chunk.content or "",
            "metadata": chunk.metadata or {},
            "domain": chunk.domain,
        }
        return chunk.id

    async def delete(self, id: str) -> None:
        self._vectors.pop(id, None)

    async def count(self, domain: str | None = None) -> int:
        if domain:
            return sum(1 for v in self._vectors.values() if v["domain"] == domain)
        return len(self._vectors)


class StubLexicalStore(LexicalStore):
    """In-memory lexical store."""

    def __init__(self) -> None:
        self._docs: dict[str, dict] = {}

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def index_document(self, doc_id: str, content: str, domain: str, metadata: dict | None = None) -> None:
        self._docs[doc_id] = {
            "id": doc_id,
            "content": content,
            "domain": domain,
            "metadata": metadata or {},
        }

    async def search(self, query: str, domain: str | None = None, limit: int = 10) -> list[Chunk]:
        return []

    async def delete_document(self, doc_id: str) -> None:
        self._docs.pop(doc_id, None)


class StubModelProvider:
    """Deterministic model provider that returns canned LLM responses.

    Inspects the system prompt to determine what kind of response to give:
    - For wiki: returns a markdown wiki article.
    - For graph: returns entities and relationships.
    - For tagging (default): returns a JSON array of tag classifications.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def call(self, slot: str, messages: list[dict], **kwargs: Any) -> dict:
        self.calls.append({"slot": slot, "messages": messages})

        system_msg = messages[0]["content"] if messages else ""

        if "wiki article" in system_msg.lower():
            return {
                "content": (
                    "## Overview\nTest wiki overview for a domain.\n\n"
                    "## Key Concepts\n- Concept A: description.\n- Concept B: description.\n\n"
                    "## Current State\nWork is ongoing.\n\n"
                    "## Cross-Domain Connections\nConnects to other domains.\n\n"
                    "## Evolution\nThinking has evolved.\n\n"
                    "## Key Turns\nturn_test1 | important turn\n\n"
                    "## Open Questions\n- Question 1\n- Question 2\n"
                ),
            }
        elif "extracting entities" in system_msg.lower():
            return {
                "content": json.dumps(
                    [
                        {"entity_type": "CONCEPT", "canonical_name": "TestConcept", "confidence": 0.9},
                        {
                            "relationship_type": "CONNECTS",
                            "source": "TestConcept",
                            "target": "OtherConcept",
                            "confidence": 0.8,
                        },
                    ]
                ),
            }
        else:
            return {
                "content": json.dumps(
                    [
                        {
                            "turn_id": "test_turn_1",
                            "primary_domain": "nbcm",
                            "domains": ["nbcm"],
                            "tags": ["test_tag"],
                            "importance": 0.8,
                            "bridges": [],
                            "beast_confidence": 0.9,
                        }
                    ]
                ),
            }


class StubEventStore:
    """In-memory event store."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def write_event(self, **kwargs: Any) -> None:
        self.events.append(kwargs)

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Test domain registry (avoids dependency on docs/ file)
# ---------------------------------------------------------------------------


def _make_test_registry() -> DomainRegistry:
    """Create a minimal DomainRegistry for testing wiki + graph flows."""
    domains = {
        "nbcm": DomainEntry(
            domain_id="nbcm",
            description="Null Boundary Condition Model — theoretical physics framework",
            core_keywords=["nbcm", "null boundary", "timelessness"],
            exclude_note="",
            importance_floor=0.3,
        ),
        "chemistry_research": DomainEntry(
            domain_id="chemistry_research",
            description="Chemistry research including EZ Water and exclusion zones",
            core_keywords=["chemistry", "ez water", "exclusion zone"],
            exclude_note="",
            importance_floor=0.3,
        ),
    }
    connectors = [
        ConnectorEntry(
            domain_a="nbcm",
            domain_b="chemistry_research",
            bridge_tag="nbcm->chemistry_research",
            description="NBCM connects to chemistry through exclusion zone frameworks",
        ),
    ]
    return DomainRegistry(
        domains=domains,
        connectors=connectors,
        version="v1.0",
        loaded_at="2025-01-01T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db():
    """Provide a temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "test_state.db")


@pytest.fixture
async def corpus_turn_store(tmp_db):
    """Provide an initialized CorpusTurnStore."""
    store = CorpusTurnStore(tmp_db)
    await store.initialize()
    yield store
    await store.close()


@pytest.fixture
def test_registry():
    """Provide a test DomainRegistry."""
    return _make_test_registry()


# ---------------------------------------------------------------------------
# Wiki generation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sexton_wiki_generation_writes_artifact(corpus_turn_store, test_registry):
    """Wiki generation should write a GENERATED artifact to the artifact store."""
    # Write a tagged turn in the nbcm domain so wiki generation has data
    from aip.foundation.schemas.corpus_turn import CorpusTurn

    turn = CorpusTurn(
        turn_id="wiki_test_turn_1",
        conversation_id="conv_1",
        conversation_name="Test Conversation",
        turn_index=1,
        source_model="claude-3",
        source_account="test",
        export_date="2025-01-01",
        user_text="What is NBCM?",
        assistant_text="NBCM is the Null Boundary Condition Model.",
        turn_timestamp="2025-01-01T00:00:00Z",
        domains=["nbcm"],
        primary_domain="nbcm",
        tags=["test_tag"],
        importance=0.8,
        bridges=[],
        beast_confidence=0.9,
        tagging_version=1,
        embedded=0,
        searchable_text="What is NBCM? NBCM is the Null Boundary Condition Model.",
        word_count=20,
    )
    await corpus_turn_store.write_turn(turn)

    artifact_store = StubArtifactStore()
    ecs_store = StubEcsStore()
    model_provider = StubModelProvider()
    event_store = StubEventStore()

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=corpus_turn_store,
        artifact_store=artifact_store,
        ecs_store=ecs_store,
        event_store=event_store,
        config=SextonConfig(),
    )

    # Patch load_registry to return our test registry
    with patch("aip.orchestration.actors.domain_registry.load_registry", return_value=test_registry):
        await sexton._run_wiki_generation(force_domains=["nbcm"], max_per_cycle=1)

    # Should have written at least one wiki artifact
    all_artifacts = artifact_store.get_all()
    wiki_artifacts = {
        aid: a for aid, a in all_artifacts.items() if a.get("metadata", {}).get("artifact_type") == "sexton_wiki"
    }

    assert len(wiki_artifacts) >= 1, f"Expected at least 1 wiki artifact, got {len(wiki_artifacts)}"

    # Verify artifact has the right domain
    for aid, art in wiki_artifacts.items():
        assert art["metadata"]["domain"] == "nbcm"
        assert "## Overview" in art["content"]
        assert "## Key Concepts" in art["content"]


@pytest.mark.asyncio
async def test_sexton_wiki_generation_ecs_transition(corpus_turn_store, test_registry):
    """Wiki generation should create a GENERATED ECS transition."""
    from aip.foundation.schemas.corpus_turn import CorpusTurn

    turn = CorpusTurn(
        turn_id="wiki_ecs_test_turn_1",
        conversation_id="conv_1",
        conversation_name="Test Conversation",
        turn_index=1,
        source_model="claude-3",
        source_account="test",
        export_date="2025-01-01",
        user_text="What is NBCM?",
        assistant_text="NBCM is the Null Boundary Condition Model.",
        turn_timestamp="2025-01-01T00:00:00Z",
        domains=["nbcm"],
        primary_domain="nbcm",
        tags=["test_tag"],
        importance=0.8,
        bridges=[],
        beast_confidence=0.9,
        tagging_version=1,
        embedded=0,
        searchable_text="What is NBCM?",
        word_count=20,
    )
    await corpus_turn_store.write_turn(turn)

    artifact_store = StubArtifactStore()
    ecs_store = StubEcsStore()
    model_provider = StubModelProvider()
    event_store = StubEventStore()

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=corpus_turn_store,
        artifact_store=artifact_store,
        ecs_store=ecs_store,
        event_store=event_store,
        config=SextonConfig(),
    )

    with patch("aip.orchestration.actors.domain_registry.load_registry", return_value=test_registry):
        await sexton._run_wiki_generation(force_domains=["nbcm"], max_per_cycle=1)

    # Check that ECS state was set to GENERATED for the wiki artifact
    wiki_arts = {
        aid: a
        for aid, a in artifact_store.get_all().items()
        if a.get("metadata", {}).get("artifact_type") == "sexton_wiki"
    }
    for aid in wiki_arts:
        state = await ecs_store.current_state(aid)
        assert state == "GENERATED", f"ECS state for {aid} should be GENERATED, got {state}"


@pytest.mark.asyncio
async def test_sexton_wiki_skipped_when_no_provider():
    """Wiki generation should skip when no model provider is available."""
    artifact_store = StubArtifactStore()

    sexton = Sexton(
        sexton_provider=None,
        artifact_store=artifact_store,
        config=SextonConfig(),
    )

    result = await sexton._run_wiki_generation(force_domains=["nbcm"])
    assert result.get("skipped") is not None


@pytest.mark.asyncio
async def test_sexton_wiki_overview_extraction(corpus_turn_store, test_registry):
    """Wiki article metadata should contain extracted overview_text."""
    from aip.foundation.schemas.corpus_turn import CorpusTurn

    turn = CorpusTurn(
        turn_id="wiki_overview_test_turn_1",
        conversation_id="conv_1",
        conversation_name="Test Conversation",
        turn_index=1,
        source_model="claude-3",
        source_account="test",
        export_date="2025-01-01",
        user_text="What is NBCM?",
        assistant_text="NBCM is the Null Boundary Condition Model.",
        turn_timestamp="2025-01-01T00:00:00Z",
        domains=["nbcm"],
        primary_domain="nbcm",
        tags=["test_tag"],
        importance=0.8,
        bridges=[],
        beast_confidence=0.9,
        tagging_version=1,
        embedded=0,
        searchable_text="What is NBCM?",
        word_count=20,
    )
    await corpus_turn_store.write_turn(turn)

    artifact_store = StubArtifactStore()
    ecs_store = StubEcsStore()
    model_provider = StubModelProvider()
    event_store = StubEventStore()

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=corpus_turn_store,
        artifact_store=artifact_store,
        ecs_store=ecs_store,
        event_store=event_store,
        config=SextonConfig(),
    )

    with patch("aip.orchestration.actors.domain_registry.load_registry", return_value=test_registry):
        await sexton._run_wiki_generation(force_domains=["nbcm"], max_per_cycle=1)

    wiki_arts = {
        aid: a
        for aid, a in artifact_store.get_all().items()
        if a.get("metadata", {}).get("artifact_type") == "sexton_wiki"
    }
    assert len(wiki_arts) >= 1
    for aid, art in wiki_arts.items():
        meta = art["metadata"]
        assert "overview_text" in meta
        # The stub model returns "Test wiki overview for a domain."
        assert "Test wiki overview" in meta["overview_text"]


# ---------------------------------------------------------------------------
# Graph extraction tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sexton_graph_extraction_skipped_when_no_provider():
    """Graph extraction should skip when no model provider is available."""
    sexton = Sexton(
        sexton_provider=None,
        config=SextonConfig(),
    )

    result = await sexton._run_graph_extraction()
    assert result.get("skipped") is not None


@pytest.mark.asyncio
async def test_sexton_graph_extraction_result_structure(tmp_db):
    """Graph extraction result should have expected structure."""
    corpus_turn_store = CorpusTurnStore(tmp_db)
    await corpus_turn_store.initialize()

    # Write a high-importance turn with bridge tags for graph extraction
    from aip.foundation.schemas.corpus_turn import CorpusTurn

    turn = CorpusTurn(
        turn_id="graph_test_turn_1",
        conversation_id="conv_1",
        conversation_name="Test Conversation",
        turn_index=1,
        source_model="claude-3",
        source_account="test",
        export_date="2025-01-01",
        user_text="Tell me about NBCM and EZ Water connections",
        assistant_text="NBCM connects to EZ Water through the exclusion zone framework.",
        turn_timestamp="2025-01-01T00:00:00Z",
        thinking_text="This is a deep analysis of the NBCM-EZ Water connection.",
        domains=["nbcm"],
        primary_domain="nbcm",
        tags=["nbcm", "ez_water", "connection"],
        importance=0.9,
        bridges=["nbcm->chemistry_research"],
        beast_confidence=0.9,
        tagging_version=1,
        embedded=1,
        searchable_text="NBCM connects to EZ Water through the exclusion zone framework.",
        word_count=50,
    )
    await corpus_turn_store.write_turn(turn)
    await corpus_turn_store.close()

    model_provider = StubModelProvider()
    event_store = StubEventStore()

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=CorpusTurnStore(tmp_db),
        event_store=event_store,
        config=SextonConfig(),
    )

    result = await sexton._run_graph_extraction(limit=5)

    # Verify result structure
    assert "turns_processed" in result
    assert "entities_created" in result
    assert "relationships_created" in result
    assert isinstance(result["turns_processed"], int)

    new_store = CorpusTurnStore(tmp_db)
    await new_store.close()


# ---------------------------------------------------------------------------
# Bridge-tagged turn detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sexton_has_bridge_tagged_turns(tmp_db):
    """Bridge-tagged turn detection should work via CorpusTurnStore."""
    corpus_turn_store = CorpusTurnStore(tmp_db)
    await corpus_turn_store.initialize()

    # Initially no bridge-tagged turns
    result = await corpus_turn_store.has_bridge_tagged_turns()
    assert result is False

    # Write a turn with bridge tags
    from aip.foundation.schemas.corpus_turn import CorpusTurn

    turn = CorpusTurn(
        turn_id="bridge_test_turn_1",
        conversation_id="conv_1",
        conversation_name="Test Conversation",
        turn_index=1,
        source_model="claude-3",
        source_account="test",
        export_date="2025-01-01",
        user_text="How does NBCM connect to theology?",
        assistant_text="NBCM connects to theology through the concept of timelessness.",
        turn_timestamp="2025-01-01T00:00:00Z",
        domains=["nbcm", "theology_research"],
        primary_domain="nbcm",
        tags=["bridge", "connection"],
        importance=0.8,
        bridges=["nbcm->chemistry_research"],
        beast_confidence=0.9,
        tagging_version=1,
        embedded=0,
        searchable_text="How does NBCM connect to theology?",
        word_count=30,
    )
    await corpus_turn_store.write_turn(turn)

    result = await corpus_turn_store.has_bridge_tagged_turns()
    assert result is True

    await corpus_turn_store.close()


@pytest.mark.asyncio
async def test_sexton_has_bridge_tagged_turns_actor(tmp_db):
    """Sexton._has_bridge_tagged_turns() should delegate to CorpusTurnStore."""
    corpus_turn_store = CorpusTurnStore(tmp_db)
    await corpus_turn_store.initialize()

    sexton = Sexton(
        corpus_turn_store=corpus_turn_store,
        config=SextonConfig(),
    )

    # No bridge turns initially
    result = await sexton._has_bridge_tagged_turns()
    assert result is False

    await corpus_turn_store.close()


# ---------------------------------------------------------------------------
# Full cycle smoke test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sexton_run_cycle_smoke(tmp_db, test_registry):
    """Full run_cycle() should complete without error using stubs.

    This is a smoke test — it verifies the full cycle executes
    without crashing, but doesn't assert specific data outcomes
    since the stubs are minimal.
    """
    corpus_turn_store = CorpusTurnStore(tmp_db)
    await corpus_turn_store.initialize()

    # Write a turn for the cycle to find
    from aip.foundation.schemas.corpus_turn import CorpusTurn

    turn = CorpusTurn(
        turn_id="cycle_test_turn_1",
        conversation_id="conv_1",
        conversation_name="Test Conversation",
        turn_index=1,
        source_model="claude-3",
        source_account="test",
        export_date="2025-01-01",
        user_text="What is NBCM?",
        assistant_text="NBCM is the Null Boundary Condition Model.",
        turn_timestamp="2025-01-01T00:00:00Z",
        domains=[],
        primary_domain="",
        tags=[],
        importance=0.0,
        bridges=[],
        beast_confidence=0.0,
        tagging_version=0,
        embedded=0,
        searchable_text="What is NBCM? NBCM is the Null Boundary Condition Model.",
        word_count=20,
    )
    await corpus_turn_store.write_turn(turn)

    artifact_store = StubArtifactStore()
    ecs_store = StubEcsStore()
    model_provider = StubModelProvider()
    embedding_provider = StubEmbeddingProvider(dim=8)
    vector_store = StubVectorStore()
    event_store = StubEventStore()

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=corpus_turn_store,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        artifact_store=artifact_store,
        ecs_store=ecs_store,
        event_store=event_store,
        config=SextonConfig(),
    )

    with patch("aip.orchestration.actors.domain_registry.load_registry", return_value=test_registry):
        summary = await sexton.run_cycle()

    # Verify summary structure
    assert "tagging" in summary
    assert "embedding" in summary
    assert "wiki" in summary
    assert "graph" in summary
    assert "classification" in summary
    assert "cycle_elapsed_seconds" in summary

    # Embedding should have attempted
    assert summary["embedding"]["embedded"] >= 1 or summary["embedding"].get("skipped") is not None

    # Cycle should have completed in reasonable time
    assert summary["cycle_elapsed_seconds"] < 30

    await corpus_turn_store.close()


# ---------------------------------------------------------------------------
# Graph extraction log (entity_turn_index) E2E tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sexton_graph_extraction_writes_extraction_log(tmp_db):
    """Graph extraction should write entries to graph_extraction_log table.

    This verifies the entity_turn_index equivalent: after graph extraction,
    the turn should be logged in graph_extraction_log so it is not re-extracted
    on subsequent cycles.
    """
    from aip.adapter.graph_store import GraphStore
    from aip.foundation.schemas.corpus_turn import CorpusTurn

    corpus_turn_store = CorpusTurnStore(tmp_db)
    await corpus_turn_store.initialize()

    turn = CorpusTurn(
        turn_id="extraction_log_test_turn_1",
        conversation_id="conv_1",
        conversation_name="Test Conversation",
        turn_index=1,
        source_model="claude-3",
        source_account="test",
        export_date="2025-01-01",
        user_text="Tell me about NBCM and EZ Water connections",
        assistant_text="NBCM connects to EZ Water through the exclusion zone framework.",
        turn_timestamp="2025-01-01T00:00:00Z",
        domains=["nbcm"],
        primary_domain="nbcm",
        tags=["nbcm", "ez_water", "connection"],
        importance=0.9,
        bridges=["nbcm->chemistry_research"],
        beast_confidence=0.9,
        tagging_version=1,
        embedded=1,
        searchable_text="NBCM connects to EZ Water through the exclusion zone framework.",
        word_count=50,
    )
    await corpus_turn_store.write_turn(turn)
    await corpus_turn_store.close()

    model_provider = StubModelProvider()
    event_store = StubEventStore()

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=CorpusTurnStore(tmp_db),
        event_store=event_store,
        config=SextonConfig(),
    )

    result = await sexton._run_graph_extraction(limit=5)

    # Verify the turn was processed
    assert result["turns_processed"] >= 1, f"Expected at least 1 turn processed, got {result}"

    # Verify graph_extraction_log was written
    graph_store = GraphStore(tmp_db)
    await graph_store.initialize()
    assert await graph_store.is_turn_extracted("extraction_log_test_turn_1"), (
        "Turn should be logged in graph_extraction_log after extraction"
    )

    # Verify nodes were created
    node_count = await graph_store.node_count()
    assert node_count >= 1, f"Expected at least 1 graph node, got {node_count}"
    await graph_store.close()


@pytest.mark.asyncio
async def test_sexton_graph_extraction_log_prevents_reduplication(tmp_db):
    """Turns logged in graph_extraction_log should not be re-extracted.

    After a successful graph extraction, calling _run_graph_extraction again
    should skip the already-extracted turn.
    """
    from aip.adapter.graph_store import GraphStore
    from aip.foundation.schemas.corpus_turn import CorpusTurn

    corpus_turn_store = CorpusTurnStore(tmp_db)
    await corpus_turn_store.initialize()

    turn = CorpusTurn(
        turn_id="dedup_test_turn_1",
        conversation_id="conv_1",
        conversation_name="Test Conversation",
        turn_index=1,
        source_model="claude-3",
        source_account="test",
        export_date="2025-01-01",
        user_text="Tell me about NBCM and EZ Water connections",
        assistant_text="NBCM connects to EZ Water through the exclusion zone framework.",
        turn_timestamp="2025-01-01T00:00:00Z",
        domains=["nbcm"],
        primary_domain="nbcm",
        tags=["nbcm", "ez_water", "connection"],
        importance=0.9,
        bridges=["nbcm->chemistry_research"],
        beast_confidence=0.9,
        tagging_version=1,
        embedded=1,
        searchable_text="NBCM connects to EZ Water through the exclusion zone framework.",
        word_count=50,
    )
    await corpus_turn_store.write_turn(turn)
    await corpus_turn_store.close()

    model_provider = StubModelProvider()
    event_store = StubEventStore()

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=CorpusTurnStore(tmp_db),
        event_store=event_store,
        config=SextonConfig(),
    )

    # First extraction
    result1 = await sexton._run_graph_extraction(limit=5)
    assert result1["turns_processed"] >= 1

    # Second extraction — should skip the already-extracted turn
    result2 = await sexton._run_graph_extraction(limit=5)
    assert result2["turns_processed"] == 0, (
        f"Second extraction should process 0 turns (already extracted), got {result2['turns_processed']}"
    )

    # Verify graph_extraction_log has the entry
    graph_store = GraphStore(tmp_db)
    await graph_store.initialize()
    assert await graph_store.is_turn_extracted("dedup_test_turn_1")
    await graph_store.close()


@pytest.mark.asyncio
async def test_sexton_graph_extraction_log_records_counts(tmp_db):
    """graph_extraction_log should record entities_found and relationships_found counts."""
    import sqlite3 as _sqlite3

    from aip.foundation.schemas.corpus_turn import CorpusTurn

    corpus_turn_store = CorpusTurnStore(tmp_db)
    await corpus_turn_store.initialize()

    turn = CorpusTurn(
        turn_id="counts_test_turn_1",
        conversation_id="conv_1",
        conversation_name="Test Conversation",
        turn_index=1,
        source_model="claude-3",
        source_account="test",
        export_date="2025-01-01",
        user_text="Tell me about NBCM and EZ Water connections",
        assistant_text="NBCM connects to EZ Water through the exclusion zone framework.",
        turn_timestamp="2025-01-01T00:00:00Z",
        domains=["nbcm"],
        primary_domain="nbcm",
        tags=["nbcm", "ez_water", "connection"],
        importance=0.9,
        bridges=["nbcm->chemistry_research"],
        beast_confidence=0.9,
        tagging_version=1,
        embedded=1,
        searchable_text="NBCM connects to EZ Water through the exclusion zone framework.",
        word_count=50,
    )
    await corpus_turn_store.write_turn(turn)
    await corpus_turn_store.close()

    model_provider = StubModelProvider()
    event_store = StubEventStore()

    sexton = Sexton(
        sexton_provider=model_provider,
        corpus_turn_store=CorpusTurnStore(tmp_db),
        event_store=event_store,
        config=SextonConfig(),
    )

    result = await sexton._run_graph_extraction(limit=5)
    assert result["turns_processed"] >= 1

    # Query the extraction log directly to verify counts
    conn = _sqlite3.connect(tmp_db)
    conn.row_factory = _sqlite3.Row
    try:
        row = conn.execute(
            "SELECT entities_found, relationships_found FROM graph_extraction_log WHERE turn_id = ?",
            ("counts_test_turn_1",),
        ).fetchone()
        assert row is not None, "graph_extraction_log entry should exist"
        assert row["entities_found"] >= 0
        assert row["relationships_found"] >= 0
    finally:
        conn.close()
