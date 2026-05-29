"""Verify Phase 5 schema additions do not break Phase 0, 1, 2, 3, or 4."""

from aip.foundation.protocols import (
    ArtifactStore,
    BudgetStore,
    EcsStore,
    EmbeddingProvider,
    EventStore,
    ModelProvider,
    ProjectStore,
    TraceStore,
    VectorStore,
)
from aip.foundation.schemas import (
    AcePlaybookEntry,
    BeastCadenceConfig,
    BudgetConfig,
    BudgetScope,
    Chunk,
    EcsState,
    EvaluationScore,
    FailureClassification,
    FailureType,
    PgvectorConfig,
    ReviewVerdict,
    RoutingWeight,
    SextonConfig,
    TrajectorySignal,
)


def test_sexton_config_dataclass():
    sc = SextonConfig(
        classification_batch_size=50,
        classification_interval_seconds=300,
        audit_on_slot_change=True,
        max_unclassified_before_alert=10,
    )
    assert sc.classification_batch_size == 50
    assert sc.audit_on_slot_change is True


def test_ace_playbook_entry_carries_model_gen_assumption():
    """Per §1.8: every ACE Playbook entry must carry model_gen_assumption."""
    entry = AcePlaybookEntry(
        entry_id="ace-001",
        domain="software_architecture",
        failure_type="A",
        intervention="Inject domain contract before synthesis",
        condition="domain == 'software_architecture' and output_schema_missing",
        model_gen_assumption="Models may adopt wrong domain role without explicit framing",
        confidence=0.85,
        created_at="2026-05-28T10:00:00Z",
    )
    assert entry.model_gen_assumption is not None
    assert entry.failure_type == "A"
    assert entry.deprecated_at is None


def test_ace_playbook_entry_deprecation():
    """Entries can be deprecated with reason."""
    entry = AcePlaybookEntry(
        entry_id="ace-002",
        domain="code_generation",
        failure_type="C",
        intervention="Validate JSON output structure",
        condition="output_format == 'json'",
        model_gen_assumption="Models may produce invalid JSON",
        deprecated_at="2026-06-01T10:00:00Z",
        deprecated_reason="Model slot upgrade improved JSON reliability",
    )
    assert entry.deprecated_at is not None
    assert "upgrade" in entry.deprecated_reason


def test_budget_config_dataclass():
    bc = BudgetConfig(
        session_token_limit=500000,
        project_token_limit=5000000,
        daily_token_limit=10000000,
        budget_warning_threshold=0.80,
        budget_hard_stop=True,
    )
    assert bc.session_token_limit == 500000
    assert bc.budget_hard_stop is True


def test_routing_weight_dataclass():
    rw = RoutingWeight(
        model_slot="synthesis",
        domain="software_architecture",
        weight=0.80,
        exploration_weight=0.10,
        sample_count=25,
    )
    assert rw.weight == 0.80
    assert rw.exploration_weight == 0.10


def test_beast_cadence_config_dataclass():
    bcc = BeastCadenceConfig(
        corpus_reindex_interval_seconds=3600,
        entity_maintenance_interval_seconds=1800,
        health_check_interval_seconds=60,
        max_reindex_batch_size=1000,
    )
    assert bcc.corpus_reindex_interval_seconds == 3600


def test_failure_classification_carries_model_gen_assumption():
    """Per §1.8: every Sexton classification must carry model_gen_assumption."""
    fc = FailureClassification(
        trace_event_id=42,
        failure_type="A",
        confidence=0.92,
        rationale="Output domain mismatch — synthesis adopted wrong role",
        model_slot_used="sexton",
        tokens_consumed=150,
        model_gen_assumption="Local Qwen3-Coder may misclassify domain-adjacent failures",
        classified_at="2026-05-28T10:00:00Z",
    )
    assert fc.model_gen_assumption is not None
    assert fc.failure_type == "A"


def test_budget_scope_type_alias():
    """BudgetScope must accept session, project, daily."""
    s: BudgetScope = "session"
    p: BudgetScope = "project"
    d: BudgetScope = "daily"
    assert s == "session"
    assert p == "project"
    assert d == "daily"


def test_phase0_phase1_phase2_phase3_phase4_enums_still_work():
    """Phase 0/1/2/3/4 enums must not be broken by Phase 5 additions."""
    assert EcsState.GENERATED is not None
    assert "C" in FailureType.__args__  # Literal still contains the expected values (matches Phase 2 test pattern)


def test_phase1_phase2_phase3_phase4_dataclasses_still_work():
    """Phase 1/2/3/4 dataclasses must not be broken by Phase 5 additions."""
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
    pc = PgvectorConfig(connection_string="postgresql://localhost/aip")
    assert pc.hnsw_m == 16
    es = EvaluationScore(
        dimension="faithfulness",
        score=0.85,
        model_gen_assumption="Models may miss subtle factual contradictions",
    )
    assert es.model_gen_assumption is not None


def test_budgetstore_protocol_has_methods():
    """Phase 5: BudgetStore must have get_budget, record_usage, check_limit."""
    assert hasattr(BudgetStore, "get_budget"), "BudgetStore missing get_budget"
    assert hasattr(BudgetStore, "record_usage"), "BudgetStore missing record_usage"
    assert hasattr(BudgetStore, "check_limit"), "BudgetStore missing check_limit"


def test_projectstore_protocol_has_list_projects():
    """Phase 5: ProjectStore must have list_projects method."""
    assert hasattr(ProjectStore, "list_projects"), "ProjectStore missing list_projects"


def test_existing_protocol_methods_preserved():
    """Phase 1/2/3/4 methods must still exist after Phase 5 amendments."""
    assert hasattr(VectorStore, "upsert"), "VectorStore missing upsert (Phase 1)"
    assert hasattr(VectorStore, "health_check"), "VectorStore missing health_check (Phase 4)"
    assert hasattr(VectorStore, "count"), "VectorStore missing count (Phase 4)"
    assert hasattr(EventStore, "write_event"), "EventStore missing write_event (Phase 1)"
    assert hasattr(EventStore, "query"), "EventStore missing query (Phase 2)"
    assert hasattr(ArtifactStore, "list_versions"), "ArtifactStore missing list_versions (Phase 2)"
    assert hasattr(EcsStore, "current_state"), "EcsStore missing current_state (Phase 2)"
    assert hasattr(TraceStore, "query_events"), "TraceStore missing query_events (Phase 3)"
    assert hasattr(ModelProvider, "call"), "ModelProvider missing call (Phase 3)"
    assert hasattr(EmbeddingProvider, "embed"), "EmbeddingProvider missing embed (Phase 3)"
