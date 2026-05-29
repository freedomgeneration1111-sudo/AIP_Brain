"""Verify Phase 4 schema additions do not break Phase 0, 1, 2, or 3."""

from aip.foundation.protocols import (
    ArtifactStore,
    EcsStore,
    EmbeddingProvider,
    EventStore,
    ModelProvider,
    TraceStore,
    VectorStore,
)
from aip.foundation.schemas import (
    Chunk,
    DomainCoherenceResult,
    EcsState,
    EvaluationScore,
    FailureType,
    FaithfulnessResult,
    MigrationCheckpoint,
    MigrationStatus,
    PgvectorConfig,
    ReviewVerdict,
    TrajectorySignal,
    VectorBackendType,
)


def test_pgvector_config_dataclass():
    cfg = PgvectorConfig(
        connection_string="postgresql://localhost/aip_vectors",
        pool_min_size=2,
        pool_max_size=10,
        hnsw_m=16,
        hnsw_ef_construction=64,
        hnsw_ef_search=40,
    )
    assert cfg.connection_string == "postgresql://localhost/aip_vectors"
    assert cfg.hnsw_m == 16


def test_migration_status_dataclass():
    ms = MigrationStatus(
        source_backend="sqlite_vss",
        target_backend="pgvector",
        total_vectors=1000,
        migrated_vectors=750,
        started_at="2026-05-28T10:00:00Z",
    )
    assert ms.migrated_vectors == 750
    assert ms.completed_at is None


def test_migration_checkpoint_dataclass():
    mc = MigrationCheckpoint(
        checkpoint_id="ckpt-001",
        source_backend="sqlite_vss",
        target_backend="pgvector",
        last_migrated_id=750,
        total_migrated=750,
    )
    assert mc.last_migrated_id == 750


def test_evaluation_score_carries_model_gen_assumption():
    """Per §1.8: every model-based evaluation must carry model_gen_assumption."""
    es = EvaluationScore(
        dimension="faithfulness",
        score=0.85,
        model_slot_used="evaluation",
        tokens_consumed=150,
        model_gen_assumption="Models may miss subtle factual contradictions",
    )
    assert es.model_gen_assumption is not None
    assert es.score == 0.85


def test_faithfulness_result_dataclass():
    fr = FaithfulnessResult(
        artifact_id="a1",
        faithfulness_score=0.82,
        context_coverage=0.75,
        hallucination_flags=["Claim about X not in context"],
    )
    assert fr.hallucination_flags == ["Claim about X not in context"]


def test_domain_coherence_result_dataclass():
    dcr = DomainCoherenceResult(
        artifact_id="a1",
        coherence_score=0.90,
        domain="software_architecture",
        violations=["Missing required section: Error Handling"],
    )
    assert dcr.violations == ["Missing required section: Error Handling"]


def test_vector_backend_type_alias():
    """VectorBackendType must accept only 'pgvector' or 'sqlite_vss'."""
    # These should be valid at the type level (mypy enforces)
    pg: VectorBackendType = "pgvector"
    sq: VectorBackendType = "sqlite_vss"
    assert pg == "pgvector"
    assert sq == "sqlite_vss"


def test_phase0_phase1_phase2_phase3_enums_still_work():
    """Phase 0/1/2/3 enums must not be broken by Phase 4 additions."""
    assert EcsState.GENERATED is not None
    assert "C" in FailureType.__args__  # Literal still contains the expected values


def test_phase1_phase2_phase3_dataclasses_still_work():
    """Phase 1/2/3 dataclasses must not be broken by Phase 4 additions."""
    c = Chunk(id="x", content="hello", score=0.9, metadata={"k": "v"}, domain="test")
    assert c.id == "x"
    v = ReviewVerdict(artifact_id="a1", verdict="APPROVED", reviewer="definer")
    assert v.verdict == "APPROVED"
    s = TrajectorySignal(
        signal_type="loop",
        session_id="s1",
        failure_type="D",
        confidence=0.85,
        detail="Repeated pattern",
        detected_at="2026-05-28T10:00:00Z",
    )
    assert s.signal_type == "loop"


def test_vectorstore_protocol_has_health_check():
    """Phase 4: VectorStore must have health_check method."""
    assert hasattr(VectorStore, "health_check"), "VectorStore missing health_check method"


def test_vectorstore_protocol_has_count():
    """Phase 4: VectorStore must have count method."""
    assert hasattr(VectorStore, "count"), "VectorStore missing count method"


def test_existing_protocol_methods_preserved():
    """Phase 1/2/3 methods must still exist after Phase 4 amendments."""
    assert hasattr(VectorStore, "upsert"), "VectorStore missing upsert (Phase 1)"
    assert hasattr(EventStore, "write_event"), "EventStore missing write_event (Phase 1)"
    assert hasattr(EventStore, "query"), "EventStore missing query (Phase 2)"
    assert hasattr(ArtifactStore, "list_versions"), "ArtifactStore missing list_versions (Phase 2)"
    assert hasattr(EcsStore, "current_state"), "EcsStore missing current_state (Phase 2)"
    assert hasattr(TraceStore, "query_events"), "TraceStore missing query_events (Phase 3)"
    assert hasattr(ModelProvider, "call"), "ModelProvider missing call (Phase 3)"
    assert hasattr(EmbeddingProvider, "embed"), "EmbeddingProvider missing embed (Phase 3)"
