"""CHUNK-9.2 gate: Canonical Promotion Pipeline (evaluate, promote with gate + indexing + Vigil health, reject, idempotency)."""

from __future__ import annotations

import pytest

from aip.foundation.schemas import CanonicalPromotionConfig, AutonomyEscalation
from aip.orchestration.canonical_pipeline import CanonicalPipeline


# --- Minimal fakes for injected Protocols ---

class FakeAutonomyGate:
    async def check(self, action_type, resource_id, requested_level, requested_by):
        return AutonomyEscalation(
            escalation_id="esc-1", action_type=action_type, requested_by=requested_by,
            resource_id=resource_id, requested_level=requested_level, granted=True, reason="ok",
        )

    async def escalate(self, action_type, resource_id, requested_level, requested_by):
        if requested_by == "definer":
            return AutonomyEscalation(
                escalation_id="esc-2", action_type=action_type, requested_by=requested_by,
                resource_id=resource_id, requested_level=requested_level, granted=True, reason="definer approved",
            )
        return AutonomyEscalation(
            escalation_id="esc-3", action_type=action_type, requested_by=requested_by,
            resource_id=resource_id, requested_level=requested_level, granted=False, reason="DEFINER required",
        )


class FakeCanonicalStore:
    def __init__(self):
        self.data = {}

    async def write_canonical(self, artifact_id, content, approved_by="definer"):
        self.data[artifact_id] = {"content": content, "approved_by": approved_by}

    async def read_canonical(self, artifact_id):
        return self.data.get(artifact_id)

    async def list_canonical(self, domain=None):
        return list(self.data.values())


class FakeArtifactStore:
    def __init__(self):
        self.data = {"art-1": "sample artifact content"}

    async def read(self, artifact_id):
        return self.data.get(artifact_id)

    async def write(self, artifact_id, content, metadata=None):
        self.data[artifact_id] = content


class FakeEcsStore:
    def __init__(self):
        self.states = {"art-1": "REVIEWED"}
        self.transitions = []

    async def current_state(self, artifact_id):
        return self.states.get(artifact_id, "UNKNOWN")

    async def transition(self, artifact_id, from_state, to_state, actor=None, reason=None):
        self.states[artifact_id] = to_state
        self.transitions.append({"artifact_id": artifact_id, "from": from_state, "to": to_state})


class FakeEventStore:
    def __init__(self):
        self.events = []

    async def write_event(self, **kwargs):
        self.events.append(kwargs)

    async def query(self, event_type=None, limit=100):
        return []


class FakeVectorStore:
    async def upsert(self, id, embedding, content, metadata=None, domain=None):
        pass

    async def retrieve(self, query_vector, domain=None, top_k=10):
        return []


class FakeLexicalStore:
    async def index_document(self, doc_id, content, domain=None, metadata=None):
        pass

    async def search(self, query, domain=None, limit=10):
        return []


class FakeModelProvider:
    async def call(self, slot, messages, **kw):
        return {"content": "mock response"}


class FakeEmbeddingProvider:
    async def embed(self, text):
        return [0.1] * 8


class FakeVigilStore:
    def __init__(self):
        self.checks = []

    async def record_vigil_check(self, canonical_count=0, stale_count=0, status="healthy"):
        self.checks.append({"canonical_count": canonical_count, "stale_count": stale_count, "status": status})

    async def list_stale_canonicals(self, threshold_days=30):
        return []


@pytest.fixture
def pipeline():
    config = CanonicalPromotionConfig()
    return CanonicalPipeline(
        config=config,
        autonomy_gate=FakeAutonomyGate(),
        canonical_store=FakeCanonicalStore(),
        artifact_store=FakeArtifactStore(),
        ecs_store=FakeEcsStore(),
        event_store=FakeEventStore(),
        vector_store=FakeVectorStore(),
        lexical_store=FakeLexicalStore(),
        model_provider=FakeModelProvider(),
        embedding_provider=FakeEmbeddingProvider(),
        vigil_store=FakeVigilStore(),
    )


@pytest.mark.asyncio
async def test_promote_to_canonical_success(pipeline):
    """Full promotion pipeline succeeds when artifact is REVIEWED and approved_by=definer."""
    result = await pipeline.promote_to_canonical("art-1", "definer")
    assert result["artifact_id"] == "art-1"
    assert result["state"] == "APPROVED"
    assert result["canonical_written"] is True


@pytest.mark.asyncio
async def test_promote_to_canonical_rejects_non_definer(pipeline):
    """Promotion must be rejected if approved_by is not 'definer'."""
    with pytest.raises(PermissionError):
        await pipeline.promote_to_canonical("art-1", "mcp")


@pytest.mark.asyncio
async def test_promote_to_canonical_rejects_non_reviewed(pipeline):
    """Promotion must be rejected if artifact is not in REVIEWED state."""
    pipeline.ecs_store.states["art-2"] = "GENERATED"
    with pytest.raises(ValueError, match="not in REVIEWED state"):
        await pipeline.promote_to_canonical("art-2", "definer")


@pytest.mark.asyncio
async def test_reject_promotion_records_event(pipeline):
    """Rejecting promotion records a rejection event without changing ECS state."""
    result = await pipeline.reject_promotion("art-1", "low quality")
    assert result["action"] == "rejected"
    assert result["reason"] == "low quality"
    # Artifact should still be in REVIEWED state
    state = await pipeline.ecs_store.current_state("art-1")
    assert state == "REVIEWED"


@pytest.mark.asyncio
async def test_evaluate_for_promotion_returns_scores(pipeline):
    """evaluate_for_promotion returns score dict without state change."""
    result = await pipeline.evaluate_for_promotion("art-1")
    assert "faithfulness_score" in result
    assert "domain_coherence_score" in result
    assert "passes_threshold" in result
    assert "artifact_id" in result


@pytest.mark.asyncio
async def test_promotion_writes_vigil_health(pipeline):
    """Successful promotion writes a health check to VigilStore."""
    await pipeline.promote_to_canonical("art-1", "definer")
    assert len(pipeline.vigil_store.checks) >= 1
    check = pipeline.vigil_store.checks[-1]
    assert check["status"] == "healthy"
    assert check["canonical_count"] >= 1


def test_layering():
    """Orchestration component imports only Protocols."""
    from pathlib import Path
    pipeline_file = Path(__file__).parent.parent / "src/aip/orchestration/canonical_pipeline.py"
    if pipeline_file.exists():
        text = pipeline_file.read_text()
        # Must not import concrete adapter implementations directly
        assert "from aip.adapter.budget_store" not in text
        assert "from aip.adapter.vector" not in text
        # Should import from foundation.protocols
        assert "from aip.foundation.protocols" in text
