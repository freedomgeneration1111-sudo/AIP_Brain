"""Schema contracts for knowledge compilation, plugin management, collaborator access, and performance configuration."""

from typing import get_args

from aip.foundation.protocols import (
    AuthStore,
    # Prior
    CanonicalStore,
    KnowledgeStore,
    PluginProvider,
)

# Prior (must still work — non-breaking)
from aip.foundation.schemas import (
    AuthConfig,
    Chunk,
    CollaboratorConfig,
    CollaboratorRole,
    CompilationState,
    EcsState,
    FailureType,
    KnowledgeCompilationConfig,
    PerformanceConfig,
    PluginConfig,
    PluginStatus,
    ReleaseMetadata,
    VigilConfig,
)


def test_knowledge_plugin_collaborator_configs():
    """New dataclasses instantiate with required fields + defaults."""
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
    assert perf.max_memory_mb == 4096  # laptop-viable
    assert perf.sqlite_wal_mode is True
    assert perf.vector_query_limit == 50

    rm = ReleaseMetadata()
    assert rm.release_version == "0.1.0"
    assert rm.architecture_revision == "5.2"


def test_configs_carry_model_gen_assumption():
    """KnowledgeCompilationConfig, PluginConfig carry model_gen_assumption per §1.8."""
    kcc = KnowledgeCompilationConfig(model_gen_assumption="test assumption")
    assert kcc.model_gen_assumption == "test assumption"

    pc = PluginConfig(model_gen_assumption="plugin extensibility")
    assert pc.model_gen_assumption == "plugin extensibility"


def test_type_aliases():
    """Type aliases exist and have expected members."""
    assert "COMPILED" in get_args(CompilationState)
    assert "APPROVED" in get_args(CompilationState)
    assert "loaded" in get_args(PluginStatus)
    assert "definer" in get_args(CollaboratorRole)
    assert "collaborator" in get_args(CollaboratorRole)
    assert "readonly" in get_args(CollaboratorRole)


def test_knowledge_store_and_plugin_provider_protocols():
    """KnowledgeStore and PluginProvider Protocols have all declared methods."""
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


def test_authstore_collaborator_methods():
    """AuthStore has the 4 collaborator management methods."""
    assert hasattr(AuthStore, "list_users")
    assert hasattr(AuthStore, "create_user")
    assert hasattr(AuthStore, "update_user_role")
    assert hasattr(AuthStore, "revoke_user")


def test_prior_types_compat():
    """All prior schema enums and dataclasses are not broken."""
    assert EcsState.GENERATED is not None
    assert "C" in get_args(FailureType)
    c = Chunk(id="x", content="hello", score=0.9, metadata={}, domain="test")
    assert c.id == "x"
    # VigilConfig still importable and usable
    vc = VigilConfig(canonical_health_check_interval_seconds=3600)
    assert vc.canonical_health_check_interval_seconds == 3600
    ac = AuthConfig()
    assert ac.api_key_enabled is True
    # CollaboratorRole is new but extends the concept cleanly
    assert "definer" in get_args(CollaboratorRole)


def test_collaborator_cannot_approve_by_default():
    """collaborator_can_approve defaults to False — DEFINER sovereignty."""
    cc = CollaboratorConfig()
    assert cc.collaborator_can_approve is False


def test_knowledge_store_is_distinct_protocol():
    """KnowledgeStore is distinct peer (no collapse with CanonicalStore)."""
    assert KnowledgeStore is not CanonicalStore
    # Methods differ in purpose (provenance vs artifact versioning)
    assert hasattr(KnowledgeStore, "get_provenance")
    assert not hasattr(CanonicalStore, "get_provenance")  # or different semantics


# Additional smoke: import of types via the aip. path works (File Layout compliance)
def test_aip_prefixed_imports_work():
    """aip. imports succeed per File Layout & Import Conventions."""
    # Already exercised by the module-level imports above
    assert KnowledgeCompilationConfig is not None
    assert KnowledgeStore is not None
