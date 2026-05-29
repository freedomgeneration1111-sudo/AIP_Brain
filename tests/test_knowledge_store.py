"""Tests for SqliteKnowledgeStore (CHUNK-10.0b).

Verifies CRUD, provenance, state transitions, search, and — critically —
that APPROVED compiled knowledge receives real (non-zero) embeddings when
an EmbeddingProvider is configured.
"""

import asyncio
import hashlib
import os
import tempfile

import pytest

from aip.adapter.knowledge.sqlite_knowledge_store import SqliteKnowledgeStore
from aip.foundation.protocols import KnowledgeStore, LexicalStore, VectorStore

# ---------------------------------------------------------------------------
# Fakes for VectorStore / LexicalStore (minimal, for basic tests)
# ---------------------------------------------------------------------------


class FakeVectorStore(VectorStore):
    """Records upsert calls so tests can inspect what was stored."""

    def __init__(self):
        self.upserted: list[dict] = []

    async def upsert(self, id, embedding, content, metadata, domain=None):
        self.upserted.append(
            {
                "id": id,
                "embedding": embedding,
                "content": content,
                "metadata": metadata,
                "domain": domain,
            },
        )

    async def retrieve(self, query_vector, domain=None, top_k=10):
        # Return any upserted items that match the domain for basic retrieval
        hits = []
        for item in self.upserted:
            if domain is None or item.get("domain") == domain:
                hits.append(_FakeHit(item["id"], item["content"], 0.9))
        return hits[:top_k]

    async def delete(self, id):
        self.upserted = [u for u in self.upserted if u["id"] != id]


class _FakeHit:
    """Minimal search result object with id/content/score attributes."""

    def __init__(self, id, content, score):
        self.id = id
        self.content = content
        self.score = score


class FakeLexicalStore(LexicalStore):
    """Records index_document calls so tests can inspect what was indexed."""

    def __init__(self):
        self.indexed: list[dict] = []

    async def search(self, query, domain=None, limit=10):
        hits = []
        for item in self.indexed:
            if domain is None or item.get("domain") == domain:
                if query.lower() in item.get("content", "").lower():
                    hits.append(_FakeHit(item["id"], item["content"], 0.7))
        return hits[:limit]

    async def index_document(self, id, content, metadata, domain=None):
        self.indexed.append(
            {
                "id": id,
                "content": content,
                "metadata": metadata,
                "domain": domain,
            },
        )


# ---------------------------------------------------------------------------
# Fake EmbeddingProvider — deterministic, non-zero vectors
# ---------------------------------------------------------------------------


class FakeEmbeddingProvider:
    """Deterministic embedding provider for testing.

    Returns 768-dim vectors derived from SHA-256 of the input text.
    Never returns zero vectors — each text gets a unique, non-trivial vector.
    """

    def __init__(self, dimensions: int = 768):
        self.dimensions = dimensions
        self.embed_calls: list[str] = []  # track what was embedded

    async def embed(self, text: str) -> list[float]:
        self.embed_calls.append(text)
        h = hashlib.sha256(text.encode("utf-8")).digest()
        vec = []
        i = 0
        while len(vec) < self.dimensions:
            val = (h[i % len(h)] / 255.0) - 0.5
            vec.append(val)
            i += 1
        return vec[: self.dimensions]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def knowledge_store():
    """KnowledgeStore without EmbeddingProvider (backward compat)."""
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "knowledge.db")
        store = SqliteKnowledgeStore(db, FakeVectorStore(), FakeLexicalStore())
        yield store
        asyncio.run(store.close())


@pytest.fixture
def knowledge_store_with_embeddings():
    """KnowledgeStore with a real (fake) EmbeddingProvider."""
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "knowledge.db")
        embed_provider = FakeEmbeddingProvider(dimensions=768)
        vector_store = FakeVectorStore()
        lexical_store = FakeLexicalStore()
        store = SqliteKnowledgeStore(db, vector_store, lexical_store, embedding_provider=embed_provider)
        yield store, vector_store, lexical_store, embed_provider
        asyncio.run(store.close())


# ---------------------------------------------------------------------------
# Original tests (preserved)
# ---------------------------------------------------------------------------


def test_knowledge_store_implements_protocol(knowledge_store):
    assert isinstance(knowledge_store, KnowledgeStore)


async def test_store_and_get_compiled(knowledge_store):
    await knowledge_store.store_compiled("k1", "Compiled summary of foo.", ["c1", "c2"], "test", {"state": "COMPILED"})
    got = await knowledge_store.get_compiled("k1")
    assert got is not None
    assert got["content"].startswith("Compiled")
    assert got["source_canonical_ids"] == ["c1", "c2"]
    assert got["state"] == "COMPILED"


async def test_provenance_and_state_transition(knowledge_store):
    await knowledge_store.store_compiled("k2", "content", ["c99"], "d", {"state": "REVIEWED"})
    prov = await knowledge_store.get_provenance("k2")
    assert len(prov) >= 1

    await knowledge_store.update_state("k2", "APPROVED")
    got = await knowledge_store.get_compiled("k2")
    assert got["state"] == "APPROVED"


async def test_list_and_search(knowledge_store):
    await knowledge_store.store_compiled(
        "k3",
        "searchable compiled text about AIP",
        ["c1"],
        "test",
        {"state": "APPROVED"},
    )
    listed = await knowledge_store.list_compiled(state="APPROVED")
    assert any(r["knowledge_id"] == "k3" for r in listed)

    results = await knowledge_store.search_compiled("AIP", limit=5)
    # May be empty if lexical fake returns nothing, but call succeeds
    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# NEW: Embedding tests
# ---------------------------------------------------------------------------


async def test_store_compiled_approved_generates_real_embedding(knowledge_store_with_embeddings):
    """APPROVED compiled knowledge must receive a real (non-zero) embedding."""
    store, vector_store, lexical_store, embed_provider = knowledge_store_with_embeddings

    await store.store_compiled(
        "k-embed1",
        "This is a detailed compiled knowledge document about machine learning.",
        ["c1", "c2"],
        "test",
        {"state": "APPROVED"},
    )

    # Embedding provider was called
    assert len(embed_provider.embed_calls) > 0, "EmbeddingProvider.embed() was never called"

    # Vector store received a real upsert
    assert len(vector_store.upserted) == 1, f"Expected 1 upsert, got {len(vector_store.upserted)}"

    upsert = vector_store.upserted[0]
    assert upsert["id"] == "compiled:k-embed1"

    # The embedding is NOT a zero vector
    embedding = upsert["embedding"]
    assert len(embedding) == 768, f"Expected 768-dim embedding, got {len(embedding)}"
    assert any(v != 0.0 for v in embedding), "Embedding is all zeros — should be a real vector"

    # Lexical store also received the document
    assert len(lexical_store.indexed) == 1


async def test_store_compiled_non_approved_no_embedding(knowledge_store_with_embeddings):
    """Non-APPROVED compiled knowledge should NOT be embedded into vector store."""
    store, vector_store, lexical_store, embed_provider = knowledge_store_with_embeddings

    await store.store_compiled(
        "k-embed2",
        "Draft content that is not yet approved.",
        ["c1"],
        "test",
        {"state": "COMPILED"},
    )

    # No embedding generated (only APPROVED triggers dual-indexing)
    assert len(embed_provider.embed_calls) == 0, "embed() should not be called for COMPILED state"
    assert len(vector_store.upserted) == 0, "Vector store should not receive COMPILED items"
    assert len(lexical_store.indexed) == 0, "Lexical store should not receive COMPILED items"


async def test_update_state_to_approved_triggers_embedding(knowledge_store_with_embeddings):
    """Transitioning to APPROVED via update_state() must trigger embedding + dual-index."""
    store, vector_store, lexical_store, embed_provider = knowledge_store_with_embeddings

    # Store as COMPILED first (the typical flow)
    await store.store_compiled(
        "k-state-promote",
        "Content that will be promoted to APPROVED via state transition.",
        ["c1"],
        "test",
        {"state": "COMPILED"},
    )

    # No embedding yet
    assert len(embed_provider.embed_calls) == 0
    assert len(vector_store.upserted) == 0

    # Promote to REVIEWED
    await store.update_state("k-state-promote", "REVIEWED")
    # Still no embedding (REVIEWED doesn't trigger dual-index)
    assert len(embed_provider.embed_calls) == 0

    # Promote to APPROVED — this triggers embedding
    await store.update_state("k-state-promote", "APPROVED")
    assert len(embed_provider.embed_calls) > 0, "embed() should be called on APPROVED transition"

    # Vector store received real embedding
    assert len(vector_store.upserted) == 1
    embedding = vector_store.upserted[0]["embedding"]
    assert len(embedding) == 768
    assert any(v != 0.0 for v in embedding), "Embedding should not be zero vector"

    # Lexical store also received the document
    assert len(lexical_store.indexed) == 1


async def test_search_compiled_uses_vector_search(knowledge_store_with_embeddings):
    """search_compiled() must use vector search when EmbeddingProvider is available."""
    store, vector_store, lexical_store, embed_provider = knowledge_store_with_embeddings

    # Store and approve a document
    await store.store_compiled(
        "k-search1",
        "Machine learning is a subfield of artificial intelligence.",
        ["c1"],
        "test",
        {"state": "APPROVED"},
    )

    # Search with a query
    results = await store.search_compiled("artificial intelligence", domain="test", limit=5)

    # The embed provider was called for the query (in addition to the store)
    assert len(embed_provider.embed_calls) >= 1, "embed() should be called for search query"

    # Should get results from vector store
    assert len(results) > 0, "Search should return results from vector store"
    assert results[0]["knowledge_id"] == "k-search1"


async def test_no_embedding_provider_graceful_degradation():
    """Without EmbeddingProvider, store_compiled(APPROVED) still works (lexical only)."""
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "knowledge.db")
        vector_store = FakeVectorStore()
        lexical_store = FakeLexicalStore()
        store = SqliteKnowledgeStore(db, vector_store, lexical_store, embedding_provider=None)

        # Store APPROVED without embedding provider — should not crash
        await store.store_compiled(
            "k-no-embed",
            "Content without embedding provider.",
            ["c1"],
            "test",
            {"state": "APPROVED"},
        )

        # Vector store NOT called (no embedding available)
        assert len(vector_store.upserted) == 0

        # Lexical store still receives the document
        assert len(lexical_store.indexed) == 1

        # Search works with lexical only
        results = await store.search_compiled("Content", domain="test", limit=5)
        # No vector results, but lexical may return something
        assert isinstance(results, list)

        await store.close()


async def test_embedding_failure_graceful_handling():
    """When embedding generation fails, store_compiled(APPROVED) degrades gracefully."""
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "knowledge.db")
        vector_store = FakeVectorStore()
        lexical_store = FakeLexicalStore()

        class FailingEmbeddingProvider:
            async def embed(self, text: str) -> list[float]:
                raise ConnectionError("Ollama is not running")

        store = SqliteKnowledgeStore(
            db,
            vector_store,
            lexical_store,
            embedding_provider=FailingEmbeddingProvider(),
        )

        # Should NOT raise — gracefully degrades to lexical-only
        await store.store_compiled(
            "k-fail-embed",
            "Content with failing embedding.",
            ["c1"],
            "test",
            {"state": "APPROVED"},
        )

        # Vector store NOT called (embedding failed)
        assert len(vector_store.upserted) == 0

        # Lexical store still receives the document
        assert len(lexical_store.indexed) == 1

        await store.close()


async def test_update_state_approved_no_double_embedding(knowledge_store_with_embeddings):
    """Re-calling update_state('APPROVED') for already-APPROVED item should not re-embed."""
    store, vector_store, lexical_store, embed_provider = knowledge_store_with_embeddings

    # Store directly as APPROVED
    await store.store_compiled(
        "k-already-approved",
        "Already approved content.",
        ["c1"],
        "test",
        {"state": "APPROVED"},
    )

    # One embedding from the initial store
    embed_count_after_store = len(embed_provider.embed_calls)

    # Call update_state("APPROVED") again — should NOT re-embed
    await store.update_state("k-already-approved", "APPROVED")

    # No additional embedding call (state was already APPROVED)
    assert len(embed_provider.embed_calls) == embed_count_after_store, (
        "update_state(APPROVED) on already-APPROVED item should not trigger re-embedding"
    )
