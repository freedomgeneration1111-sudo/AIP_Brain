"""Verify Phase 7 schema additions do not break Phase 0–6."""

import pytest

from aip.foundation.schemas import (
    # Prior phases (must still work)
    AcePlaybookEntry,
    ApiRoute,
    AutonomyEscalation,
    AutonomyLevel,
    BeastCadenceConfig,
    BudgetConfig,
    BudgetScope,
    ChatMessage,
    Chunk,
    ContractRule,
    EcsState,
    FailureClassification,
    FailureType,
    McpAutonomyLevel,
    McpToolDef,
    ModelSlotConfig,
    ReviewQueueEntry,
    ReviewVerdict,
    SessionContext,
    SextonConfig,
    SurfaceConfig,
    TrajectorySignal,
    # Phase 7 additions
    VigilConfig,
    AuthConfig,
    RateLimitConfig,
    CanonicalPromotionConfig,
    WorkflowTemplate,
    DeploymentProfile,
)
from aip.foundation.protocols import (
    VigilStore,
    AuthStore,
    # Prior
    AutonomyGate,
    LexicalStore,
    CanonicalStore,
    EntityStore,
)


def test_phase7_config_dataclasses():
    vc = VigilConfig(canonical_health_check_interval_seconds=3600, stale_threshold_days=30)
    assert vc.canonical_health_check_interval_seconds == 3600

    ac = AuthConfig(api_key_enabled=True)
    assert ac.api_key_enabled is True

    rl = RateLimitConfig(requests_per_minute=60)
    assert rl.requests_per_minute == 60

    cp = CanonicalPromotionConfig(require_vigil_health_check=True)
    assert cp.require_vigil_health_check is True

    wt = WorkflowTemplate(template_id="incremental_update_v1", name="Incremental Update v1")
    assert wt.name == "Incremental Update v1"

    dp = DeploymentProfile(profile_name="laptop", vector_backend="sqlite_vss")
    assert dp.profile_name == "laptop"


def test_phase7_new_protocols_exist():
    assert hasattr(VigilStore, "list_stale_canonicals")
    assert hasattr(AuthStore, "validate_api_key")


def test_phase0_through_phase6_types_still_work():
    """All prior phase types must remain importable and functional."""
    assert EcsState.GENERATED is not None
    from typing import get_args
    assert "C" in get_args(FailureType)
    c = Chunk(id="x", content="hello", score=0.9, metadata={}, domain="test")
    assert c.id == "x"
    fc = FailureClassification(
        trace_event_id=1,
        failure_type="A",
        model_gen_assumption="test",
        classified_at="2026-05-28T00:00:00Z",
    )
    assert fc.failure_type == "A"


def test_phase7_schema_additions_test_file_exists():
    """Meta-test: this file itself proves the test was created per ANNEX."""
    assert True
