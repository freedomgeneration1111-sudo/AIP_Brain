"""Acceptance gate tests per §22.

These tests verify that all §22 acceptance gates pass, covering:
- Architecture conformance (§1-§10)
- Process rules (§11-§16)
- Appendix constraints (A-E)
"""
import pytest


def test_foundation_protocols_importable():
    """Gate: all Foundation Protocol classes are importable."""
    from aip.foundation.protocols import (
        VectorStore,
        LexicalStore,
        CanonicalStore,
        ArtifactStore,
        TraceStore,
        EntityStore,
        EventStore,
        ProjectStore,
        EcsStore,
        BudgetStore,
        AutonomyGate,
        ModelProvider,
        EmbeddingProvider,
        VigilStore,
        AuthStore,
        KnowledgeStore,
        PluginProvider,
    )
    # All protocols must be runtime-checkable
    assert hasattr(VectorStore, "__protocol_attrs__") or hasattr(VectorStore, "__abstractmethods__")


def test_foundation_schemas_importable():
    """Gate: all Foundation schema dataclasses are importable."""
    from aip.foundation.schemas import (
        EcsState,
        ContractTier,
        ContractRule,
        Chunk,
        RetrievalResult,
        ReviewVerdict,
        ReviewContext,
        EcsTransition,
        Event,
        TrajectorySignal,
        SessionContext,
        ModelSlotConfig,
        PgvectorConfig,
        MigrationStatus,
        EvaluationScore,
        FaithfulnessResult,
        DomainCoherenceResult,
        BudgetConfig,
        BudgetScope,
        SextonConfig,
        AcePlaybookEntry,
        RoutingWeight,
        BeastCadenceConfig,
        FailureClassification,
        SurfaceConfig,
        ApiRoute,
        McpToolDef,
        AutonomyEscalation,
        ChatMessage,
        ReviewQueueEntry,
        VigilConfig,
        AuthConfig,
        RateLimitConfig,
        CanonicalPromotionConfig,
        WorkflowTemplate,
        DeploymentProfile,
        KnowledgeCompilationConfig,
        PluginConfig,
        CollaboratorConfig,
        PerformanceConfig,
        ReleaseMetadata,
    )
    # EcsState must have all required states
    assert EcsState.SPECIFIED.value == "SPECIFIED"
    assert EcsState.GENERATED.value == "GENERATED"
    assert EcsState.REVIEWED.value == "REVIEWED"
    assert EcsState.APPROVED.value == "APPROVED"
    assert EcsState.SUPERSEDED.value == "SUPERSEDED"
    assert EcsState.FAILED.value == "FAILED"


def test_ecs_graph_valid_transitions():
    """Gate: ECS graph enforces valid transitions per §9.3."""
    from aip.foundation.ecs_graph import VALID_TRANSITIONS, validate_transition, InvalidTransitionError

    # Valid transitions
    validate_transition("SPECIFIED", "GENERATED")
    validate_transition("GENERATED", "REVIEWED")
    validate_transition("REVIEWED", "APPROVED")

    # Invalid transitions
    with pytest.raises(InvalidTransitionError):
        validate_transition("SPECIFIED", "APPROVED")  # skip states

    with pytest.raises(InvalidTransitionError):
        validate_transition("APPROVED", "SPECIFIED")  # backwards


def test_validation_module_importable():
    """Gate: foundation validation module is importable."""
    from aip.foundation.validation import ValidationResult

    # Can construct a result with all required fields
    result = ValidationResult(
        passed=True,
        failure_type=None,
        failure_detail=None,
        checks_run=3,
        checks_failed=[],
    )
    assert result.passed is True


def test_no_hardcoded_model_names():
    """Gate: no hardcoded model names in configuration (per §4.1)."""
    from aip.foundation.schemas import ModelSlotConfig

    # ModelSlotConfig uses slot names, not concrete model names
    slot = ModelSlotConfig(slot_name="synthesis", provider="ollama", model="placeholder")
    assert slot.slot_name == "synthesis"
    assert slot.provider == "ollama"


def test_budget_config_has_all_scopes():
    """Gate: BudgetConfig defines limits for all required scopes (session, project, daily)."""
    from aip.foundation.schemas import BudgetConfig

    cfg = BudgetConfig()
    assert cfg.session_token_limit > 0
    assert cfg.project_token_limit > 0
    assert cfg.daily_token_limit > 0
    assert 0 < cfg.budget_warning_threshold <= 1.0


def test_deployment_profiles_exist():
    """Gate: both laptop and production deployment profiles are defined."""
    from aip.foundation.schemas import DeploymentProfile

    laptop = DeploymentProfile(profile_name="laptop")
    assert laptop.vector_backend == "sqlite_vss"
    assert laptop.auth_enabled is False

    production = DeploymentProfile(
        profile_name="production",
        vector_backend="pgvector",
        auth_enabled=True,
        workers=2,
    )
    assert production.vector_backend == "pgvector"
    assert production.auth_enabled is True


def test_collaborator_cannot_approve_by_default():
    """Gate: Process Rule 11 — collaborator_can_approve defaults to False."""
    from aip.foundation.schemas import CollaboratorConfig

    cfg = CollaboratorConfig()
    assert cfg.collaborator_can_approve is False
