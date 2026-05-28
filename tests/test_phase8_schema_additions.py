"""Verify Phase 8 schema additions (CHUNK-10.0a) do not break Phase 0–7.
Exact per AIP_0_1_Phase8_BuildSpec_Rev1.0.md ANNEX + prose gate expectations.
File Layout: imports use aip. prefix per delta'd spec note.
"""

import pytest
from typing import get_args

# Prior phases (must still work — non-breaking)
from aip.foundation.schemas import (
    # Phase 0-6 samples
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
    # Phase 7 additions (still work)
    VigilConfig,
    AuthConfig,
    RateLimitConfig,
    CanonicalPromotionConfig,
    WorkflowTemplate,
    DeploymentProfile,
    # Phase 8 additions
    KnowledgeCompilationConfig,
    PluginConfig,
    CollaboratorConfig,
    PerformanceConfig,
    ReleaseMetadata,
    CompilationState,
    PluginStatus,
    CollaboratorRole,
)

from aip.foundation.protocols import (
    # Phase 7
    VigilStore,
    AuthStore,
    # Prior
    AutonomyGate,
    LexicalStore,
    CanonicalStore,
    EntityStore,
    # Phase 8 new Protocols
    KnowledgeStore,
    PluginProvider,
)


def test_phase8_config_dataclasses():
    """(a)(c)(d)(e)(f) New dataclasses instantiate with required fields + defaults."""
    kcc = KnowledgeCompilationConfig()
    assert kcc.compilation_model_slot == "synthesis"
    assert kcc.evaluation_model_slot == "evaluation"
    assert kcc.max_source_canonicals == 10
    assert kcc.compilation_confidence_threshold == 0.60
    assert kcc.auto_index_on_approval is True

    pc = PluginConfig()
    assert pc.plugins_dir == "plugins"
    assert pc.enabled is True
    assert pc.sandbox_mode is True

    cc = CollaboratorConfig()
    assert cc.enabled is False
    assert cc.max_collaborators == 5
    assert cc.collaborator_can_approve is False  # critical §1.7 default
    assert cc.readonly_can_search is True

    perf = PerformanceConfig()
    assert perf.max_memory_mb == 4096  # §2.1 laptop-viable
    assert perf.sqlite_wal_mode is True
    assert perf.vector_query_limit == 50

    rm = ReleaseMetadata()
    assert rm.release_version == "0.1.0"
    assert rm.architecture_revision == "5.2"


def test_phase8_configs_carry_model_gen_assumption():
    """(b) KnowledgeCompilationConfig, PluginConfig carry model_gen_assumption per §1.8."""
    kcc = KnowledgeCompilationConfig(model_gen_assumption="test assumption")
    assert kcc.model_gen_assumption == "test assumption"

    pc = PluginConfig(model_gen_assumption="plugin extensibility")
    assert pc.model_gen_assumption == "plugin extensibility"


def test_phase8_type_aliases():
    """Type aliases exist and have expected members."""
    assert "COMPILED" in get_args(CompilationState)
    assert "APPROVED" in get_args(CompilationState)
    assert "loaded" in get_args(PluginStatus)
    assert "definer" in get_args(CollaboratorRole)
    assert "collaborator" in get_args(CollaboratorRole)
    assert "readonly" in get_args(CollaboratorRole)


def test_phase8_new_protocols_exist():
    """(g)(h) KnowledgeStore and PluginProvider Protocols have all declared methods."""
    # KnowledgeStore (6 methods)
    assert hasattr(KnowledgeStore, "store_compiled")
    assert hasattr(KnowledgeStore, "get_compiled")
    assert hasattr(KnowledgeStore, "list_compiled")
    assert hasattr(KnowledgeStore, "update_state")
    assert hasattr(KnowledgeStore, "get_provenance")
    assert hasattr(KnowledgeStore, "search_compiled")

    # PluginProvider (4 methods)
    assert hasattr(PluginProvider, "call_model")
    assert hasattr(PluginProvider, "health_check")
    assert hasattr(PluginProvider, "get_slot_name")
    assert hasattr(PluginProvider, "get_provider_name")


def test_phase8_authstore_amendments_exist():
    """(i) AuthStore has the 4 new collaborator management methods."""
    assert hasattr(AuthStore, "list_users")
    assert hasattr(AuthStore, "create_user")
    assert hasattr(AuthStore, "update_user_role")
    assert hasattr(AuthStore, "revoke_user")


def test_phase0_through_phase7_types_still_work():
    """(j) All prior Phase 0–7 schema enums and dataclasses are not broken."""
    assert EcsState.GENERATED is not None
    assert "C" in get_args(FailureType)
    c = Chunk(id="x", content="hello", score=0.9, metadata={}, domain="test")
    assert c.id == "x"
    # Phase 7 still importable and usable
    vc = VigilConfig(canonical_health_check_interval_seconds=3600)
    assert vc.canonical_health_check_interval_seconds == 3600
    ac = AuthConfig()
    assert ac.api_key_enabled is True
    # CollaboratorRole is new but extends the concept cleanly
    assert "definer" in get_args(CollaboratorRole)


def test_collaborator_can_approve_default_is_false():
    """(k + §1.7) collaborator_can_approve defaults to False — DEFINER sovereignty."""
    cc = CollaboratorConfig()
    assert cc.collaborator_can_approve is False


def test_knowledge_store_is_distinct_protocol():
    """Appendix D / Process Rule 12: KnowledgeStore is distinct peer (no collapse with CanonicalStore)."""
    assert KnowledgeStore is not CanonicalStore
    # Methods differ in purpose (provenance vs artifact versioning)
    assert hasattr(KnowledgeStore, "get_provenance")
    assert not hasattr(CanonicalStore, "get_provenance")  # or different semantics


# Additional smoke: import of Phase 8 types via the aip. path works (File Layout compliance)
def test_aip_prefixed_imports_work():
    """Per File Layout & Import Conventions note in spec: aip. imports succeed."""
    # Already exercised by the module-level imports above
    assert KnowledgeCompilationConfig is not None
    assert KnowledgeStore is not None
